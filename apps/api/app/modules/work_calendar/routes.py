"""Work calendar endpoints.

Reading is open to anyone with a login — the holiday list is something every
employee needs and nothing about it is sensitive. Writing needs
`calendar.write` (HR/admin), because changing the work week silently changes
how every future leave request is billed.
"""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.audit import service as audit
from app.modules.hr_core.models import Employee
from app.modules.work_calendar import service
from app.modules.work_calendar.models import CalendarAssignment, Holiday, WorkCalendar
from app.modules.work_calendar.schemas import (
    AssignmentIn,
    AssignmentOut,
    CalendarIn,
    CalendarOut,
    HolidayIn,
    HolidayOut,
    ResolvedOut,
    WorkCalendarIn,
    WorkCalendarOut,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])
ANY_USER = [Depends(current_user)]
CAN_WRITE = [Depends(require("calendar.write"))]


@router.get("/work-week", response_model=WorkCalendarOut, dependencies=ANY_USER)
def get_work_week(db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    return service.default_calendar(db, uuid.UUID(cid))


@router.put("/work-week", response_model=WorkCalendarOut, dependencies=CAN_WRITE)
def set_work_week(
    body: WorkCalendarIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    company_id = uuid.UUID(cid)
    cal = service.default_calendar(db, company_id)
    cal.working_days = body.working_days
    db.flush()
    audit.record(
        db, company_id=company_id, entity="work_calendar", entity_id=cal.id,
        action="work_week_changed", payload={"working_days": body.working_days},
    )
    return cal


@router.get("/holidays", response_model=list[HolidayOut], dependencies=ANY_USER)
def list_holidays(
    days: int = 365, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    """Holidays from a month ago through the next `days` — recent past included
    so the attendance heatmap can explain last week's empty cells."""
    days = max(1, min(days, 730))
    today = date.today()
    return db.scalars(
        select(Holiday)
        .where(
            Holiday.day >= today - timedelta(days=31),
            Holiday.day <= today + timedelta(days=days),
            Holiday.deleted_at.is_(None),
        )
        .order_by(Holiday.day)
    ).all()


@router.post("/holidays", response_model=HolidayOut, dependencies=CAN_WRITE)
def add_holiday(
    body: HolidayIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    company_id = uuid.UUID(cid)
    # The company's default calendar. A tenant with one calendar — which is
    # every tenant until it makes a second — keeps the behaviour it had.
    calendar = service.default_calendar(db, company_id)
    existing = db.scalar(
        select(Holiday).where(
            Holiday.calendar_id == calendar.id,
            Holiday.day == body.day,
            Holiday.deleted_at.is_(None),
        )
    )
    if existing:
        # Idempotent rename rather than a duplicate row — two entries for the
        # same date would be harmless for billing (holidays are a set) but
        # confusing to look at.
        existing.name = body.name
        db.flush()
        return existing

    holiday = Holiday(
        company_id=company_id, calendar_id=calendar.id, day=body.day, name=body.name
    )
    db.add(holiday)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="holiday", entity_id=holiday.id,
        action="added", payload={"day": body.day.isoformat(), "name": body.name},
    )
    return holiday


@router.delete("/holidays/{holiday_id}", status_code=204, dependencies=CAN_WRITE)
def remove_holiday(
    holiday_id: uuid.UUID, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    holiday = db.get(Holiday, holiday_id)
    if holiday is None or holiday.deleted_at is not None:
        raise HTTPException(404, "not found")
    from datetime import UTC, datetime

    holiday.deleted_at = datetime.now(UTC)  # soft delete, per the row convention
    audit.record(
        db, company_id=uuid.UUID(cid), entity="holiday", entity_id=holiday.id, action="removed",
    )


# --- multiple calendars, and who they apply to -------------------------------
#
# The legacy /work-week and /holidays routes above operate on the company's
# DEFAULT calendar, so a tenant with one calendar — every tenant until it makes
# a second — behaves exactly as it did before.


@router.get("/calendars", response_model=list[CalendarOut], dependencies=ANY_USER)
def list_calendars(db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    service.default_calendar(db, uuid.UUID(cid))  # a company always has one
    return db.scalars(
        select(WorkCalendar)
        .where(WorkCalendar.deleted_at.is_(None))
        .order_by(WorkCalendar.created_at)
    ).all()


@router.post("/calendars", response_model=CalendarOut, dependencies=CAN_WRITE)
def create_calendar(
    body: CalendarIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    company_id = uuid.UUID(cid)
    service.default_calendar(db, company_id)  # keep the company scope covered
    cal = WorkCalendar(company_id=company_id, **body.model_dump())
    db.add(cal)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="work_calendar", entity_id=cal.id,
        action="created", payload={"name": cal.name, "working_days": cal.working_days},
    )
    return cal


@router.get("/calendars/{calendar_id}", response_model=CalendarOut, dependencies=ANY_USER)
def get_calendar_by_id(
    calendar_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    cal = db.get(WorkCalendar, calendar_id)
    if cal is None or cal.deleted_at is not None:
        raise HTTPException(404, "calendar not found")
    return cal


@router.get(
    "/calendars/{calendar_id}/holidays", response_model=list[HolidayOut],
    dependencies=ANY_USER,
)
def list_calendar_holidays(
    calendar_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    return db.scalars(
        select(Holiday)
        .where(Holiday.calendar_id == calendar_id, Holiday.deleted_at.is_(None))
        .order_by(Holiday.day)
    ).all()


@router.post(
    "/calendars/{calendar_id}/holidays", response_model=HolidayOut, dependencies=CAN_WRITE
)
def add_calendar_holiday(
    calendar_id: uuid.UUID,
    body: HolidayIn,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    """A holiday on ONE calendar. The same date can be a holiday here and an
    ordinary working day on another calendar — that is the whole point."""
    company_id = uuid.UUID(cid)
    cal = db.get(WorkCalendar, calendar_id)
    if cal is None or cal.deleted_at is not None:
        raise HTTPException(404, "calendar not found")

    existing = db.scalar(
        select(Holiday).where(
            Holiday.calendar_id == calendar_id,
            Holiday.day == body.day,
            Holiday.deleted_at.is_(None),
        )
    )
    if existing:  # idempotent rename rather than a duplicate row
        existing.name = body.name
        db.flush()
        return existing

    holiday = Holiday(
        company_id=company_id, calendar_id=calendar_id, day=body.day, name=body.name
    )
    db.add(holiday)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="holiday", entity_id=holiday.id,
        action="added",
        payload={"calendar": cal.name, "day": body.day.isoformat(), "name": body.name},
    )
    return holiday


@router.get("/assignments", response_model=list[AssignmentOut], dependencies=ANY_USER)
def list_assignments(db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)):
    return db.scalars(
        select(CalendarAssignment)
        .where(CalendarAssignment.deleted_at.is_(None))
        .order_by(CalendarAssignment.effective_from)
    ).all()


@router.post("/assignments", response_model=AssignmentOut, dependencies=CAN_WRITE)
def create_assignment(
    body: AssignmentIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Assign a calendar to a scope, from a date.

    Effective-dated on purpose: August 2026's payroll must resolve the
    assignment in force in August 2026, not whatever is current when somebody
    presses recompute.
    """
    company_id = uuid.UUID(cid)
    cal = db.get(WorkCalendar, body.calendar_id)
    if cal is None or cal.deleted_at is not None:
        raise HTTPException(404, "calendar not found")
    if body.scope_type not in service.SCOPE_PRECEDENCE:
        raise HTTPException(422, f"unknown scope: {body.scope_type}")
    if body.scope_type in ("location", "employee_group"):
        raise HTTPException(
            422,
            f"{body.scope_type} calendars are modelled but not yet assignable — "
            "that entity does not exist yet",
        )
    if body.scope_type == "establishment" and body.scope_id is None:
        raise HTTPException(422, "an establishment assignment needs a scope_id")

    row = CalendarAssignment(company_id=company_id, **body.model_dump())
    db.add(row)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="calendar_assignment", entity_id=row.id,
        action="created",
        payload={
            "calendar": cal.name, "scope_type": row.scope_type,
            "effective_from": row.effective_from.isoformat(),
        },
    )
    return row


@router.get("/resolve", response_model=ResolvedOut, dependencies=ANY_USER)
def resolve(
    employee_id: uuid.UUID,
    on: date,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    """Which calendar applies to THIS employee on THIS date, and what it says.

    The authoritative question. `source` answers "which calendar produced that
    decision", which is what an operator asking why a site has 18 working days
    actually needs.
    """
    employee = db.get(Employee, employee_id)
    if employee is None or employee.deleted_at is not None:
        raise HTTPException(404, "employee not found")

    resolved = service.resolve_for(
        db, company_id=uuid.UUID(cid),
        establishment_id=employee.establishment_id, start=on, end=on,
    )
    return ResolvedOut(
        calendar_id=resolved.calendar_id,
        calendar_name=resolved.name,
        working_days=resolved.working_days,
        source=resolved.source,
        is_working_day=resolved.is_working_day(on),
        is_holiday=resolved.is_holiday(on),
        holiday_name=resolved.holidays.get(on),
    )
