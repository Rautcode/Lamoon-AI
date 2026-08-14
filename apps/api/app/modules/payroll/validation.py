"""Validation and exceptions — three questions, kept apart.

The control centre conflated them, and they are genuinely different:

    READINESS    Can payroll be run at all?      configuration, coverage
    VALIDATION   Are the inputs valid?           blocking errors, warnings
    RISK         Does anything look unusual?     anomalies, money at stake

A missing salary structure is a validation error: it has a right answer and
somebody must supply it. A 41% jump in someone's pay is a risk signal: it may
be entirely correct. Merging them produces a number that means nothing —
"92% ready" with an unexplained ₹1.8L of variance underneath it.

Detection here is DETERMINISTIC. Every finding is a rule over stored figures,
never inference. AI may explain a finding afterwards; it never produces one,
and it never suppresses one.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.hr_core.models import Employee
from app.modules.payroll import ledger
from app.modules.payroll.models import Payslip
from app.modules.payroll.workforce import Establishment, WorkFact

ZERO = Decimal("0")

BLOCKING = "blocking"
WARNING = "warning"
INFO = "info"


@dataclass
class Finding:
    """One thing wrong, or one thing worth a second look.

    `impact` is money at stake where it can be estimated, so an operator
    triages by consequence rather than by count. None means "not quantifiable",
    which is different from zero.
    """

    code: str
    severity: str
    message: str
    employee_id: uuid.UUID | None = None
    employee_name: str | None = None
    impact: Decimal | None = None
    detail: dict = field(default_factory=dict)


# --- validation: are the inputs valid? --------------------------------------


def validate(db: Session, *, company_id: uuid.UUID, period: date) -> list[Finding]:
    """Errors and warnings about the inputs to a run.

    Blocking findings mean a payslip would be wrong, not merely surprising, so
    the affected employee is excluded from the run rather than paid a number
    nobody can defend.
    """
    period = period.replace(day=1)
    findings: list[Finding] = []

    employees = db.scalars(
        select(Employee).where(
            Employee.status != "exited", Employee.deleted_at.is_(None)
        )
    ).all()

    _, month_end = ledger.month_bounds(period)

    for emp in employees:
        inputs = ledger.inputs_for(db, emp.id, period)
        earnings = [i for i in inputs if i.kind in ("earning", "overtime")]

        # Checks that need earnings to exist. Everything after this block is
        # INDEPENDENT and must still run — an early `continue` here would hide
        # a person's unapproved overtime behind their missing salary structure,
        # which is precisely the pair you most want to see together.
        if not earnings:
            findings.append(
                Finding(
                    code="no_salary_structure", severity=BLOCKING,
                    employee_id=emp.id, employee_name=emp.full_name,
                    message="No pay inputs for this period — cannot be calculated",
                )
            )
        else:
            gross = sum((i.amount for i in earnings), start=ZERO)
            if gross <= ZERO:
                findings.append(
                    Finding(
                        code="zero_gross", severity=BLOCKING,
                        employee_id=emp.id, employee_name=emp.full_name,
                        message="Gross pay computes to zero",
                    )
                )

            deductions = sum(
                (i.amount for i in inputs if i.kind in ("deduction", "tax")), start=ZERO
            )
            if deductions > gross:
                findings.append(
                    Finding(
                        code="negative_net", severity=BLOCKING,
                        employee_id=emp.id, employee_name=emp.full_name,
                        impact=deductions - gross,
                        message="Deductions exceed gross — net pay would be negative",
                    )
                )

        # Unapproved work facts are a warning, not an error: payroll can run
        # without them, but somebody is not getting paid for hours they worked.
        # Bounded to THIS month — an unbounded lower/upper range would report
        # next month's pending approvals against this period.
        pending = db.scalars(
            select(WorkFact).where(
                WorkFact.employee_id == emp.id,
                WorkFact.day >= period,
                WorkFact.day <= month_end,
                WorkFact.approved_at.is_(None),
                WorkFact.overtime_hours > 0,
                WorkFact.deleted_at.is_(None),
            )
        ).all()
        if pending:
            hours = sum((f.overtime_hours for f in pending), start=ZERO)
            findings.append(
                Finding(
                    code="overtime_unapproved", severity=WARNING,
                    employee_id=emp.id, employee_name=emp.full_name,
                    message=f"{hours} overtime hours awaiting approval — not being paid",
                    detail={"hours": str(hours), "days": str(len(pending))},
                )
            )

        # Days the bridge could not explain. A WARNING, never blocking: an
        # unexplained absence must not stop payroll, it must be decided. If it
        # blocked, HR would resolve it by clicking whatever cleared the block.
        unexplained = db.scalars(
            select(WorkFact).where(
                WorkFact.employee_id == emp.id,
                WorkFact.day >= period,
                WorkFact.day <= month_end,
                WorkFact.status == "absent",
                WorkFact.approved_at.is_(None),
                WorkFact.deleted_at.is_(None),
            )
        ).all()
        if unexplained:
            days = sorted(f.day.isoformat() for f in unexplained)
            findings.append(
                Finding(
                    code="attendance_unexplained", severity=WARNING,
                    employee_id=emp.id, employee_name=emp.full_name,
                    message=(
                        f"{len(days)} day{'' if len(days) == 1 else 's'} with no punch "
                        "and no leave — regularise, record leave, or confirm as unpaid"
                    ),
                    detail={"days": str(len(days)), "dates": ", ".join(days[:5])},
                )
            )

    findings.extend(_minimum_wage(db, company_id=company_id, period=period))
    return findings


def _minimum_wage(
    db: Session, *, company_id: uuid.UUID, period: date
) -> list[Finding]:
    """Flag pay below the establishment's configured daily floor.

    A WARNING, never an automatic correction. Minimum wages vary by state,
    scheduled employment and skill grade; the system knows the floor it was
    told, not whether that floor is the right one for this worker. Silently
    topping somebody up to a rate that may not apply would be worse than
    saying nothing.
    """
    # Each employee is measured against THEIR OWN establishment's floor. Taking
    # the lowest across the company would clear a Mumbai worker against a rate
    # set for somewhere cheaper, which is the opposite of a safety check.
    establishments = db.scalars(
        select(Establishment).where(Establishment.deleted_at.is_(None))
    ).all()
    floors: dict[uuid.UUID, Decimal] = {
        e.id: e.minimum_daily_wage
        for e in establishments
        if e.minimum_daily_wage is not None
    }
    # Somebody with no establishment is measured against the default one, if
    # that has a floor. No default and no attachment means no check — silence
    # is correct when nobody has said which jurisdiction applies.
    default_floor: Decimal | None = next(
        (
            e.minimum_daily_wage
            for e in establishments
            if e.is_default and e.minimum_daily_wage is not None
        ),
        None,
    )
    if not floors:
        return []

    _, end = ledger.month_bounds(period)
    findings: list[Finding] = []

    for emp in db.scalars(
        select(Employee).where(
            Employee.status != "exited", Employee.deleted_at.is_(None)
        )
    ).all():
        floor = (
            floors.get(emp.establishment_id, default_floor)
            if emp.establishment_id is not None
            else default_floor
        )
        if floor is None:
            continue
        facts = ledger.approved_work_facts(db, emp.id, period, end)
        worked = sum(1 for f in facts if f.status == "worked")
        if worked == 0:
            continue
        inputs = ledger.inputs_for(db, emp.id, period)
        gross = sum(
            (i.amount for i in inputs if i.kind in ("earning", "overtime")), start=ZERO
        )
        daily = gross / Decimal(worked)
        if daily < floor:
            findings.append(
                Finding(
                    code="below_minimum_wage", severity=WARNING,
                    employee_id=emp.id, employee_name=emp.full_name,
                    impact=(floor - daily) * Decimal(worked),
                    message=(
                        f"Daily pay {daily.quantize(Decimal('0.01'))} is below the "
                        f"configured floor of {floor}"
                    ),
                    detail={"daily": str(daily.quantize(Decimal("0.01"))),
                            "floor": str(floor), "days": str(worked)},
                )
            )
    return findings


# --- risk: does anything look unusual? --------------------------------------


#: A month-on-month swing beyond this is worth a human glance. Not a rule of
#: law — a threshold, and a deliberately blunt one.
VARIANCE_THRESHOLD = Decimal("0.30")


def risk(db: Session, *, company_id: uuid.UUID, period: date) -> list[Finding]:
    """Anomalies against the previous finalized period.

    These are not errors. Every one may be perfectly correct — a promotion, a
    month of overtime, a joiner's first full month. The value is that somebody
    looked.
    """
    period = period.replace(day=1)
    prev = (period - __import__("datetime").timedelta(days=1)).replace(day=1)

    previous = {
        p.employee_id: p
        for p in db.scalars(
            select(Payslip).where(
                Payslip.period == prev, Payslip.deleted_at.is_(None)
            )
        ).all()
    }
    if not previous:
        return []

    findings: list[Finding] = []
    for slip in db.scalars(
        select(Payslip).where(Payslip.period == period, Payslip.deleted_at.is_(None))
    ).all():
        was = previous.get(slip.employee_id)
        if was is None or was.gross <= ZERO:
            continue
        change = (slip.gross - was.gross) / was.gross
        if abs(change) >= VARIANCE_THRESHOLD:
            findings.append(
                Finding(
                    code="pay_variance", severity=INFO,
                    employee_id=slip.employee_id, employee_name=slip.employee_name,
                    impact=abs(slip.gross - was.gross),
                    message=(
                        f"Gross pay {'up' if change > 0 else 'down'} "
                        f"{abs(change * 100).quantize(Decimal('1'))}% on last month"
                    ),
                    detail={"previous": str(was.gross), "current": str(slip.gross)},
                )
            )
    return findings


def blocking_employee_ids(findings: list[Finding]) -> set[uuid.UUID]:
    """Who cannot be calculated. The run excludes them and names them."""
    return {
        f.employee_id for f in findings
        if f.severity == BLOCKING and f.employee_id is not None
    }


def summarise(findings: list[Finding]) -> dict:
    """Counts and money at stake, grouped for the control centre."""
    by_code: dict[str, dict] = {}
    for f in findings:
        slot = by_code.setdefault(
            f.code, {"code": f.code, "severity": f.severity, "count": 0, "impact": ZERO}
        )
        slot["count"] += 1
        if f.impact is not None:
            slot["impact"] += f.impact
    return {
        "blocking": sum(1 for f in findings if f.severity == BLOCKING),
        "warnings": sum(1 for f in findings if f.severity == WARNING),
        "info": sum(1 for f in findings if f.severity == INFO),
        "impact": sum((f.impact for f in findings if f.impact is not None), start=ZERO),
        "groups": sorted(by_code.values(), key=lambda g: -g["count"]),
    }
