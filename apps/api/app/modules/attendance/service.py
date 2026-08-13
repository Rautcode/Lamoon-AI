"""Turning a punch ledger into days.

`pair_events` is the heart of the module and is deliberately pure — no DB, no
clock — because it has to survive messy reality: double punch-ins, a missing
check-out at the end of a shift, HR inserting a correction out of order. Every
one of those has a test.

Everything is grouped by the company's LOCAL date (policy.timezone). Grouping
by UTC would file a 2am IST punch under the previous day, which is a silent
data-corruption bug for night shifts in the target market.
"""
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.attendance.models import AttendanceEvent, AttendancePolicy
from app.modules.hr_core.models import Employee
from app.modules.leave.models import LeaveRequest, LeaveType
from app.modules.work_calendar import service as work_calendar


@dataclass(frozen=True)
class Punch:
    kind: str
    at: datetime


@dataclass
class DaySummary:
    day: date
    first_in: datetime | None = None
    last_out: datetime | None = None
    worked_minutes: int = 0
    #: Punched in with no matching out — still on the clock.
    open: bool = False
    late: bool = False
    short: bool = False
    #: Punches that couldn't be paired (e.g. an "out" with no preceding "in").
    anomalies: list[str] = field(default_factory=list)
    #: False for weekends and holidays. Without this an empty cell in the
    #: heatmap can't distinguish a Sunday from a no-show.
    working_day: bool = True
    holiday: str | None = None


