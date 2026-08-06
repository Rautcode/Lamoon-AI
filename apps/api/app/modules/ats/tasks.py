"""Scheduled ATS workflows (spec: Workflow 2 — Daily Auto Reject; Workflow 4 —
Interview Reminder). Both run once per tenant (RLS needs the GUC set per
company), fanned out from the companies table — the one table not under RLS.
"""
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import open_session
from app.core.notify.base import Notifier, get_notifier
from app.modules.ats.models import Application, Candidate, Interview, JobOpening
from app.modules.audit import service as audit
from app.modules.auth.models import Company

AUTO_REJECT_DAYS = 10
REMINDER_HOURS_BEFORE = 24


async def auto_reject_stale_for_company(
    db: Session, company_id: uuid.UUID, notifier: Notifier, *, cutoff_days: int = AUTO_REJECT_DAYS
) -> int:
    """Reject Tier C/D applications screened more than `cutoff_days` ago. Runs
    inside a session already scoped to `company_id` (RLS GUC set by caller)."""
    cutoff = datetime.now(UTC) - timedelta(days=cutoff_days)
    stale = db.scalars(
        select(Application).where(
            Application.status == "pending_reject",
            Application.screened_at.is_not(None),
            Application.screened_at <= cutoff,
            Application.deleted_at.is_(None),
        )
    ).all()

    company = db.get(Company, company_id)
    company_name = company.name if company else "the company"

    for app in stale:
        cand = db.get(Candidate, app.candidate_id)
        job = db.get(JobOpening, app.job_opening_id) if app.job_opening_id else None
        if cand and cand.email:
            await notifier.send(
                to=cand.email, template="rejection",
                ctx={
                    "candidate_name": cand.full_name or "there",
                    "job_title": job.title if job else "the role",
                    "company_name": company_name,
                },
            )
        app.status = "rejected"
        app.rejected_at = datetime.now(UTC)
        audit.record(
            db, company_id=app.company_id, entity="application", entity_id=app.id,
            action="auto_rejected", source="scheduler",
            payload={"tier": app.tier, "days_pending": cutoff_days},
        )
    db.flush()
    return len(stale)


async def send_interview_reminders_for_company(
    db: Session, company_id: uuid.UUID, notifier: Notifier,
    *, hours_before: int = REMINDER_HOURS_BEFORE,
) -> int:
    """Remind candidates whose interview is within `hours_before` hours and who
    haven't been reminded yet. `reminder_sent_at` is the idempotency guard —
    running this twice in the window sends nothing the second time."""
    now = datetime.now(UTC)
    horizon = now + timedelta(hours=hours_before)
    due = db.scalars(
        select(Interview).where(
            Interview.status == "booked",
            Interview.reminder_sent_at.is_(None),
            Interview.scheduled_at > now,
            Interview.scheduled_at <= horizon,
            Interview.deleted_at.is_(None),
        )
    ).all()

    company = db.get(Company, company_id)
    company_name = company.name if company else "the company"

    for interview in due:
        app = db.get(Application, interview.application_id)
        if app is None:
            continue
        cand = db.get(Candidate, app.candidate_id)
        job = db.get(JobOpening, app.job_opening_id) if app.job_opening_id else None
        if cand and cand.email:
            await notifier.send(
                to=cand.email, template="interview_reminder",
                ctx={
                    "candidate_name": cand.full_name or "there",
                    "job_title": job.title if job else "the role",
                    "company_name": company_name,
                    "scheduled_at": interview.scheduled_at.isoformat(),
                },
            )
        interview.reminder_sent_at = now
        audit.record(
            db, company_id=interview.company_id, entity="interview", entity_id=interview.id,
            action="reminder_sent", source="scheduler",
        )
    db.flush()
    return len(due)


_PerCompanyJob = Callable[[Session, uuid.UUID, Notifier], Awaitable[int]]


async def _run_per_company(job: _PerCompanyJob, notifier: Notifier | None) -> dict[str, int]:
    """Fan out `job(db, company_id, notifier)` across every company, each in
    its own session with the RLS GUC set. companies has no RLS, so this is the
    one query that legitimately runs without a tenant GUC set."""
    notifier = notifier or get_notifier()
    results: dict[str, int] = {}
    with open_session() as scan:
        company_ids = [row[0] for row in scan.execute(select(Company.id))]

    for cid in company_ids:
        with open_session() as db:
            db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": str(cid)})
            n = await job(db, cid, notifier)
            if n:
                results[str(cid)] = n
    return results


async def auto_reject_stale_all(notifier: Notifier | None = None) -> dict[str, int]:
    return await _run_per_company(auto_reject_stale_for_company, notifier)


async def send_interview_reminders_all(notifier: Notifier | None = None) -> dict[str, int]:
    return await _run_per_company(send_interview_reminders_for_company, notifier)
