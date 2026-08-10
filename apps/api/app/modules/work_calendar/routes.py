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
from app.modules.work_calendar import service
from app.modules.work_calendar.models import Holiday
from app.modules.work_calendar.schemas import (
    HolidayIn,
    HolidayOut,
    WorkCalendarIn,
    WorkCalendarOut,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])
ANY_USER = [Depends(current_user)]
CAN_WRITE = [Depends(require("calendar.write"))]


@router.get("/work-week", response_model=WorkCalendarOut, dependencies=ANY_USER)
def get_work_week(db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    return service.get_calendar(db, uuid.UUID(cid))


@router.put("/work-week", response_model=WorkCalendarOut, dependencies=CAN_WRITE)
def set_work_week(
    body: WorkCalendarIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    company_id = uuid.UUID(cid)
    cal = service.get_calendar(db, company_id)
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
    existing = db.scalar(
        select(Holiday).where(Holiday.day == body.day, Holiday.deleted_at.is_(None))
    )
    if existing:
        # Idempotent rename rather than a duplicate row — two entries for the
        # same date would be harmless for billing (holidays are a set) but
        # confusing to look at.
        existing.name = body.name
        db.flush()
        return existing

    holiday = Holiday(company_id=company_id, day=body.day, name=body.name)
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
