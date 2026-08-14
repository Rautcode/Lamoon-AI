"""Which calendar applies to THIS employee on THIS date.

**Owns:** working-day classification — whether a given date is a working day,
a weekly off or a holiday, *for a given employee*, and which calendar decided.
**Consumes:** employee → establishment, read-only (`hr_core`).
**Produces:** `ResolvedCalendar`, the denominator of salary proration, leave
billing and the overtime hourly rate.
**Depended on by:** attendance (day states), leave (billing), payroll
(proration, LOP, OT rate), readiness.
**Correction behaviour:** assignments are effective-dated, so changing a
calendar today cannot change what a past period resolved. Editing the
*holidays inside* a calendar is not effective-dated and does affect a
recomputed DRAFT run — a finalized run is immune, because its payslips froze
their own `working_days`.

The bug this replaces: holidays were unique per `(company, day)`, so a company
had exactly one calendar. Two establishments in different states could not
differ, and working days are the denominator of proration — so it was wrong
pay, quietly, for everybody at one location, every month.

Scope, deliberately narrow: **calendar applicability plus effective dating.**
Not a scheduling engine. The acceptance criterion is that for any employee and
any date the answer is deterministic and attributable, and nothing more.
"""
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.work_calendar.models import CalendarAssignment, Holiday, WorkCalendar

DEFAULT_WORKING_DAYS = "1111100"  # Mon–Fri

#: Most specific wins. `location` and `employee_group` are modelled but not yet
#: assignable — neither entity exists (they arrive with B1/B2). Listing them
#: now means adding one is a row, not a migration.
SCOPE_PRECEDENCE = ("company", "establishment", "location", "employee_group")


