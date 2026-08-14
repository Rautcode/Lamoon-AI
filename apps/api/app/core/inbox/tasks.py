"""The nightly sweep: re-derive, escalate, then tell people once.

Order matters and is not arbitrary. Syncing first means the digest cannot
mention something that was fixed this afternoon, and escalating before the
digest means an item that has just crossed the threshold goes out in the same
message rather than tomorrow's.

Runs per tenant with RLS armed for each. A sweep that leaked across companies
would be the worst possible bug in this file, so the tenant scope is set
explicitly per company rather than assumed from a connection.
"""
import logging
import uuid

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.core.inbox import digest
from app.core.inbox import service as inbox
from app.modules.auth.models import Company

logger = logging.getLogger("lamoon.inbox")


async def sweep_all() -> dict[str, int]:
    """Every company, one at a time.

    One tenant's failure must not stop the rest — a company with bad data
    should not silence everybody else's notifications — so each is caught and
    logged rather than allowed to abort the sweep.
    """
    totals = {"companies": 0, "opened": 0, "resolved": 0, "escalated": 0, "notified": 0}
    db = SessionLocal()
    try:
        company_ids = [
            c.id
            for c in db.scalars(
                select(Company).where(Company.deleted_at.is_(None))
            ).all()
        ]
    finally:
        db.close()

    for company_id in company_ids:
        try:
            result = await sweep_company(company_id)
        except Exception:  # noqa: BLE001 — one tenant must not stop the sweep
            logger.exception("inbox sweep failed for company %s", company_id)
            continue
        totals["companies"] += 1
        for key in ("opened", "resolved", "escalated", "notified"):
            totals[key] += result.get(key, 0)
    return totals


async def sweep_company(company_id: uuid.UUID) -> dict[str, int]:
    from app.modules.payroll import inbox_sync as payroll_inbox

    db = SessionLocal()
    try:
        # RLS is FORCEd; without this every query below sees nothing and the
        # sweep silently reports success having done nothing at all.
        db.execute(
            text("SELECT set_config('app.company_id', :c, true)"),
            {"c": str(company_id)},
        )
        synced = payroll_inbox.sync_pending_work_facts(db, company_id=company_id)
        escalated = inbox.escalate_due(db)
        sent = await digest.send_digests(db, company_id=company_id)
        db.commit()
        return {
            "opened": synced.opened,
            "resolved": synced.resolved,
            "escalated": len(escalated),
            "notified": sent["people"],
        }
    finally:
        db.close()
