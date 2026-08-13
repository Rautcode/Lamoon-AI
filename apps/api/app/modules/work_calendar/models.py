"""The company's working calendar: which weekdays are worked, and which dates
are holidays.

Lives in its own module rather than on `attendance_policies` because it isn't
an attendance concern — LEAVE needs it more urgently (billing a weekend as
leave takes real days off someone's balance), and `leave` importing an
*attendance* policy would be a nonsense dependency.

Named `work_calendar`, not `calendar`, so it can't shadow the stdlib module.
"""
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase


class WorkCalendar(TenantBase):
    """A named calendar. A company may have several.

    It used to be one row per company, which is the bug: a Mumbai site and a
    Bengaluru site could not have different holidays, and working days are the
    denominator of salary proration.
    """

    __tablename__ = "work_calendars"

    #: "Maharashtra", "Karnataka — six day", "Head office". Shown to an
    #: operator asking why a site has 18 working days rather than 21.
    name: Mapped[str] = mapped_column(String(120), default="Company calendar")

    #: Seven chars, Monday-first, "1" = worked. Default is Mon–Fri.
    #: A string rather than a bitmask because "1111100" is legible in a DB
    #: client and in a log line, and this is read far more than it's written.
    #: NOT hardcoded to Mon–Fri anywhere: six-day weeks (Mon–Sat, "1111110")
    #: are common in Indian SMEs, and assuming otherwise would silently
    #: mis-bill leave for a large share of the target market.
    working_days: Mapped[str] = mapped_column(String(7), default="1111100")


class Holiday(TenantBase):
    """A date off, **on one calendar**.

    Previously unique per `(company, day)`, which is what made one company mean
    one calendar. Now unique per `(calendar, day)`: 15 August can be a holiday
    on the Maharashtra calendar and a working day on the Karnataka one.
    """

    __tablename__ = "holidays"
    __table_args__ = (
        # One entry per date PER CALENDAR — re-adding Diwali shouldn't create a
        # duplicate that double-discounts a leave request. PARTIAL: a
        # soft-deleted holiday must release the date, or re-adding one you
        # removed 500s (migration 0010).
        Index("uq_holiday_calendar_day", "calendar_id", "day", unique=True,
              postgresql_where=text("deleted_at IS NULL")),
    )

    calendar_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_calendars.id"), index=True
    )
    day: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(120))


class CalendarAssignment(TenantBase):
    """Who uses which calendar, and from when.

    Effective-dated on purpose. A site's calendar changes between years, and
    August 2026's payroll must resolve the assignment that was in force in
    August 2026 — not whatever is current when somebody presses recompute.

    `scope_type` carries `location` and `employee_group` although neither
    entity exists yet (they arrive with B1/B2). Modelling them now means
    adding one later is a row rather than a migration; `pick_assignment`
    simply cannot match them until there is something to match.
    """

    __tablename__ = "calendar_assignments"
    __table_args__ = (
        Index("ix_calendar_assignment_lookup", "company_id", "scope_type", "scope_id"),
    )

    calendar_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_calendars.id"), index=True
    )
    #: company | establishment | location | employee_group
    scope_type: Mapped[str] = mapped_column(String(20), default="company")
    #: NULL for company scope — there is nothing to point at.
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    effective_from: Mapped[date] = mapped_column(Date)
    #: NULL = open-ended.
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
