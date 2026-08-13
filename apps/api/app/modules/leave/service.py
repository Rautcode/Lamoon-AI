"""Balance computation — the one piece of real logic, shared by the balances
endpoint and the approve-time validation, so both can never disagree.
"""
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.hr_core.models import Employee
from app.modules.leave.models import LeaveRequest, LeaveType
from app.modules.leave.schemas import LeaveBalanceOut
from app.modules.work_calendar import service as work_calendar

# ponytail: calendar year (Jan-Dec), not a configurable fiscal year. A request
# straddling Dec 31/Jan 1 is counted by its start_date's year only. Both are
# real simplifications an Indian SME's actual leave policy may need refined.


def used_days(db: Session, employee_id: uuid.UUID, leave_type_id: uuid.UUID, year: int) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.leave_type_id == leave_type_id,
            LeaveRequest.status == "approved",
            func.extract("year", LeaveRequest.start_date) == year,
            LeaveRequest.deleted_at.is_(None),
        )
    )
    return int(total or 0)


def balances_for(db: Session, employee_id: uuid.UUID) -> list[LeaveBalanceOut]:
    year = datetime.now(UTC).year
    types = db.scalars(select(LeaveType).where(LeaveType.deleted_at.is_(None))).all()
    out = []
    for lt in types:
        used = used_days(db, employee_id, lt.id, year)
        out.append(
            LeaveBalanceOut(
                leave_type_id=lt.id,
                leave_type_name=lt.name,
                allocated=lt.annual_quota,
                used=used,
                remaining=lt.annual_quota - used,
            )
        )
    return out


def remaining_for(db: Session, employee_id: uuid.UUID, leave_type_id: uuid.UUID, quota: int) -> int:
    year = datetime.now(UTC).year
    return quota - used_days(db, employee_id, leave_type_id, year)


class InvalidDateRange(ValueError):
    """end_date before start_date. Routes map this to a 422."""


class NoWorkingDays(ValueError):
    """The whole range is weekend/holiday. Routes map this to a 422."""


def create_request(
    db: Session,
    *,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    leave_type_id: uuid.UUID,
    start_date: date,
    end_date: date,
    reason: str | None = None,
    source: str | None = None,
) -> LeaveRequest:
    """The single implementation of "file a leave request".

    Both HR (`POST /leave/requests`) and an employee filing their own
    (`POST /me/leave/requests`) route through here, so the day-count rule and
    validation can't drift between the two paths. The CALLER decides whose
    request it is — ESS passes the id it derived from the JWT, never one from
    the request body.
    """
    if end_date < start_date:
        raise InvalidDateRange("end_date must not be before start_date")

    # Billed in WORKING days. Counting calendar days (what this did before)
    # charged 4 days for a Friday-to-Monday absence — real balance taken off
    # people for days they were never going to work.
    establishment_id = db.scalar(
        select(Employee.establishment_id).where(Employee.id == employee_id)
    )
    days, holidays = work_calendar.billable_days(
        db, company_id, start_date, end_date, establishment_id=establishment_id
    )
    if days == 0:
        raise NoWorkingDays("that range contains no working days")

    req = LeaveRequest(
        company_id=company_id,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        start_date=start_date,
        end_date=end_date,
        days=days,
        reason=reason,
        status="pending",
    )
    db.add(req)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="leave_request", entity_id=req.id,
        action="requested", source=source,
        payload={
            "days": days,
            "calendar_days": (end_date - start_date).days + 1,
            "holidays_excluded": [d.isoformat() for d in holidays],
        },
    )
    return req
