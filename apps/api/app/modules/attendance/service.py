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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.attendance.models import AttendanceEvent, AttendancePolicy


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
    Days with no punches are omitted — absence is the caller's to interpret
    (a weekend and a no-show look identical here, and this module has no
    holiday calendar to tell them apart)."""
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

    return [
        pair_events(
            punches,
            day=d,
            tz=tz,
            workday_start=policy.workday_start,
            expected_minutes=policy.expected_minutes,
            grace_minutes=policy.grace_minutes,
            now=now,
        )
        for d, punches in sorted(by_day.items())
    ]


def today_for(
    db: Session, employee_id: uuid.UUID, policy: AttendancePolicy, now: datetime | None = None
) -> DaySummary:
    tz = tz_of(policy)
    today = local_date(now or datetime.now(UTC), tz)
    days = summaries_for(db, employee_id, policy, today, today, now=now)
    return days[0] if days else DaySummary(day=today)
