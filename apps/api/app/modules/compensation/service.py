"""Resolving compensation for a payroll period.

The question payroll asks is never "what does this person earn" — it is
"what applied to them during THIS period, and for which days of it". A raise
on the 15th means the month has two answers, and picking either one alone is
wrong: the later one overpays the first half, the earlier one underpays the
second.

So resolution returns *segments*, not an amount, and the caller prorates.
"""
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.compensation.models import CompensationLine, CompensationVersion

ZERO = Decimal("0.00")

#: How far back a first salary reaches when nobody said when it started.
#:
#: A first version has no earlier version to conflict with, so dating it early
#: is free — and the alternative is worse: an employee with no recorded joining
#: date whose salary starts "today" resolves to NOTHING for every past period,
#: and payroll pays them zero rather than admitting it does not know.
EPOCH = date(2000, 1, 1)


class OverlappingVersion(Exception):
    """A version already starts on that date. Never silently replace one —
    somebody's pay history is not a field to overwrite."""


@dataclass(frozen=True)
class Segment:
    """One version's slice of a period."""

    version_id: uuid.UUID
    start: date  # clamped to the period
    end: date  # clamped to the period
    effective_from: date  # the version's own dates, for explaining the split
    reason: str


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def month_bounds(period: date) -> tuple[date, date]:
    start = period.replace(day=1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end


def versions_for_period(
    db: Session, *, employee_id: uuid.UUID, period: date
) -> list[Segment]:
    """Every version overlapping the period, clamped to it, in date order.

    Usually one. Two means somebody's pay changed mid-month, which is the case
    this whole model exists to get right.
    """
    start, end = month_bounds(period)
    rows = db.scalars(
        select(CompensationVersion)
        .where(
            CompensationVersion.employee_id == employee_id,
            CompensationVersion.deleted_at.is_(None),
            CompensationVersion.effective_from <= end,
            # An open version (effective_to NULL) runs forever.
            (CompensationVersion.effective_to.is_(None))
            | (CompensationVersion.effective_to >= start),
        )
        .order_by(CompensationVersion.effective_from)
    ).all()

    return [
        Segment(
            version_id=v.id,
            start=max(v.effective_from, start),
            end=min(v.effective_to, end) if v.effective_to else end,
            effective_from=v.effective_from,
            reason=v.reason,
        )
        for v in rows
    ]


def lines_for(
    db: Session, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[CompensationLine]]:
    """Lines for many versions in one query — payroll asks for a whole
    company's worth at once."""
    if not version_ids:
        return {}
    rows = db.scalars(
        select(CompensationLine).where(
            CompensationLine.version_id.in_(version_ids),
            CompensationLine.deleted_at.is_(None),
        )
    ).all()
    out: dict[uuid.UUID, list[CompensationLine]] = {}
    for line in rows:
        out.setdefault(line.version_id, []).append(line)
    return out


def prorate(
    amount: Decimal, *, segment_working_days: int, period_working_days: int
) -> Decimal:
    """A segment's share of a monthly amount, by WORKING days.

    Working days rather than calendar days, because that is how this engine
    prorates everything else and how leave is billed — a system that counts a
    day one way for the balance and another for the money will be asked to
    explain the difference.

    A segment covering the whole period returns the amount EXACTLY, with no
    rounding applied, so the ordinary case (nobody's pay changed) can never
    drift by a paisa.

    A period with NO working days — every day declared a holiday, a shutdown
    month — pays the full amount. The guard is here to avoid dividing by zero,
    not to decide nobody gets paid: a monthly salary is not forfeited because
    the company closed, and returning zero would silently wipe out a month's
    pay for everybody at once.
    """
    if period_working_days <= 0 or segment_working_days >= period_working_days:
        return money(amount)
    return money(amount * Decimal(segment_working_days) / Decimal(period_working_days))


def current_version(
    db: Session, *, employee_id: uuid.UUID, on: date | None = None
) -> CompensationVersion | None:
    """The version in force on a date — today by default.

    Only for showing somebody their current salary. Payroll must never use
    this: it resolves by PERIOD, and "now" is not a payroll period.
    """
    on = on or date.today()
    return db.scalar(
        select(CompensationVersion)
        .where(
            CompensationVersion.employee_id == employee_id,
            CompensationVersion.deleted_at.is_(None),
            CompensationVersion.effective_from <= on,
            (CompensationVersion.effective_to.is_(None))
            | (CompensationVersion.effective_to >= on),
        )
        .order_by(CompensationVersion.effective_from.desc())
        .limit(1)
    )


def create_version(
    db: Session,
    *,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    effective_from: date,
    lines: list[tuple[uuid.UUID, Decimal]],
    reason: str = "revision",
    note: str | None = None,
    created_by: uuid.UUID | None = None,
) -> CompensationVersion:
    """Add a version and re-close the timeline around it.

    Nothing is overwritten. The version starting before this one is closed the
    day before it begins; if a later version already exists, this one is
    bounded by it. That keeps the timeline gapless and non-overlapping by
    construction, which is what the database cannot enforce for us without
    btree_gist (see models.py).
    """
    existing = db.scalars(
        select(CompensationVersion)
        .where(
            CompensationVersion.employee_id == employee_id,
            CompensationVersion.deleted_at.is_(None),
        )
        .order_by(CompensationVersion.effective_from)
    ).all()

    if any(v.effective_from == effective_from for v in existing):
        raise OverlappingVersion(
            f"a compensation version already starts on {effective_from.isoformat()} — "
            "correct that version instead of adding a second one for the same day"
        )

    previous = [v for v in existing if v.effective_from < effective_from]
    later = [v for v in existing if v.effective_from > effective_from]

    version = CompensationVersion(
        company_id=company_id,
        employee_id=employee_id,
        effective_from=effective_from,
        # Bounded by the next version if one exists, otherwise open-ended.
        effective_to=(later[0].effective_from - timedelta(days=1)) if later else None,
        reason=reason,
        note=note,
        created_by=created_by,
    )
    db.add(version)
    db.flush()

    if previous:
        # Close the one that was in force, rather than deleting it. Its dates
        # are what makes an old payslip explicable.
        previous[-1].effective_to = effective_from - timedelta(days=1)

    for component_id, amount in lines:
        db.add(
            CompensationLine(
                company_id=company_id,
                version_id=version.id,
                component_id=component_id,
                amount=money(amount),
            )
        )
    db.flush()
    return version


def overlaps(db: Session, employee_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Any pair of versions whose spans intersect. Should always be empty —
    this exists so a test can assert the invariant the database cannot."""
    rows = db.scalars(
        select(CompensationVersion)
        .where(
            CompensationVersion.employee_id == employee_id,
            CompensationVersion.deleted_at.is_(None),
        )
        .order_by(CompensationVersion.effective_from)
    ).all()
    bad = []
    for earlier, later in zip(rows, rows[1:], strict=False):
        if earlier.effective_to is None or earlier.effective_to >= later.effective_from:
            bad.append((earlier.id, later.id))
    return bad
