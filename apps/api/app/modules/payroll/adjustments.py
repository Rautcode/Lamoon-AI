"""Correcting a finalized period.

    April  finalized, and wrong
      └─ adjustment, recorded against April, targeted at May
           └─ approved → a payroll input in May, source="adjustment"
                └─ May payslip: "April arrear   2,400"

Never:

    April → edit April

That is the whole reason payroll runs are immutable. April's payslips are what
was paid in April; a mistake found in May does not change what happened, it
creates something owed. Recording it this way keeps both months reconcilable
to what actually left the bank, which an edit does not.

APPROVAL IS WHAT MOVES MONEY
Creating an adjustment writes down a claim. Approving it creates the ledger
row. Splitting the two means somebody can propose a correction without being
able to pay it, which is the point of having the record at all.
"""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.payroll.models import PayrollRun
from app.modules.payroll.workforce import PayrollAdjustment, PayrollInput

ZERO = Decimal("0")


class AdjustmentError(ValueError):
    """Routes map this to a 422."""


def _is_finalized(db: Session, period: date) -> bool:
    return db.scalar(
        select(PayrollRun.id).where(
            PayrollRun.period == period.replace(day=1),
            PayrollRun.status == "finalized",
            PayrollRun.deleted_at.is_(None),
        )
    ) is not None


def validate_periods(db: Session, *, source: date, target: date) -> None:
    """The two rules that make this a correction rather than an edit."""
    source, target = source.replace(day=1), target.replace(day=1)

    if target <= source:
        raise AdjustmentError(
            "the correction must land in a period AFTER the one being corrected"
        )
    if not _is_finalized(db, source):
        raise AdjustmentError(
            f"payroll for {source.strftime('%B %Y')} is not finalized — correct it "
            "there directly rather than raising an adjustment"
        )
    if _is_finalized(db, target):
        raise AdjustmentError(
            f"payroll for {target.strftime('%B %Y')} is already finalized — target "
            "an open period"
        )


def default_name(source: date, kind: str) -> str:
    """What the line reads as on the payslip. It names the month it came from,
    because "arrear 2,400" with no month is a figure nobody can place."""
    return f"{source.strftime('%B %Y')} {'arrear' if kind == 'arrear' else 'recovery'}"


def create(
    db: Session,
    *,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    source_period: date,
    target_period: date,
    kind: str,
    amount: Decimal,
    reason: str,
    code: str | None = None,
    name: str | None = None,
    created_by: uuid.UUID | None = None,
) -> PayrollAdjustment:
    """Record a correction. Creates no money — approval does that."""
    source, target = source_period.replace(day=1), target_period.replace(day=1)
    validate_periods(db, source=source, target=target)
    if amount <= ZERO:
        raise AdjustmentError("amount must be positive — use `kind` for the direction")

    row = PayrollAdjustment(
        company_id=company_id,
        employee_id=employee_id,
        source_period=source,
        target_period=target,
        kind=kind,
        # The code carries the source month so two corrections from different
        # months can coexist in one target period without colliding on the
        # ledger's unique (employee, period, code, source) slot.
        code=code or f"ADJ-{source.strftime('%Y%m')}",
        name=name or default_name(source, kind),
        amount=amount,
        reason=reason,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def approve(
    db: Session, *, adjustment: PayrollAdjustment, approved_by: uuid.UUID
) -> PayrollAdjustment:
    """Agree to settle it, and put it in the ledger.

    Re-validates the periods: a target that was open when the correction was
    raised may have been finalized since, and paying into a closed month would
    silently do nothing.
    """
    if adjustment.approved_at is not None:
        raise AdjustmentError("already approved")
    validate_periods(
        db, source=adjustment.source_period, target=adjustment.target_period
    )

    row = PayrollInput(
        company_id=adjustment.company_id,
        employee_id=adjustment.employee_id,
        period=adjustment.target_period,
        # Direction becomes the input's kind here, at the one point where a
        # signed amount is unambiguous because the record says which it is.
        kind="earning" if adjustment.kind == "arrear" else "deduction",
        code=adjustment.code,
        name=adjustment.name,
        amount=adjustment.amount,
        # An arrear is pay for an earlier month. It is remuneration, but it is
        # not this month's wages — so it sits outside the statutory wage rather
        # than inflating this period's PF basis.
        wage_basis="excluded",
        source="adjustment",
        reason=adjustment.reason,
        created_by=adjustment.created_by,
        approved_by=approved_by,
        approved_at=datetime.now(UTC),
        sequence=600,
    )
    db.add(row)
    db.flush()

    adjustment.approved_by = approved_by
    adjustment.approved_at = row.approved_at
    adjustment.applied_input_id = row.id
    db.flush()
    return adjustment


def cancel(db: Session, *, adjustment: PayrollAdjustment) -> None:
    """Withdraw it, and the money with it.

    Cancelling an approved adjustment removes the ledger row too — leaving the
    input behind would keep paying a correction somebody has retracted. Once
    the target period is finalized it is history, and the only remedy is
    another adjustment in the month after.
    """
    if adjustment.applied_input_id and _is_finalized(db, adjustment.target_period):
        raise AdjustmentError(
            f"this was paid in {adjustment.target_period.strftime('%B %Y')}, which is "
            "finalized — raise a further adjustment to reverse it"
        )

    now = datetime.now(UTC)
    if adjustment.applied_input_id:
        row = db.get(PayrollInput, adjustment.applied_input_id)
        if row is not None and row.deleted_at is None:
            row.deleted_at = now
    adjustment.deleted_at = now


def for_period(
    db: Session, *, target_period: date | None = None, employee_id: uuid.UUID | None = None
) -> list[PayrollAdjustment]:
    stmt = select(PayrollAdjustment).where(PayrollAdjustment.deleted_at.is_(None))
    if target_period is not None:
        stmt = stmt.where(PayrollAdjustment.target_period == target_period.replace(day=1))
    if employee_id is not None:
        stmt = stmt.where(PayrollAdjustment.employee_id == employee_id)
    return list(db.scalars(stmt.order_by(PayrollAdjustment.created_at.desc())).all())
