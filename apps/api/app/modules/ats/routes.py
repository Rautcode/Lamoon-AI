"""ATS endpoints — the two front doors and the pipeline surface (ARCH §5).
Email intake front door is deferred (marked)."""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ai.provider import AIProvider, get_ai_provider
from app.core.db import get_db
from app.core.notify.base import Notifier, get_notifier
from app.core.rbac import require
from app.core.storage.base import checksum, get_blob_store
from app.core.tenant import resolve_tenant
from app.modules.ats import extract, interview_service, pipeline
from app.modules.ats.models import AIAnalysis, Application, Candidate, JobOpening
from app.modules.ats.notify_helpers import hr_recipient
from app.modules.ats.schemas import ApplicationOut, JobIn, ProposeSlotsIn, ProposeSlotsOut
from app.modules.audit import service as audit
from app.modules.auth.models import Company

# ponytail: add dependencies=[Depends(require_module("ats"))] once entitlements are seeded.
router = APIRouter(prefix="/ats", tags=["ats"])

# SECURITY: these routes were authenticated but NOT permission-gated until ESS
# landed. That was survivable while every login was admin/hr/manager; the
# moment employees got logins it meant any employee could read the entire
# candidate pipeline — names, emails, AI scores, resume summaries. Caught by
# an adversarial ESS test (test_employee_cannot_reach_hiring_or_lumo_tools).
CAN_READ = [Depends(require("ats.read"))]
CAN_WRITE = [Depends(require("ats.write"))]


@router.post("/jobs", dependencies=CAN_WRITE)
def create_job(body: JobIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    job = JobOpening(company_id=uuid.UUID(cid), **body.model_dump())
    db.add(job)
    db.flush()
    return {"id": job.id, "title": job.title, "status": job.status}


@router.get("/jobs", dependencies=CAN_READ)
def list_jobs(db: Session = Depends(get_db)):
    rows = db.scalars(select(JobOpening).where(JobOpening.deleted_at.is_(None))).all()
    return [{"id": j.id, "title": j.title, "status": j.status} for j in rows]


# ponytail: JWT-gated, so this is an internal/service-account intake, not a
# public careers page. A genuinely public front door belongs behind the
# /public prefix with its own token, like interview booking.
@router.post("/apply", status_code=202, dependencies=CAN_WRITE)
async def apply(
    file: UploadFile = File(...),
    job_id: uuid.UUID | None = Form(None),
    email: str | None = Form(None),
    full_name: str | None = Form(None),
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
    notifier: Notifier = Depends(get_notifier),
):
    """Webhook front door. Idempotent by resume SHA-256: same resume dedups to
    one candidate instead of re-uploading (the cost/dedup lever)."""
    data = await file.read()
    sha = checksum(data)
    company_id = uuid.UUID(cid)

    candidate = db.scalar(select(Candidate).where(Candidate.resume_sha256 == sha))
    is_new_candidate = candidate is None
    if candidate is None:
        blob = get_blob_store()
        key = f"{cid}/{sha}.pdf"
        url = await blob.put(key, data, content_type="application/pdf")
        text = extract.extract_text(data)
        candidate = Candidate(
            company_id=company_id,
            email=email,
            full_name=full_name,
            resume_blob_key=key,
            resume_url=url,
            resume_sha256=sha,
            extracted_text=text,
        )
        db.add(candidate)
        db.flush()

    job = db.get(JobOpening, job_id) if job_id else None
    app = Application(
        company_id=company_id,
        candidate_id=candidate.id,
        job_opening_id=job_id,
        source="webhook",
        status="received",
    )
    db.add(app)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="application", entity_id=app.id,
        action="received", source="webhook",
    )

    company = db.get(Company, company_id)
    company_name = company.name if company else "the company"
    job_title = job.title if job else "the role"

    # Bad resume: extraction produced nothing → HR can't screen it. Alert HR,
    # mark for manual review, skip the candidate email (nothing to confirm yet).
    if is_new_candidate and not (candidate.extracted_text or "").strip():
        app.status = "needs_review"
        hr = hr_recipient(db, company_id)
        if hr:
            await notifier.send(
                to=hr, template="hr_alert",
                ctx={
                    "subject": (
                        f"Resume could not be processed — "
                        f"{candidate.full_name or email or sha[:8]}"
                    ),
                    "body": (
                        f"Application {app.id} for {job_title} has no extractable text.\n"
                        f"Resume: {candidate.resume_url}\nCandidate email: {email or 'unknown'}"
                    ),
                },
            )
    elif email:
        await notifier.send(
            to=email, template="application_received",
            ctx={
                "candidate_name": full_name or "there",
                "job_title": job_title, "company_name": company_name,
            },
        )

    # ponytail: production enqueues screen_application on the Celery `high` queue here.
    return {"application_id": app.id, "candidate_id": candidate.id, "status": app.status}


