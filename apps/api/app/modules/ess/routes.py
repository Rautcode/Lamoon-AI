"""Employee Self-Service — the `/me/**` surface.

THE security invariant of this module:

    The employee is ALWAYS derived from the JWT. No route here accepts an
    employee_id, from the path, the query, or the body.

That's why an employee can hold `self.*` and nothing else: there is no
parameter they could tamper with to reach another person's record. Every
handler starts from `_me()`, which resolves the caller's own Employee row or
404s. Combined with RLS (which already confines them to their company), the
blast radius of the `employee` role is exactly one person: themselves.

Anything that needs to name someone else belongs in hr_core or leave, behind
employee.*/leave.* permissions.
"""
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.attendance import service as attendance_service
from app.modules.attendance.models import AttendanceEvent
from app.modules.attendance.schemas import DaySummaryOut, PunchIn
from app.modules.ess.schemas import MyLeaveRequestIn
from app.modules.hr_core.models import Employee
from app.modules.hr_core.schemas import EmployeeOut
from app.modules.leave import service as leave_service
from app.modules.leave.models import LeaveRequest
from app.modules.leave.schemas import LeaveBalanceOut, LeaveRequestOut
from app.modules.payroll import service as payroll_service
from app.modules.payroll.schemas import PayslipOut

router = APIRouter(prefix="/me", tags=["ess"])
CAN_READ_SELF = [Depends(require("self.read"))]
CAN_FILE_LEAVE = [Depends(require("self.leave.write"))]
CAN_PUNCH = [Depends(require("self.attendance.write"))]
CAN_READ_PAYSLIP = [Depends(require("self.payslip.read"))]


def _me(db: Session, principal: Principal) -> Employee:
    """The caller's own employee record. The ONLY way this module identifies a
    person — note it takes no id argument by design."""
    emp = db.scalar(
        select(Employee).where(
            Employee.user_id == uuid.UUID(principal.user_id),
            Employee.deleted_at.is_(None),
        )
    )
    if emp is None:
        # A real state: admins/HR have logins with no employee record. Say so
        # plainly rather than pretending the person doesn't exist.
        raise HTTPException(404, "no employee record is linked to this login")
    return emp


@router.get("", response_model=EmployeeOut, dependencies=CAN_READ_SELF)
def my_profile(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    _cid: str = Depends(resolve_tenant),
):
    return _me(db, principal)


@router.get("/leave/balances", response_model=list[LeaveBalanceOut], dependencies=CAN_READ_SELF)
def my_balances(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    _cid: str = Depends(resolve_tenant),
):
    return leave_service.balances_for(db, _me(db, principal).id)


@router.get("/leave/requests", response_model=list[LeaveRequestOut], dependencies=CAN_READ_SELF)
def my_leave_requests(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    _cid: str = Depends(resolve_tenant),
):
    me = _me(db, principal)
    return db.scalars(
        select(LeaveRequest)
        .where(LeaveRequest.employee_id == me.id, LeaveRequest.deleted_at.is_(None))
        .order_by(LeaveRequest.created_at.desc())
    ).all()


@router.post("/leave/requests", response_model=LeaveRequestOut, dependencies=CAN_FILE_LEAVE)
def file_my_leave(
    body: MyLeaveRequestIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """File leave for yourself. `MyLeaveRequestIn` has no employee_id field —
    filing on someone else's behalf isn't a thing you can express here, it's
    an HR action behind leave.write."""
    me = _me(db, principal)
    try:
        return leave_service.create_request(
            db,
            company_id=uuid.UUID(cid),
            employee_id=me.id,  # from the JWT, never from the request
            leave_type_id=body.leave_type_id,
            start_date=body.start_date,
            end_date=body.end_date,
            reason=body.reason,
            source="ess",
        )
    except (leave_service.InvalidDateRange, leave_service.NoWorkingDays) as e:
        raise HTTPException(422, str(e)) from None


# --- attendance -------------------------------------------------------------


@router.post("/attendance/punch", response_model=DaySummaryOut, dependencies=CAN_PUNCH)
def punch(
    body: PunchIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """Record your own check-in/out. The timestamp is the SERVER's — accepting
    a client-supplied time would make attendance self-reported, which is not
    attendance. HR corrections go through /attendance/punch instead."""
    me = _me(db, principal)
    policy = attendance_service.get_policy(db, uuid.UUID(cid))
    now = datetime.now(UTC)

    # Reject the no-op rather than writing a punch that pairs with nothing:
    # two check-ins in a row is almost always a double-tap.
    current = attendance_service.today_for(db, me.id, policy, now=now)
    if body.kind == "in" and current.open:
        raise HTTPException(409, "you're already checked in")
    if body.kind == "out" and not current.open:
        raise HTTPException(409, "you're not checked in")

    db.add(
        AttendanceEvent(
            company_id=uuid.UUID(cid), employee_id=me.id, kind=body.kind,
            at=now, source="ess", note=body.note,
        )
    )
    db.flush()
    return attendance_service.today_for(db, me.id, policy, now=now)


@router.get("/attendance/today", response_model=DaySummaryOut, dependencies=CAN_READ_SELF)
def my_today(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """Today's clock, with "today" decided by the COMPANY's timezone.

    The browser must not compute this: its UTC date diverges from the
    company-local date every evening in IST, which would show "not checked in"
    to someone who is very much checked in."""
    me = _me(db, principal)
    policy = attendance_service.get_policy(db, uuid.UUID(cid))
    return attendance_service.today_for(db, me.id, policy)


@router.get("/attendance", response_model=list[DaySummaryOut], dependencies=CAN_READ_SELF)
def my_attendance(
    days: int = 14,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    me = _me(db, principal)
    policy = attendance_service.get_policy(db, uuid.UUID(cid))
    days = max(1, min(days, 92))
    tz = attendance_service.tz_of(policy)
    today = attendance_service.local_date(datetime.now(UTC), tz)
    return attendance_service.summaries_for(
        db, me.id, policy, today - timedelta(days=days - 1), today
    )


# --- payslips ---------------------------------------------------------------


@router.get("/payslips", response_model=list[PayslipOut], dependencies=CAN_READ_PAYSLIP)
def my_payslips(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    _cid: str = Depends(resolve_tenant),
):
    """Own payslips, FINALIZED runs only.

    A draft payslip is a figure HR is still working on — an employee seeing
    one would be reading a number that is about to change. The finalized
    filter lives in the payroll service, not in this handler, so no other
    caller can forget it."""
    return payroll_service.finalized_payslips_for(db, _me(db, principal).id)
