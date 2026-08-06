"""Scheduled ATS workflows (spec: Workflow 2 — Daily Auto Reject).

Tier C/D applications wait `AUTO_REJECT_DAYS` after screening, then get
rejected automatically with a candidate email. Runs once per tenant (RLS
requires the GUC set per company; there's no cross-tenant query), fanned out
from the companies table — the one table not under RLS.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import open_session
from app.core.notify.base import Notifier, get_notifier
from app.modules.ats.models import Application, Candidate, JobOpening
from app.modules.audit import service as audit
from app.modules.auth.models import Company

AUTO_REJECT_DAYS = 10


async def auto_reject_stale_for_company(
    db: Session, company_id, notifier: Notifier, *, cutoff_days: int = AUTO_REJECT_DAYS
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


async def auto_reject_stale_all(notifier: Notifier | None = None) -> dict[str, int]:
    """Fan out across every company. companies has no RLS, so this is the one
    query that legitimately runs without a tenant GUC set."""
    notifier = notifier or get_notifier()
    results: dict[str, int] = {}
    with open_session() as scan:
        company_ids = [row[0] for row in scan.execute(select(Company.id))]

    for cid in company_ids:
        with open_session() as db:
            db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": str(cid)})
            n = await auto_reject_stale_for_company(db, cid, notifier)
            if n:
                results[str(cid)] = n
    return results
