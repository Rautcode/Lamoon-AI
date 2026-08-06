"""ATS endpoints — the two front doors and the pipeline surface (ARCH §5).
Email intake front door + interview scheduling are deferred (marked)."""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ai.provider import AIProvider, get_ai_provider
from app.core.db import get_db
from app.core.storage.base import checksum, get_blob_store
from app.core.tenant import resolve_tenant
from app.modules.ats import extract, pipeline
from app.modules.ats.models import Application, Candidate, JobOpening
from app.modules.ats.schemas import ApplicationOut, JobIn
from app.modules.audit import service as audit

# ponytail: add dependencies=[Depends(require_module("ats"))] once entitlements are seeded.
router = APIRouter(prefix="/ats", tags=["ats"])


@router.post("/jobs")
def create_job(body: JobIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    job = JobOpening(company_id=uuid.UUID(cid), **body.model_dump())
    db.add(job)
    db.flush()
    return {"id": job.id, "title": job.title, "status": job.status}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    rows = db.scalars(select(JobOpening).where(JobOpening.deleted_at.is_(None))).all()
    return [{"id": j.id, "title": j.title, "status": j.status} for j in rows]


@router.post("/apply", status_code=202)
async def apply(
    file: UploadFile = File(...),
    job_id: uuid.UUID | None = Form(None),
    email: str | None = Form(None),
    full_name: str | None = Form(None),
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    """Webhook front door. Idempotent by resume SHA-256: same resume dedups to
    one candidate instead of re-uploading (the cost/dedup lever)."""
    data = await file.read()
    sha = checksum(data)
    company_id = uuid.UUID(cid)

    candidate = db.scalar(select(Candidate).where(Candidate.resume_sha256 == sha))
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
    # ponytail: production enqueues screen_application on the Celery `high` queue here.
    return {"application_id": app.id, "candidate_id": candidate.id, "status": app.status}


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(db: Session = Depends(get_db)):
    rows = db.scalars(select(Application).where(Application.deleted_at.is_(None))).all()
    return rows


@router.get("/applications/{application_id}", response_model=ApplicationOut)
def get_application(application_id: uuid.UUID, db: Session = Depends(get_db)):
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(404, "not found")
    return app


@router.post("/applications/{application_id}/screen", response_model=ApplicationOut)
async def screen(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
    cid: str = Depends(resolve_tenant),
):
    try:
        app = await pipeline.screen_application(db, application_id, provider)
    except ValueError:
        raise HTTPException(404, "not found") from None
    return app
