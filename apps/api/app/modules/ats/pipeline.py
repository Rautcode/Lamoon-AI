"""Resume screening pipeline — the flagship flow.

    extracted_text → [cache?] → AIProvider.analyze → job_match → score → tier
        → persist → notify (spec's automatic actions)

The cost lever (ADR-0005): the AI screening is resume-only, so it's cached by
(resume_sha256 + recipe_hash). Same resume + same recipe = zero API calls; only
the cheap job-match/score/tier is recomputed per application.

Automatic actions per tier (spec): A/B → HR + candidate notified immediately,
status="shortlisted". C/D → status="pending_reject"; the actual rejection email
is sent by the scheduled auto-reject job after a 10-day grace window (see
tasks.py), not here — that's Workflow 2, not the screening workflow.

ponytail: runs inline (awaited) for the V1 slice. Production enqueues this on
the Celery `high` queue from /apply — the thin task wrapper is a later add.
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.ai.prompts import Recipe
from app.core.ai.prompts import get as get_prompt
from app.core.ai.provider import (
    OUTPUT_SCHEMA_VERSION,
    TEMPERATURE,
    AIProvider,
)
from app.core.config import get_settings
from app.core.notify.base import Notifier
from app.modules.ats import scoring
from app.modules.ats.models import AIAnalysis, Application, Candidate, JobOpening
from app.modules.ats.notify_helpers import hr_recipient
from app.modules.ats.schemas import Screening
from app.modules.audit import service as audit

PROMPT_KEY, PROMPT_VER = "resume_screen", "v1"
SHORTLIST_TIERS = ("A", "B")


def _recipe_hash() -> str:
    prompt = get_prompt(PROMPT_KEY, PROMPT_VER)
    model = get_settings().ai_default_model
    return Recipe(PROMPT_KEY, PROMPT_VER, OUTPUT_SCHEMA_VERSION, model, TEMPERATURE).hash(prompt)


async def screen_application(
    db: Session, application_id: uuid.UUID, provider: AIProvider, notifier: Notifier
) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise ValueError("application not found")
    cand = db.get(Candidate, app.candidate_id)
    if cand is None:
        raise ValueError("candidate not found")  # FK guarantees this, but guard anyway
    job = db.get(JobOpening, app.job_opening_id) if app.job_opening_id else None
    recipe_hash = _recipe_hash()

    # --- cache: reuse the resume-only AI screening if we've seen this resume+recipe ---
    cached = (
        db.query(AIAnalysis)
        .filter(
            AIAnalysis.resume_sha256 == cand.resume_sha256,
            AIAnalysis.recipe_hash == recipe_hash,
        )
        .first()
    )
    if cached:
        s = Screening(**cached.extracted)
        model, cache_hit = cached.model, True
    else:
        result = await provider.analyze(
            prompt_key=PROMPT_KEY,
            prompt_ver=PROMPT_VER,
            inputs={"resume_text": cand.extracted_text or "", "job": _job_dict(job)},
            output_schema=Screening,
            cache_key=f"{cand.resume_sha256}:{recipe_hash}",
        )
        s = result.output
        model, cache_hit = result.model, False

    # --- job match + final verdict (cheap, per-application, always recomputed) ---
    m = scoring.job_match(
        s.skills,
        job.required_skills if job else [],
        job.preferred_skills if job else [],
        job.min_experience_years if job else 0,
        s.years_experience,
    )
    final = scoring.final_score(s.overall_ai_score, m.match_pct)
    tier = scoring.tier_of(final)

    db.add(
        AIAnalysis(
            company_id=app.company_id,
            application_id=app.id,
            resume_sha256=cand.resume_sha256,
            recipe_hash=recipe_hash,
            extracted=s.model_dump(),
            technical_score=s.technical_score,
            experience_score=s.experience_score,
            education_score=s.education_score,
            communication_score=s.communication_score,
            overall_ai_score=s.overall_ai_score,
            job_match_pct=m.match_pct,
            final_score=final,
            matched_skills=m.matched_skills,
            missing_skills=m.missing_skills,
            summary=s.summary,
            model=model,
            prompt_version=PROMPT_VER,
        )
    )
    app.tier = tier
    app.recommended_action = scoring.recommended_action(tier)
    app.screened_at = datetime.now(UTC)
    app.status = "shortlisted" if tier in SHORTLIST_TIERS else "pending_reject"

    await _notify_screened(db, app, cand, job, tier, final, notifier)

    audit.record(
        db,
        company_id=app.company_id,
        entity="application",
        entity_id=app.id,
        action="screened",
        source="ai",
        payload={"tier": tier, "final_score": final, "cache_hit": cache_hit},
    )
    db.flush()
    return app


async def _notify_screened(
    db: Session, app: Application, cand: Candidate, job: JobOpening | None,
    tier: str, final: float, notifier: Notifier,
) -> None:
    """Spec's automatic actions: A/B → HR heads-up now (candidate gets the
    scheduling link once HR proposes slots — see interview_service.py; no
    slots exist yet at screening time, so there's nothing to send them).
    C/D → no email yet; the scheduled job rejects after the grace window."""
    job_title = job.title if job else "the role"
    hr = hr_recipient(db, app.company_id)

    if tier in SHORTLIST_TIERS and hr:
        await notifier.send(
            to=hr, template="hr_alert",
            ctx={
                "subject": f"Tier {tier} candidate — {job_title}: {cand.full_name or cand.email}",
                "body": (
                    f"Application {app.id} scored {final}/10 (Tier {tier}). "
                    f"Propose interview slots: POST /ats/applications/{app.id}/interview-slots"
                ),
            },
        )


def _job_dict(job: JobOpening | None) -> dict:
    if not job:
        return {}
    return {
        "title": job.title,
        "required_skills": job.required_skills,
        "preferred_skills": job.preferred_skills,
        "min_experience_years": job.min_experience_years,
    }