@dataclass(frozen=True)
class Assignment:
    """One rule: this calendar, for this scope, over this span."""

    calendar_id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID | None
    effective_from: date
    effective_to: date | None

    def covers(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


@dataclass(frozen=True)
class ResolvedCalendar:
    """The answer, with its provenance attached.

    `source` is not decoration: "which calendar produced that decision" is the
    whole acceptance criterion, and an operator asking why a site has 18
    working days needs it.
    """

    calendar_id: uuid.UUID
    name: str
    working_days: str
    holidays: dict[date, str]
    source: str  # company | establishment | location | employee_group

    def is_holiday(self, day: date) -> bool:
        return day in self.holidays

    def is_working_day(self, day: date) -> bool:
        return is_working_weekday(day, self.working_days) and day not in self.holidays

    def working_days_between(self, start: date, end: date) -> int:
        return count_working_days(start, end, self.working_days, set(self.holidays))


def pick_assignment(
    assignments: list[Assignment], *, establishment_id: uuid.UUID | None, on: date
) -> Assignment | None:
    """The assignment in force for this employee on this date.

    Pure, so precedence and effective dating are tested without a database.

    Specificity beats recency: a company-wide calendar introduced in July must
    not silently take over a site that has had its own since 2020. Within one
    scope the latest applicable start wins, which is how a mid-year change
    works.

    Returns None when nothing applies. **Deliberately not a default** — a
    caller must be able to tell "not configured" from "no holidays", because
    guessing Mon–Fri here is exactly how a six-day site gets quietly wrong pay.
    """
    applicable = [a for a in assignments if a.covers(on)]
    matches = [
        a
        for a in applicable
        if a.scope_type == "company"
        or (a.scope_type == "establishment" and a.scope_id == establishment_id)
        # location / employee_group cannot match until those entities exist.
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda a: (SCOPE_PRECEDENCE.index(a.scope_type), a.effective_from),
    )


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


def default_calendar(db: Session, company_id: uuid.UUID) -> WorkCalendar:
    """The company's own calendar, created with an assignment on first use.

    Creating the calendar without its assignment would leave a company that
    resolves nothing — and a company that resolves nothing is paid for zero
    working days. The two are made together, always.
    """
    cal = db.scalar(
        select(WorkCalendar)
        .where(WorkCalendar.deleted_at.is_(None))
        .order_by(WorkCalendar.created_at, WorkCalendar.id)
    )
    if cal is None:
        cal = WorkCalendar(company_id=company_id)
        db.add(cal)
        db.flush()
    if not db.scalar(
        select(CalendarAssignment).where(
            CalendarAssignment.scope_type == "company",
            CalendarAssignment.deleted_at.is_(None),
        )
    ):
        db.add(
            CalendarAssignment(
                company_id=company_id, calendar_id=cal.id, scope_type="company",
                scope_id=None, effective_from=date(2000, 1, 1),
            )
        )
        db.flush()
        invalidate(db)  # the memo was built before this assignment existed
    return cal


def _cache(db: Session) -> dict:
    """Per-session memo for calendar lookups.

    Resolution is called several times per employee per payroll run — from the
    ledger, from unpaid-leave days, from joiner proration and from
    compute_payslip — and a company's calendars do not change while a run is
    computing. Without this, moving from one company-wide calendar to
    per-employee resolution turned three extra queries per call into a **10×
    slowdown of the payroll suite**, measured: `test_payroll.py` went from
    ~25s to 262s.

    `Session.info` is SQLAlchemy's own place for exactly this, so the memo
    lives and dies with the session — a new request never sees a stale one.
    Writers call `invalidate()`.
    """
    return db.info.setdefault("_work_calendar_memo", {})


def invalidate(db: Session) -> None:
    """Drop the memo. Called after any calendar, holiday or assignment write,
    so a create-then-resolve inside one request sees its own change."""
    db.info.pop("_work_calendar_memo", None)


def _assignments(db: Session) -> list[Assignment]:
    memo = _cache(db)
    if "assignments" not in memo:
        rows = db.scalars(
            select(CalendarAssignment).where(CalendarAssignment.deleted_at.is_(None))
        ).all()
        memo["assignments"] = [
            Assignment(
                calendar_id=r.calendar_id, scope_type=r.scope_type, scope_id=r.scope_id,
                effective_from=r.effective_from, effective_to=r.effective_to,
            )
            for r in rows
        ]
    return list(memo["assignments"])


def _load(
    db: Session, calendar_ids: set[uuid.UUID], start: date, end: date
) -> dict[uuid.UUID, tuple[WorkCalendar, dict[date, str]]]:
    """Calendars and their holidays for a range, in two queries — and memoised,
    because payroll asks for the same period repeatedly."""
    if not calendar_ids:
        return {}
    memo = _cache(db)
    key = ("load", frozenset(calendar_ids), start, end)
    if key in memo:
        return dict(memo[key])
    cals = db.scalars(
        select(WorkCalendar).where(
            WorkCalendar.id.in_(calendar_ids), WorkCalendar.deleted_at.is_(None)
        )
    ).all()
    rows = db.scalars(
        select(Holiday).where(
            Holiday.calendar_id.in_(calendar_ids),
            Holiday.day >= start, Holiday.day <= end,
            Holiday.deleted_at.is_(None),
        )
    ).all()
    by_cal: dict[uuid.UUID, dict[date, str]] = {c.id: {} for c in cals}
    for h in rows:
        by_cal.setdefault(h.calendar_id, {})[h.day] = h.name
    loaded = {c.id: (c, by_cal.get(c.id, {})) for c in cals}
    memo[key] = loaded
    return dict(loaded)


def resolve_for(
    db: Session,
    *,
    company_id: uuid.UUID,
    establishment_id: uuid.UUID | None,
    start: date,
    end: date,
) -> ResolvedCalendar:
    """The calendar in force for one employee over a range.

    Resolved at `start`: a payroll period is one calendar, not a different one
    per day. An assignment that changes mid-period takes effect the following
    period, which is both simpler to explain and what an operator expects when
    they say "from April".
    """
    picked = pick_assignment(
        _assignments(db), establishment_id=establishment_id, on=start
    )
    if picked is None:
        # Nothing assigned — fall back to the company's own calendar, creating
        # it if needed, so a fresh tenant is never paid for zero days.
        cal = default_calendar(db, company_id)
        loaded = _load(db, {cal.id}, start, end)
        _, holidays = loaded[cal.id]
        return ResolvedCalendar(cal.id, cal.name, cal.working_days, holidays, "company")

    loaded = _load(db, {picked.calendar_id}, start, end)
    if picked.calendar_id not in loaded:  # calendar soft-deleted under an assignment
        cal = default_calendar(db, company_id)
        loaded = _load(db, {cal.id}, start, end)
        _, holidays = loaded[cal.id]
        return ResolvedCalendar(cal.id, cal.name, cal.working_days, holidays, "company")

    cal, holidays = loaded[picked.calendar_id]
    return ResolvedCalendar(
        cal.id, cal.name, cal.working_days, holidays, picked.scope_type
    )


def resolve_many(
    db: Session,
    *,
    company_id: uuid.UUID,
    establishment_ids: dict[uuid.UUID, uuid.UUID | None],
    start: date,
    end: date,
) -> dict[uuid.UUID, ResolvedCalendar]:
    """Every employee's calendar for a period, in a bounded number of queries.

    `establishment_ids` maps employee id → establishment id. Payroll resolves a
    whole company at once and must not add a query per person to do it —
    assignments are fetched once and picked in memory.
    """
    assignments = _assignments(db)
    picks = {
        employee_id: pick_assignment(
            assignments, establishment_id=est_id, on=start
        )
        for employee_id, est_id in establishment_ids.items()
    }
    wanted = {p.calendar_id for p in picks.values() if p is not None}
    loaded = _load(db, wanted, start, end)

    fallback: ResolvedCalendar | None = None
    out: dict[uuid.UUID, ResolvedCalendar] = {}
    for employee_id, picked in picks.items():
        if picked is not None and picked.calendar_id in loaded:
            cal, holidays = loaded[picked.calendar_id]
            out[employee_id] = ResolvedCalendar(
                cal.id, cal.name, cal.working_days, holidays, picked.scope_type
            )
            continue
        if fallback is None:
            fallback = resolve_for(
                db, company_id=company_id, establishment_id=None, start=start, end=end
            )
        out[employee_id] = fallback
    return out


def billable_days(
    db: Session,
    company_id: uuid.UUID,
    start: date,
    end: date,
    *,
    establishment_id: uuid.UUID | None = None,
) -> tuple[int, dict[date, str]]:
    """Working days in the range plus the holidays excluded — the caller
    usually wants to explain the number, not just print it.

    Takes an establishment because leave is billed against the calendar of the
    person taking it, not the company's.
    """
    resolved = resolve_for(
        db, company_id=company_id, establishment_id=establishment_id,
        start=start, end=end,
    )
    return resolved.working_days_between(start, end), resolved.holidays
