"""Compensatory off — leave you EARN by working a day you were not meant to.

This is the three-module join the plan calls the product: an **attendance
fact**, measured against a **calendar rule**, becomes a **leave credit**. No
one module can decide it. Attendance does not know the day was a holiday; the
calendar does not know anybody worked it; leave knows neither.

Two rules that make it correct rather than merely present:

  IT IS EARNED, NOT GRANTED    so it is credited from an approved work fact and
                               never from a policy. Unapproved work earns
                               nothing — that is what approval is for.
  IT IS CREDITED ONCE          a work fact is the unit, so re-running this
                               cannot pay somebody twice for one Sunday.

Deliberately NOT here: expiry. Comp-off that lapses after N days is a real
policy and a real feature, and it needs a credit ledger with dates rather than
a derived count. Until that exists, a credit does not expire — which is
generous rather than wrong, and visibly so.
"""
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.hr_core.models import Employee
from app.modules.leave.models import LeaveType
from app.modules.payroll.workforce import WorkFact
from app.modules.work_calendar import service as work_calendar

#: A full day worked on a day off earns a full day back. Half a day earns half.
#: Anything less earns nothing — a two-hour call-out is overtime, not a day.
MIN_HOURS_FOR_HALF = Decimal("4")
MIN_HOURS_FOR_FULL = Decimal("7")


@dataclass(frozen=True)
class Credit:
    day: date
    days: Decimal
    reason: str


def credit_for_hours(hours: Decimal) -> Decimal:
    """Hours worked on a non-working day → days of comp-off earned."""
    if hours >= MIN_HOURS_FOR_FULL:
        return Decimal("1.0")
    if hours >= MIN_HOURS_FOR_HALF:
        return Decimal("0.5")
    return Decimal("0")


def earned_for(
    db: Session, *, company_id: uuid.UUID, employee: Employee, year: int
) -> list[Credit]:
    """Every comp-off credit this employee has earned this leave year.

    Derived, never stored — the same reasoning the leave module already applies
    to balances: the work facts ARE the ledger, and a stored counter is one
    more thing to drift out of step with them.
    """
    start, end = date(year, 1, 1), date(year, 12, 31)
    facts = db.scalars(
        select(WorkFact).where(
            WorkFact.employee_id == employee.id,
            WorkFact.day >= start,
            WorkFact.day <= end,
            # Approved only. Unapproved work earns nothing, which is precisely
            # what approval is for.
            WorkFact.approved_at.is_not(None),
            WorkFact.deleted_at.is_(None),
        )
    ).all()
    if not facts:
        return []

    resolved = work_calendar.resolve_for(
        db, company_id=company_id, establishment_id=employee.establishment_id,
        start=start, end=end,
    )

    credits: list[Credit] = []
    for fact in facts:
        if resolved.is_working_day(fact.day):
            continue  # an ordinary working day earns pay, not a day back
        days = credit_for_hours(Decimal(fact.hours_worked or 0))
        if days <= 0:
            continue
        holiday = resolved.holidays.get(fact.day)
        credits.append(
            Credit(
                day=fact.day,
                days=days,
                reason=holiday or "weekly off",
            )
        )
    return credits


def earned_days(
    db: Session, *, company_id: uuid.UUID, employee: Employee, year: int
) -> Decimal:
    return sum(
        (c.days for c in earned_for(db, company_id=company_id, employee=employee, year=year)),
        start=Decimal("0"),
    )


def is_comp_off(leave_type: LeaveType) -> bool:
    return bool(getattr(leave_type, "comp_off", False))