def _enrich(db: Session, apps: list[Application]) -> list[ApplicationOut]:
    """Attach candidate name + latest AI score to each application in 2 queries
    total, rather than 2 per row. The pipeline board renders every open
    application at once, so the N+1 version is the difference between one
    round trip and a hundred."""
    if not apps:
        return []
    cand_ids = {a.candidate_id for a in apps}
    app_ids = [a.id for a in apps]

    candidates = {
        c.id: c for c in db.scalars(select(Candidate).where(Candidate.id.in_(cand_ids))).all()
    }
    # One analysis per application in practice; last-wins if a re-screen added
    # another, which matches "show the current score".
    analyses: dict[uuid.UUID, AIAnalysis] = {}
    for an in db.scalars(
        select(AIAnalysis)
        .where(AIAnalysis.application_id.in_(app_ids))
        .order_by(AIAnalysis.created_at)
    ).all():
        analyses[an.application_id] = an

    out = []
    for a in apps:
        cand = candidates.get(a.candidate_id)
        score = analyses.get(a.id)
        out.append(
            ApplicationOut(
                id=a.id, status=a.status, tier=a.tier,
                recommended_action=a.recommended_action,
                candidate_id=a.candidate_id, job_opening_id=a.job_opening_id,
                candidate_name=cand.full_name if cand else None,
                candidate_email=cand.email if cand else None,
                final_score=score.final_score if score else None,
                summary=score.summary if score else None,
            )
        )
    return out


@router.get("/applications", response_model=list[ApplicationOut], dependencies=CAN_READ)
def list_applications(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Application).where(Application.deleted_at.is_(None))).all())
    return _enrich(db, rows)


@router.get(
    "/applications/{application_id}", response_model=ApplicationOut, dependencies=CAN_READ
)
def get_application(application_id: uuid.UUID, db: Session = Depends(get_db)):
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(404, "not found")
    return _enrich(db, [app])[0]


@router.post(
    "/applications/{application_id}/screen", response_model=ApplicationOut,
    dependencies=CAN_WRITE,
)
async def screen(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
    notifier: Notifier = Depends(get_notifier),
    cid: str = Depends(resolve_tenant),
):
    try:
        app = await pipeline.screen_application(db, application_id, provider, notifier)
    except ValueError:
        raise HTTPException(404, "not found") from None
    return app


@router.post(
    "/applications/{application_id}/interview-slots", response_model=ProposeSlotsOut,
    dependencies=CAN_WRITE,
)
async def propose_interview_slots(
    application_id: uuid.UUID,
    body: ProposeSlotsIn,
    db: Session = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
):
    """HR offers interview times; the candidate gets a booking link (no login
    needed — see /public/interviews/{token})."""
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(404, "not found")
    if not body.slots:
        raise HTTPException(422, "at least one slot required")
    link, slots = await interview_service.propose_slots(db, app, body, notifier)
    return ProposeSlotsOut(booking_token=link.token, slots=slots)
