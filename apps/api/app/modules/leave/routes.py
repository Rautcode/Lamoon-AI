"""Leave management endpoints (ARCH-adjacent — leave wasn't in the original
API surface sketch, this follows the same shape as hr_core). HR/admin/manager
administer this; no employee self-submission yet (see models.py docstring).
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.audit import service as audit
from app.modules.leave import service as leave_service
from app.modules.leave.models import LeaveRequest, LeaveType
from app.modules.leave.schemas import (
    LeaveBalanceOut,
    LeaveRequestIn,
    LeaveRequestOut,
    LeaveTypeIn,
    LeaveTypeOut,
)

router = APIRouter(prefix="/leave", tags=["leave"])
CAN_WRITE = [Depends(require("leave.write"))]
CAN_READ = [Depends(require("leave.read"))]
CAN_APPROVE = [Depends(require("leave.approve"))]


@router.post("/types", response_model=LeaveTypeOut, dependencies=CAN_WRITE)
def create_leave_type(
    body: LeaveTypeIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    lt = LeaveType(company_id=uuid.UUID(cid), **body.model_dump())
    db.add(lt)
    db.flush()
    return lt


@router.get("/types", response_model=list[LeaveTypeOut], dependencies=CAN_READ)
def list_leave_types(db: Session = Depends(get_db)):
    return db.scalars(select(LeaveType).where(LeaveType.deleted_at.is_(None))).all()


@router.get("/balances/{employee_id}", response_model=list[LeaveBalanceOut], dependencies=CAN_READ)
def get_balances(employee_id: uuid.UUID, db: Session = Depends(get_db)):
    return leave_service.balances_for(db, employee_id)


@router.post("/requests", response_model=LeaveRequestOut, dependencies=CAN_WRITE)
def create_leave_request(
    body: LeaveRequestIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """HR files on someone's behalf. An employee filing their OWN leave uses
    /me/leave/requests, which shares create_request() below."""
    try:
        return leave_service.create_request(
            db,
            company_id=uuid.UUID(cid),
            employee_id=body.employee_id,
            leave_type_id=body.leave_type_id,
            start_date=body.start_date,
            end_date=body.end_date,
            reason=body.reason,
            source="hr",
        )
    except leave_service.InvalidDateRange as e:
        raise HTTPException(422, str(e)) from None


@router.get("/requests", response_model=list[LeaveRequestOut], dependencies=CAN_READ)
def list_leave_requests(
    employee_id: uuid.UUID | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = select(LeaveRequest).where(LeaveRequest.deleted_at.is_(None))
    if employee_id:
        q = q.where(LeaveRequest.employee_id == employee_id)
    if status:
        q = q.where(LeaveRequest.status == status)
    return db.scalars(q.order_by(LeaveRequest.created_at.desc())).all()


def _get_pending(request_id: uuid.UUID, db: Session) -> LeaveRequest:
    req = db.get(LeaveRequest, request_id)
    if req is None or req.deleted_at is not None:
        raise HTTPException(404, "not found")
    if req.status != "pending":
        raise HTTPException(409, f"request already {req.status}")
    return req


def _decide(
    req: LeaveRequest, new_status: str, db: Session, cid: str, user_id: str
) -> LeaveRequest:
    req.status = new_status
    req.decided_by = uuid.UUID(user_id)
    req.decided_at = datetime.now(UTC)
    audit.record(
        db, company_id=uuid.UUID(cid), entity="leave_request", entity_id=req.id, action=new_status,
    )
    return req


@router.post(
    "/requests/{request_id}/approve", response_model=LeaveRequestOut, dependencies=CAN_APPROVE
)
def approve_leave_request(
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
    principal: Principal = Depends(current_user),
):
    req = _get_pending(request_id, db)
    leave_type = db.get(LeaveType, req.leave_type_id)
    if leave_type is None:
        raise HTTPException(404, "leave type not found")
    remaining = leave_service.remaining_for(
        db, req.employee_id, req.leave_type_id, leave_type.annual_quota
    )
    if req.days > remaining:
        raise HTTPException(409, f"approving would exceed balance ({remaining} days left)")
    return _decide(req, "approved", db, cid, principal.user_id)


@router.post(
    "/requests/{request_id}/reject", response_model=LeaveRequestOut, dependencies=CAN_APPROVE
)
def reject_leave_request(
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
    principal: Principal = Depends(current_user),
):
    req = _get_pending(request_id, db)
    return _decide(req, "rejected", db, cid, principal.user_id)