def tz_of(policy: AttendancePolicy) -> ZoneInfo:
    try:
        return ZoneInfo(policy.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # A bad tz string must not take attendance down; fall back and carry on.
        return ZoneInfo("UTC")


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


def pair_events(
    punches: list[Punch],
    *,
    day: date,
    tz: ZoneInfo,
    workday_start: time,
    expected_minutes: int,
    grace_minutes: int,
    now: datetime | None = None,
) -> DaySummary:
    """Pair in/out punches into worked time for one local day.

    Rules, chosen so bad data degrades rather than explodes:
      * Consecutive "in"s: the FIRST wins (someone tapping twice shouldn't
        restart their clock and lose the earlier minutes).
      * An "out" with no open "in": recorded as an anomaly, not counted.
      * A trailing "in": the day is still open; worked time counts up to `now`
        so a live view can show hours accruing.
    """
    summary = DaySummary(day=day)
    ordered = sorted(punches, key=lambda p: p.at)
    open_at: datetime | None = None

    for p in ordered:
        if p.kind == "in":
            if open_at is None:
                open_at = p.at
                if summary.first_in is None or p.at < summary.first_in:
                    summary.first_in = p.at
            else:
                summary.anomalies.append(f"duplicate check-in at {p.at.isoformat()}")
        elif p.kind == "out":
            if open_at is None:
                summary.anomalies.append(f"check-out with no check-in at {p.at.isoformat()}")
                continue
            summary.worked_minutes += max(0, int((p.at - open_at).total_seconds() // 60))
            summary.last_out = p.at
            open_at = None

    if open_at is not None:
        summary.open = True
        clock = now or datetime.now(UTC)
        # Only accrue if the clock is actually past the punch — a clock skew or
        # a future-dated correction shouldn't produce negative minutes.
        if clock > open_at:
            summary.worked_minutes += int((clock - open_at).total_seconds() // 60)

    if summary.first_in is not None:
        local_in = summary.first_in.astimezone(tz)
        cutoff = datetime.combine(local_in.date(), workday_start, tzinfo=tz) + timedelta(
            minutes=grace_minutes
        )
        summary.late = local_in > cutoff

    # "Short" is only meaningful once the day is closed — someone mid-shift
    # hasn't worked a short day, they're just not finished.
    summary.short = (
        not summary.open
        and summary.first_in is not None
        and summary.worked_minutes < expected_minutes
    )
    return summary


def get_policy(db: Session, company_id: uuid.UUID) -> AttendancePolicy:
    """One policy per company, created with defaults on first use so the module
    works before anyone configures anything."""
    policy = db.scalar(select(AttendancePolicy).where(AttendancePolicy.deleted_at.is_(None)))
    if policy is None:
        policy = AttendancePolicy(company_id=company_id)
        db.add(policy)
        db.flush()
    return policy


def summaries_for(
    db: Session,
    employee_id: uuid.UUID,
    policy: AttendancePolicy,
    start: date,
    end: date,
    now: datetime | None = None,
) -> list[DaySummary]:
    """Day summaries for one employee across an inclusive local-date range.

    **Every day in the range is returned**, including days with no punches at
    all, each carrying `working_day` and `holiday` from the company work
    calendar. That is the whole point: an empty day is not one thing. A Sunday,
    a public holiday and a genuine no-show are three different facts, and the
    caller can only tell them apart if the empty days are present to be
    annotated. Returning only punched days annotated the days that never needed
    it and omitted the ones that did."""
    tz = tz_of(policy)
    # Widen the UTC window by a day either side: a local day can start before
    # and end after its UTC namesake.
    lo = datetime.combine(start, time.min, tzinfo=tz) - timedelta(days=1)
    hi = datetime.combine(end, time.max, tzinfo=tz) + timedelta(days=1)

    rows = db.scalars(
        select(AttendanceEvent)
        .where(
            AttendanceEvent.employee_id == employee_id,
            AttendanceEvent.at >= lo,
            AttendanceEvent.at <= hi,
            AttendanceEvent.deleted_at.is_(None),
        )
        .order_by(AttendanceEvent.at)
    ).all()

    by_day: dict[date, list[Punch]] = {}
    for r in rows:
        d = local_date(r.at, tz)
        if start <= d <= end:
            by_day.setdefault(d, []).append(Punch(kind=r.kind, at=r.at))

    # Annotate against THIS EMPLOYEE'S calendar so callers can tell a weekend or
    # a public holiday apart from someone simply not showing up — and so a
    # holiday at one establishment is not an absence at another.
    resolved = work_calendar.resolve_for(
        db, company_id=policy.company_id,
        establishment_id=_establishment_of(db, employee_id),
        start=start, end=end,
    )

    out: list[DaySummary] = []
    span = (end - start).days + 1
    for d in (start + timedelta(days=i) for i in range(span)):
        punches = by_day.get(d)
        summary = (
            pair_events(
                punches,
                day=d,
                tz=tz,
                workday_start=policy.workday_start,
                expected_minutes=policy.expected_minutes,
                grace_minutes=policy.grace_minutes,
                now=now,
            )
            if punches
            else DaySummary(day=d)
        )
        summary.holiday = resolved.holidays.get(d)
        summary.working_day = resolved.is_working_day(d)
        # Not working that day means not "short" — you weren't expected in.
        if not summary.working_day:
            summary.short = False
            summary.late = False
        out.append(summary)
    return out


def today_for(
    db: Session, employee_id: uuid.UUID, policy: AttendancePolicy, now: datetime | None = None
) -> DaySummary:
    tz = tz_of(policy)
    today = local_date(now or datetime.now(UTC), tz)
    # summaries_for now returns every day in the range, so a day with no
    # punches still arrives annotated against the calendar.
    return summaries_for(db, employee_id, policy, today, today, now=now)[0]


# One vocabulary for "what happened on this day", used by presence, the
# heatmap, and (next) the payroll bridge that turns days into work facts.
#
# `absent` and `missing_punch` are deliberately different: a missing punch means
# somebody worked and the record is incomplete, absence means they were not
# there. Only one of those is a candidate for loss of pay, and treating them as
# the same is how a payroll system quietly underpays people for a failed
# biometric reader.
DAY_STATES = (
    "present", "absent", "weekly_off", "holiday", "paid_leave", "unpaid_leave",
    "half_day", "missing_punch", "work_from_home", "on_duty",
)


def _establishment_of(db: Session, employee_id: uuid.UUID) -> uuid.UUID | None:
    """One employee's establishment, for calendar resolution.

    A read of hr_core, which attendance already depends on. Batched callers use
    `_establishments_of` instead — this one is for the single-employee path.
    """
    return db.scalar(select(Employee.establishment_id).where(Employee.id == employee_id))


def _establishments_of(
    db: Session, employee_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID | None]:
    if not employee_ids:
        return {}
    rows = db.execute(
        select(Employee.id, Employee.establishment_id).where(Employee.id.in_(employee_ids))
    ).all()
    return {employee_id: est for employee_id, est in rows}


def leave_on(
    db: Session, day: date, employee_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Approved leave covering `day`, as paid_leave/unpaid_leave, in ONE query.

    Batched deliberately: presence asks this for every employee at once, and a
    per-employee lookup here would put a query per person on a page that
    already renders the whole company.
    """
    if not employee_ids:
        return {}
    rows = db.execute(
        select(LeaveRequest.employee_id, LeaveType.paid)
        .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
        .where(
            LeaveRequest.employee_id.in_(employee_ids),
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= day,
            LeaveRequest.end_date >= day,
            LeaveRequest.deleted_at.is_(None),
            LeaveType.deleted_at.is_(None),
        )
    ).all()
    return {employee_id: "paid_leave" if paid else "unpaid_leave" for employee_id, paid in rows}


def states_for_today(
    db: Session,
    *,
    employee_ids: Sequence[uuid.UUID],
    policy: AttendancePolicy,
    now: datetime | None = None,
) -> dict[uuid.UUID, tuple[DaySummary, str]]:
    """Every employee's day summary and day state for today.

    Four queries total regardless of headcount — punches, calendar, holidays,
    leave. The obvious version calls today_for() in a loop, which is three
    queries per person and makes the presence page cost more the more people a
    company has.
    """
    tz = tz_of(policy)
    today = local_date(now or datetime.now(UTC), tz)
    if not employee_ids:
        return {}

    lo = datetime.combine(today, time.min, tzinfo=tz) - timedelta(days=1)
    hi = datetime.combine(today, time.max, tzinfo=tz) + timedelta(days=1)
    events = db.scalars(
        select(AttendanceEvent).where(
            AttendanceEvent.employee_id.in_(employee_ids),
            AttendanceEvent.at >= lo,
            AttendanceEvent.at <= hi,
            AttendanceEvent.deleted_at.is_(None),
        ).order_by(AttendanceEvent.at)
    ).all()

    punches: dict[uuid.UUID, list[Punch]] = {}
    for e in events:
        if local_date(e.at, tz) == today:
            punches.setdefault(e.employee_id, []).append(Punch(kind=e.kind, at=e.at))

    # Calendars per employee, not one for the company: today can be a holiday
    # at one establishment and an ordinary working day at another. Resolved in
    # bulk so the presence page does not cost a query per person.
    calendars = work_calendar.resolve_many(
        db, company_id=policy.company_id,
        establishment_ids=_establishments_of(db, employee_ids),
        start=today, end=today,
    )
    leave = leave_on(db, today, employee_ids)

    out: dict[uuid.UUID, tuple[DaySummary, str]] = {}
    for employee_id in employee_ids:
        mine = punches.get(employee_id)
        summary = (
            pair_events(
                mine, day=today, tz=tz,
                workday_start=policy.workday_start,
                expected_minutes=policy.expected_minutes,
                grace_minutes=policy.grace_minutes,
                now=now,
            )
            if mine
            else DaySummary(day=today)
        )
        resolved = calendars[employee_id]
        summary.holiday = resolved.holidays.get(today)
        working = resolved.is_working_day(today)
        summary.working_day = working
        if not working:
            summary.short = summary.late = False
        out[employee_id] = (
            summary,
            day_state(summary, leave=leave.get(employee_id), today=today),
        )
    return out


def day_state(summary: DaySummary, *, leave: str | None = None, today: date | None = None) -> str:
    """What this day was, in one word.

    Order matters. A holiday is a holiday whether or not somebody punched in;
    approved leave outranks an empty day because the absence is already
    explained; punches outrank leave because a punch is evidence and a leave
    record is an intention somebody may have changed.

    `today` decides whether an unpaired punch-in is somebody still at their
    desk or somebody who forgot to tap out. pair_events cannot know that — it
    is pure and has no clock — so the judgement lives here, where the caller
    knows which day it is looking at.
    """
    if summary.holiday is not None:
        return "holiday"
    if not summary.working_day:
        return "weekly_off"
    if summary.first_in is not None:
        if summary.open and (today is None or summary.day < today):
            # The day is over and the punch never closed. Somebody worked and
            # the record is incomplete — which is NOT absence, and must never
            # cost them a day's pay.
            return "missing_punch"
        return "present"
    if leave is not None:
        return leave
    return "absent"
