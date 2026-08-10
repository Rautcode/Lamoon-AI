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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.ess.schemas import MyLeaveRequestIn
from app.modules.hr_core.models import Employee
from app.modules.hr_core.schemas import EmployeeOut
from app.modules.leave import service as leave_service
from app.modules.leave.models import LeaveRequest
from app.modules.leave.schemas import LeaveBalanceOut, LeaveRequestOut

router = APIRouter(prefix="/me", tags=["ess"])
CAN_READ_SELF = [Depends(require("self.read"))]
CAN_FILE_LEAVE = [Depends(require("self.leave.write"))]


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
    except leave_service.InvalidDateRange as e:
        raise HTTPException(422, str(e)) from None
