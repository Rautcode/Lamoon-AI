"""The company's working calendar: which weekdays are worked, and which dates
are holidays.

Lives in its own module rather than on `attendance_policies` because it isn't
an attendance concern — LEAVE needs it more urgently (billing a weekend as
leave takes real days off someone's balance), and `leave` importing an
*attendance* policy would be a nonsense dependency.

Named `work_calendar`, not `calendar`, so it can't shadow the stdlib module.
"""
from datetime import date

from sqlalchemy import Date, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase


class WorkCalendar(TenantBase):
    """One row per company, created with defaults on first use."""

    __tablename__ = "work_calendars"

    #: Seven chars, Monday-first, "1" = worked. Default is Mon–Fri.
    #: A string rather than a bitmask because "1111100" is legible in a DB
    #: client and in a log line, and this is read far more than it's written.
    #: NOT hardcoded to Mon–Fri anywhere: six-day weeks (Mon–Sat, "1111110")
    #: are common in Indian SMEs, and assuming otherwise would silently
    #: mis-bill leave for a large share of the target market.
    working_days: Mapped[str] = mapped_column(String(7), default="1111100")


class Holiday(TenantBase):
    __tablename__ = "holidays"
    __table_args__ = (
        # Per company, one entry per date — re-adding Diwali shouldn't create
        # a duplicate that double-discounts a leave request. PARTIAL: a
        # soft-deleted holiday must release the date, or re-adding one you
        # removed 500s (migration 0010).
        Index("uq_holiday_company_day", "company_id", "day", unique=True,
              postgresql_where=text("deleted_at IS NULL")),
    )

    day: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(120))
