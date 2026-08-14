"""Attendance → work facts. The join payroll never had.

Payroll has never read attendance. LOP came only from approved unpaid leave, so
an employee who never punched in for a month and filed no leave was **paid in
full** — while the attendance module sat there producing the data that would
have said otherwise.

**Owns:** nothing. This is a translator.
**Consumes:** day states (attendance), the resolved calendar (work_calendar),
approved leave (leave).
**Produces:** work facts with `source="attendance"`, which payroll already
knows how to read.
**Correction behaviour:** derived facts are replaced on every run; anything a
human typed is left exactly alone.

The rule that makes this safe rather than merely present:

    A MISSING PUNCH NEVER BECOMES LOP.

An unexplained absence becomes an **unapproved** fact — it exists so somebody
must look at it, and payroll cannot act on it until they do. Silently docking
somebody because a biometric reader failed is a worse error than paying a day
too many, and it is the one nobody notices until payday.
"""
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.attendance import service as attendance
from app.modules.hr_core.models import Employee
from app.modules.leave.models import LeaveRequest, LeaveType
from app.modules.payroll.workforce import WorkFact

#: Facts this module owns and may replace. Anything else was typed by a person.
DERIVED_SOURCE = "attendance"


@dataclass(frozen=True)
class Derived:
    """What a day state becomes."""

    status: str
    #: Whether it arrives already signed off. Leave was approved by somebody
    #: already; an absence nobody explained was not, and neither was a day
    #: whose hours are unknown.
    auto_approve: bool


def derive(state: str) -> Derived | None:
    """One day state → one work fact, or none at all.

    Pure, so the mapping that decides whether somebody loses a day's pay is
    testable without a tenant.
    """
    if state in ("holiday", "weekly_off"):
        # The calendar already accounts for these. A fact would be a second
        # opinion about the same day.
        return None
    if state in ("paid_leave", "unpaid_leave"):
        # Explained absence. Payroll takes the LOP from the leave record, not
        # from here, and asking a manager to approve leave a second time is the
        # product forgetting what it was told.
        return Derived(status="leave", auto_approve=True)
    if state == "absent":
        return Derived(status="absent", auto_approve=False)
    if state == "missing_punch":
        # Somebody worked; the record is incomplete. Not an absence — but the
        # hours are unknown, so a human confirms them.
        return Derived(status="worked", auto_approve=False)
    return Derived(status="worked", auto_approve=True)


def derive_period(
    db: Session, *, company_id: uuid.UUID, period: date, now: datetime | None = None
) -> dict[str, int]:
    """Build this month's facts for everybody, from what attendance already knows.

    Idempotent: it replaces the facts it owns and leaves every hand-entered one
    standing. Running it nightly must not double anybody's days, and running it
    after somebody corrected a fact by hand must not undo them.
    """
    now = now or datetime.now(UTC)
    start = period.replace(day=1)
    end = start.replace(day=monthrange(start.year, start.month)[1])

    employees = db.scalars(
        select(Employee).where(
            Employee.status != "exited", Employee.deleted_at.is_(None)
        )
    ).all()
    policy = attendance.get_policy(db, company_id)

    counts = {"worked": 0, "leave": 0, "absent": 0, "kept": 0}
    for employee in employees:
        # Somebody who had not joined yet is not absent — they were not
        # employed. Deriving absences for them would invent unpaid days.
        first = max(start, employee.joined_on) if employee.joined_on else start
        if first > end:
            continue

        summaries = attendance.summaries_for(db, employee.id, policy, first, end, now=now)
        leave = _leave_by_day(db, employee.id, first, end)
        existing = {
            f.day: f
            for f in db.scalars(
                select(WorkFact).where(
                    WorkFact.employee_id == employee.id,
                    WorkFact.day >= first, WorkFact.day <= end,
                    WorkFact.deleted_at.is_(None),
                )
            ).all()
        }

        for summary in summaries:
            state = attendance.day_state(
                summary, leave=leave.get(summary.day), today=now.date()
            )
            wanted = derive(state)
            current = existing.get(summary.day)

            if current is not None and current.source != DERIVED_SOURCE:
                counts["kept"] += 1  # a human typed it; leave it alone
                continue
            if wanted is None:
                if current is not None:
                    current.deleted_at = now  # the day stopped needing a fact
                continue

            fact = current or WorkFact(
                company_id=company_id, employee_id=employee.id, day=summary.day,
                source=DERIVED_SOURCE,
            )
            fact.status = wanted.status
            fact.hours_worked = Decimal(summary.worked_minutes) / 60
            fact.approved_at = now if wanted.auto_approve else None
            fact.approved_by = None
            if current is None:
                db.add(fact)
            counts[wanted.status] = counts.get(wanted.status, 0) + 1

    db.flush()
    return counts


def _leave_by_day(
    db: Session, employee_id: uuid.UUID, start: date, end: date
) -> dict[date, str]:
    """Approved leave, expanded to one entry per covered day.

    ONE query for the whole range. The obvious version asks per day, which is
    thirty-odd queries per employee per month — the same N+1 shape that made a
    payroll run cost 14 queries a head.
    """
    rows = db.execute(
        select(LeaveRequest.start_date, LeaveRequest.end_date, LeaveType.paid)
        .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
        .where(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= end,
            LeaveRequest.end_date >= start,
            LeaveRequest.deleted_at.is_(None),
            LeaveType.deleted_at.is_(None),
        )
    ).all()

    out: dict[date, str] = {}
    for first, last, paid in rows:
        day = max(first, start)
        while day <= min(last, end):
            out[day] = "paid_leave" if paid else "unpaid_leave"
            day += timedelta(days=1)
    return out
