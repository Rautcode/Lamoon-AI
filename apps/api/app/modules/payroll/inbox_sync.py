"""Turning payroll's exceptions into somebody's inbox.

Payroll owns work facts, so payroll produces the items — `core/inbox` only
stores and reconciles them. The alternative, a core module reaching into
payroll's tables to work out what is pending, would make the inbox a second
place that knows payroll's rules.

**The routing rule:** an item goes to the person who can CLOSE it, not to a
pool. Unapproved work is the supervisor's to sign off, so it goes to the
reporting manager — and it deliberately carries hours, sites and dates, never
money. A manager approving overtime learns nothing about anybody's pay, which
is the same boundary `workfact.approve` draws in the permission model.
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.inbox import service as inbox
from app.modules.hr_core.models import Employee
from app.modules.payroll.models import PayrollRun
from app.modules.payroll.workforce import WorkFact

KIND = "workfact.pending"

#: A hard horizon on top of the finalized-period filter. Work nobody signed
#: off in half a year is a data-cleanup conversation, not a manager's inbox.
STALE_AFTER_DAYS = 180


def _manager_user_ids(db: Session, employee_ids: set[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """employee → the USER id of their reporting manager.

    Two hops, because an inbox belongs to somebody who logs in: employee →
    reporting manager (an employee) → that person's user account. A manager
    with no login has no inbox, and their reports' items are simply not raised
    here — surfacing them to nobody would be worse than not raising them, and
    HR still sees the same facts in readiness.
    """
    if not employee_ids:
        return {}
    rows = db.execute(
        select(Employee.id, Employee.reporting_manager_id).where(
            Employee.id.in_(employee_ids)
        )
    ).all()
    manager_ids = {m for _, m in rows if m}
    if not manager_ids:
        return {}
    logins: dict[uuid.UUID, uuid.UUID] = {
        employee_id: user_id
        for employee_id, user_id in db.execute(
            select(Employee.id, Employee.user_id).where(
                Employee.id.in_(manager_ids), Employee.user_id.is_not(None)
            )
        ).all()
        if user_id is not None
    }
    return {
        employee_id: logins[manager_id]
        for employee_id, manager_id in rows
        if manager_id and manager_id in logins
    }


def sync_pending_work_facts(db: Session, *, company_id: uuid.UUID) -> inbox.SyncResult:
    """Every unapproved work fact becomes its manager's problem, once.

    Reconciled rather than accumulated: approving the work makes the fact
    disappear from this query, and the next sync closes the item. That is why
    a manager who fixes something in the attendance screen never has to come
    back and tidy their inbox.

    **Bounded to periods that can still be paid.** Approving work for a month
    whose payroll is already finalized changes nothing — corrections there are
    adjustments in a later period — so asking a manager to do it is noise that
    never goes away. Unbounded, this query also grows forever: a work fact
    nobody approved in 2024 would sit in somebody's inbox for the life of the
    company. `validation.py` bounds the identical query to its period; this had
    no bound at all.
    """
    finalized = {
        run.period
        for run in db.scalars(
            select(PayrollRun).where(
                PayrollRun.status == "finalized", PayrollRun.deleted_at.is_(None)
            )
        ).all()
    }
    facts = [
        f
        for f in db.scalars(
            select(WorkFact).where(
                WorkFact.approved_at.is_(None),
                WorkFact.deleted_at.is_(None),
                # Nothing older than the horizon, whatever the run history says.
                WorkFact.day >= date.today() - timedelta(days=STALE_AFTER_DAYS),
            )
        ).all()
        if f.day.replace(day=1) not in finalized
    ]

    managers = _manager_user_ids(db, {f.employee_id for f in facts})
    names: dict[uuid.UUID, str] = (
        {
            employee_id: full_name
            for employee_id, full_name in db.execute(
                select(Employee.id, Employee.full_name).where(
                    Employee.id.in_({f.employee_id for f in facts})
                )
            ).all()
        }
        if facts
        else {}
    )

    items: dict[uuid.UUID, list[inbox.Item]] = {}
    for fact in facts:
        user_id = managers.get(fact.employee_id)
        if user_id is None:
            continue  # nobody to ask; readiness still reports it to HR
        who = names.get(fact.employee_id, "An employee")
        # Hours, site and date. No money — a manager approves work, not pay.
        detail = f"{fact.hours_worked}h worked"
        if fact.overtime_hours:
            detail += f", {fact.overtime_hours}h overtime"
        if fact.site:
            detail += f" at {fact.site}"
        items.setdefault(user_id, []).append(
            inbox.Item(
                kind=KIND,
                dedupe_key=str(fact.id),
                title=f"Approve work for {who} on {fact.day.isoformat()}",
                detail=detail,
                severity="review",
                entity="work_fact",
                entity_id=fact.id,
                href="/attendance",
                # It stops being advisory when the month it belongs to is run.
                due_on=_month_end(fact.day),
            )
        )

    return inbox.sync(
        db, company_id=company_id, kind=KIND, scope_key="company", items=items
    )


def _month_end(day: date) -> date:
    import calendar as _cal

    return day.replace(day=_cal.monthrange(day.year, day.month)[1])
