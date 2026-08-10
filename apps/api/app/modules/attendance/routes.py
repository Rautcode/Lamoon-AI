"""Attendance — the HR/manager surface. An employee's own punching lives on
/me/attendance (modules/ess), which takes no employee id."""
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import require
from app.core.tenant import resolve_tenant
from app.modules.attendance import service
from app.modules.attendance.models import AttendanceEvent
from app.modules.attendance.schemas import (
    DaySummaryOut,
    EmployeeAttendanceOut,
    PolicyIn,
    PolicyOut,
    PresenceOut,
    PunchOnBehalfIn,
)
from app.modules.audit import service as audit
from app.modules.hr_core.models import Employee

router = APIRouter(prefix="/attendance", tags=["attendance"])
CAN_READ = [Depends(require("attendance.read"))]
CAN_WRITE = [Depends(require("attendance.write"))]


@router.get("/policy", response_model=PolicyOut, dependencies=CAN_READ)
def get_policy(db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    return service.get_policy(db, uuid.UUID(cid))


@router.put("/policy", response_model=PolicyOut, dependencies=CAN_WRITE)
def update_policy(
    body: PolicyIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    policy = service.get_policy(db, uuid.UUID(cid))
    # Reject an unknown zone here rather than silently falling back to UTC and
    # quietly misfiling every future punch.
    try:
        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(422, f"unknown timezone: {body.timezone}") from None
    for k, v in body.model_dump().items():
        setattr(policy, k, v)
    db.flush()
    return policy


@router.get("/today", response_model=list[PresenceOut], dependencies=CAN_READ)
def presence_today(db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    """Who's in right now. Everyone active is listed — people with no punches
    show as `absent`, which is the question this view exists to answer."""
    policy = service.get_policy(db, uuid.UUID(cid))
    employees = db.scalars(
        select(Employee).where(Employee.deleted_at.is_(None), Employee.status != "exited")
    ).all()

    out = []
    for e in employees:
        day = service.today_for(db, e.id, policy)
        status = "in" if day.open else ("out" if day.first_in else "absent")
        out.append(
            PresenceOut(
                employee_id=e.id, full_name=e.full_name, status=status,
                first_in=day.first_in, last_out=day.last_out,
                worked_minutes=day.worked_minutes, late=day.late,
            )
        )
    return out


@router.get("/summary", response_model=list[EmployeeAttendanceOut], dependencies=CAN_READ)
def company_summary(
    days: int = 14, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Everyone × the last N days, in one response.

    Exists so the heatmap doesn't have to fire one request per employee from
    the browser. Declared BEFORE /{employee_id} — otherwise FastAPI matches
    "summary" as an employee id and every call 422s on the UUID parse.
    """
    company_id = uuid.UUID(cid)
    policy = service.get_policy(db, company_id)
    days = max(1, min(days, 92))
    tz = service.tz_of(policy)
    today = service.local_date(datetime.now(UTC), tz)
    start = today - timedelta(days=days - 1)

    employees = db.scalars(
        select(Employee).where(Employee.deleted_at.is_(None), Employee.status != "exited")
    ).all()
    return [
        EmployeeAttendanceOut(
            employee_id=e.id,
            full_name=e.full_name,
            days=[
                DaySummaryOut.model_validate(d)
                for d in service.summaries_for(db, e.id, policy, start, today)
            ],
        )
        for e in employees
    ]


@router.get("/{employee_id}", response_model=list[DaySummaryOut], dependencies=CAN_READ)
def employee_attendance(
    employee_id: uuid.UUID,
    days: int = 14,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    policy = service.get_policy(db, uuid.UUID(cid))
    days = max(1, min(days, 92))  # bounded so a big range can't be requested by accident
    tz = service.tz_of(policy)
    today = service.local_date(datetime.now(UTC), tz)
    return service.summaries_for(db, employee_id, policy, today - timedelta(days=days - 1), today)


@router.post("/punch", response_model=DaySummaryOut, dependencies=CAN_WRITE)
def punch_on_behalf(
    body: PunchOnBehalfIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """HR recording or correcting someone's punch. Corrections are new events —
    the ledger is append-only, so what was originally recorded stays visible."""
    company_id = uuid.UUID(cid)
    emp = db.get(Employee, body.employee_id)
    if emp is None or emp.deleted_at is not None:
        raise HTTPException(404, "not found")

    at = body.at or datetime.now(UTC)
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    db.add(
        AttendanceEvent(
            company_id=company_id, employee_id=emp.id, kind=body.kind,
            at=at, source="hr", note=body.note,
        )
    )
    db.flush()
    audit.record(
        db, company_id=company_id, entity="attendance", entity_id=emp.id,
        action=f"punch_{body.kind}", source="hr", payload={"at": at.isoformat()},
    )
    policy = service.get_policy(db, company_id)
    tz = service.tz_of(policy)
    day = service.local_date(at, tz)
    found = service.summaries_for(db, emp.id, policy, day, day)
    return found[0] if found else service.DaySummary(day=day)
