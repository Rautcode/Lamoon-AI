"""Balance computation — the one piece of real logic, shared by the balances
endpoint and the approve-time validation, so both can never disagree.
"""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit import service as audit
from app.modules.hr_core.models import Employee
from app.modules.leave import comp_off, entitlement, policy
from app.modules.leave.models import LeaveRequest, LeaveType
from app.modules.leave.schemas import LeaveBalanceOut
from app.modules.work_calendar import service as work_calendar

# ponytail: calendar year (Jan-Dec), not a configurable fiscal year. A request
# straddling Dec 31/Jan 1 is counted by its start_date's year only. Both are
# real simplifications an Indian SME's actual leave policy may need refined.


def used_days(
    db: Session, employee_id: uuid.UUID, leave_type_id: uuid.UUID, year: int
) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.leave_type_id == leave_type_id,
            LeaveRequest.status == "approved",
            func.extract("year", LeaveRequest.start_date) == year,
            LeaveRequest.deleted_at.is_(None),
        )
    )
    return Decimal(total or 0)


def entitled_days(
    db: Session, employee: Employee, leave_type: LeaveType, year: int,
    *, as_of: date | None = None,
) -> Decimal:
    """What this employee is owed for this type this year.

    Resolves the policy that applies to THEM — establishment, department or
    worker type — and prorates by their joining and exit dates. Falls back to
    the leave type's own `annual_quota` when nobody has written a policy, which
    is the difference between "no policy" and "no leave".
    """
    if comp_off.is_comp_off(leave_type):
        # Earned, not granted. A policy cannot allocate comp-off — working a
        # day off is what allocates it — so the entitlement IS the credit.
        return comp_off.earned_days(
            db, company_id=employee.company_id, employee=employee, year=year
        )

    candidates = [
        policy.Candidate(
            scope_type=p.scope_type, scope_id=p.scope_id, scope_value=p.scope_value,
            annual_days=str(p.annual_days), accrual_method=p.accrual_method,
            prorate_on_joining=p.prorate_on_joining, prorate_on_exit=p.prorate_on_exit,
            effective_from=p.effective_from, effective_to=p.effective_to,
            accrue_during_probation=p.accrue_during_probation,
            carry_forward_max=str(p.carry_forward_max)
            if p.carry_forward_max is not None
            else None,
            allow_negative_balance=p.allow_negative_balance,
            encashable=p.encashable,
        )
        for p in db.scalars(
            select(policy.LeavePolicy).where(
                policy.LeavePolicy.leave_type_id == leave_type.id,
                policy.LeavePolicy.deleted_at.is_(None),
            )
        ).all()
    ]
    applicable = policy.pick_policy(
        candidates,
        establishment_id=employee.establishment_id,
        department_id=employee.department_id,
        worker_type=employee.worker_type,
        on=as_of or date(year, 12, 31),
    )
    if applicable is None:
        return Decimal(leave_type.annual_quota)

    if not applicable.accrue_during_probation and employee.status == "probation":
        # Many companies accrue nothing until somebody is confirmed. Returning
        # zero here rather than filtering later keeps the reason visible: they
        # are not owed days yet, as opposed to having spent them.
        return Decimal(0)

    exited_on = employee.exited_on
    earned = entitlement.entitlement(
        annual_days=Decimal(applicable.annual_days),
        method=applicable.accrual_method,
        year=year,
        joined_on=employee.joined_on if applicable.prorate_on_joining else None,
        exited_on=exited_on if applicable.prorate_on_exit else None,
        as_of=as_of,
    )
    return earned + _carried_forward(db, employee, leave_type, year, applicable)


def _carried_forward(
    db: Session, employee: Employee, leave_type: LeaveType, year: int,
    applicable: policy.Applicable,
) -> Decimal:
    """Unused days brought in from last year, capped.

    Derived from last year's numbers rather than stored, for the same reason
    balances are: a carried-forward counter is one more thing to drift out of
    step with the requests it summarises.

    Expiry is deliberately NOT applied here. `carry_forward_expires_months` is
    recorded but unenforced, because expiring a day requires knowing WHEN it
    was carried — a credit ledger with dates, not a derived total. Carrying a
    day too long is generous and visible; expiring one that should not have
    been is a day somebody loses silently.
    """
    if applicable.carry_forward_max is None:
        return Decimal(0)
    cap = Decimal(applicable.carry_forward_max)
    if cap <= 0:
        return Decimal(0)

    last_year = year - 1
    prior_entitled = entitlement.entitlement(
        annual_days=Decimal(applicable.annual_days),
        method=applicable.accrual_method,
        year=last_year,
        joined_on=employee.joined_on if applicable.prorate_on_joining else None,
        exited_on=employee.exited_on if applicable.prorate_on_exit else None,
    )
    unused = prior_entitled - used_days(db, employee.id, leave_type.id, last_year)
    return min(max(unused, Decimal(0)), cap)


def balances_for(db: Session, employee_id: uuid.UUID) -> list[LeaveBalanceOut]:
    year = datetime.now(UTC).year
    employee = db.get(Employee, employee_id)
    types = db.scalars(select(LeaveType).where(LeaveType.deleted_at.is_(None))).all()
    out = []
    for lt in types:
        used = used_days(db, employee_id, lt.id, year)
        allocated = (
            entitled_days(db, employee, lt, year, as_of=date.today())
            if employee is not None
            else Decimal(lt.annual_quota)
        )
        out.append(
            LeaveBalanceOut(
                leave_type_id=lt.id,
                leave_type_name=lt.name,
                allocated=float(allocated),
                used=float(used),
                remaining=float(allocated - used),
            )
        )
    return out


def remaining_for(
    db: Session, employee_id: uuid.UUID, leave_type_id: uuid.UUID, quota: Decimal | int
) -> Decimal:
    year = datetime.now(UTC).year
    return Decimal(quota) - used_days(db, employee_id, leave_type_id, year)


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
    half_day: bool = False,
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
    if half_day and start_date != end_date:
        # Half of a five-day absence is not a thing anybody means, and
        # accepting it would bill 2.5 days for a week away.
        raise InvalidDateRange("a half day must start and end on the same day")

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

    billed = Decimal(days) / 2 if half_day else Decimal(days)

    req = LeaveRequest(
        company_id=company_id,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        start_date=start_date,
        end_date=end_date,
        days=billed,
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
