"""Working-day arithmetic — the shared answer to "does this date count?".

`count_working_days` is pure so the awkward cases (a leave that's entirely
weekend, a holiday inside the range, a six-day work week) are tested without
a database. Both leave billing and the attendance heatmap route through here
so they can't disagree about what a working day is.
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.work_calendar.models import Holiday, WorkCalendar

DEFAULT_WORKING_DAYS = "1111100"  # Mon–Fri


def is_working_weekday(day: date, working_days: str) -> bool:
    """`working_days` is Monday-first, "1" = worked. A malformed value falls
    back to Mon–Fri rather than making every day a holiday."""
    pattern = working_days if len(working_days) == 7 else DEFAULT_WORKING_DAYS
    return pattern[day.weekday()] == "1"


def count_working_days(
    start: date, end: date, working_days: str, holidays: set[date]
) -> int:
    """Working days in an inclusive range, excluding weekends and holidays.

    This is what a leave request is BILLED. Counting calendar days instead
    (which is what this product did before) charges someone 4 days for a
    Friday-to-Monday absence.
    """
    if end < start:
        return 0
    total = 0
    day = start
    while day <= end:
        if is_working_weekday(day, working_days) and day not in holidays:
            total += 1
        day += timedelta(days=1)
    return total


def get_calendar(db: Session, company_id: uuid.UUID) -> WorkCalendar:
    cal = db.scalar(select(WorkCalendar).where(WorkCalendar.deleted_at.is_(None)))
    if cal is None:
        cal = WorkCalendar(company_id=company_id)
        db.add(cal)
        db.flush()
    return cal


def holidays_between(db: Session, start: date, end: date) -> dict[date, str]:
    rows = db.scalars(
        select(Holiday)
        .where(Holiday.day >= start, Holiday.day <= end, Holiday.deleted_at.is_(None))
        .order_by(Holiday.day)
    ).all()
    return {h.day: h.name for h in rows}


def billable_days(
    db: Session, company_id: uuid.UUID, start: date, end: date
) -> tuple[int, dict[date, str]]:
    """Working days in the range plus the holidays that were excluded — the
    caller usually wants to explain the number, not just print it."""
    cal = get_calendar(db, company_id)
    holidays = holidays_between(db, start, end)
    return count_working_days(start, end, cal.working_days, set(holidays)), holidays
