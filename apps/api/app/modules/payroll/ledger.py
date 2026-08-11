"""Building the payroll input ledger for a period.

The ledger is generated, then corrected, then used. Generation is idempotent:
re-running it replaces the rows it owns (`structure`, `work_facts`) and leaves
every `manual` and `adjustment` row untouched. That asymmetry is the whole
design — regenerating must never destroy a human's knowledge, and a human must
never have to re-enter what the system can derive.

    salary structure ─┐
                      ├─→ payroll inputs ─→ statutory wage ─→ rules ─→ payslip
    approved work ────┘        ▲
    facts                      │
                        manual entries,
                        adjustments
"""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.hr_core.models import Employee
from app.modules.payroll import rules, statutory
from app.modules.payroll.models import PayComponent, SalaryComponent
from app.modules.payroll.workforce import PayrollInput, WorkFact

ZERO = Decimal("0")

#: Overtime and premium-day multipliers. Statutory overtime is twice ordinary
#: wages under the Code on Wages; premium (holiday / weekly-off) work is
#: treated the same here. ponytail: not yet effective-dated or per-state,
#: because unlike PF rates these vary by scheduled employment — lift them into
#: `rules.py` the moment a customer needs a different multiplier.
OVERTIME_MULTIPLIER = Decimal("2.0")
PREMIUM_DAY_MULTIPLIER = Decimal("2.0")

#: Standard hours in a working day, used to convert a monthly wage into an
#: hourly rate for overtime. Configurable per establishment later.
STANDARD_DAY_HOURS = Decimal("8")


def month_bounds(period: date) -> tuple[date, date]:
    import calendar

    first = period.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def inputs_for(
    db: Session, employee_id: uuid.UUID, period: date, *, include_unapproved: bool = False
) -> list[PayrollInput]:
    """Every approved input for one employee for one period.

    Unapproved inputs are deliberately invisible to the engine: an overtime
    claim nobody signed off is not a cost. `include_unapproved` exists so the
    UI can show what is pending, never so payroll can pay it.
    """
    stmt = select(PayrollInput).where(
        PayrollInput.employee_id == employee_id,
        PayrollInput.period == period.replace(day=1),
        PayrollInput.deleted_at.is_(None),
    )
    rows = list(db.scalars(stmt).all())
    if not include_unapproved:
        # Structure-derived rows need no separate approval — approving a salary
        # IS the approval. Everything asserted about a period does.
        rows = [
            r for r in rows
            if r.source == "structure" or r.approved_at is not None
        ]
    return sorted(rows, key=lambda r: (r.sequence, r.code))


def approved_work_facts(
    db: Session, employee_id: uuid.UUID, start: date, end: date
) -> list[WorkFact]:
    return list(
        db.scalars(
            select(WorkFact).where(
                WorkFact.employee_id == employee_id,
                WorkFact.day >= start,
                WorkFact.day <= end,
                WorkFact.approved_at.is_not(None),
                WorkFact.deleted_at.is_(None),
            )
        ).all()
    )


def _replace(db: Session, employee_id: uuid.UUID, period: date, source: str) -> None:
    """Drop the rows this generator owns. Manual and adjustment rows survive."""
    now = datetime.now(UTC)
    for row in db.scalars(
        select(PayrollInput).where(
            PayrollInput.employee_id == employee_id,
            PayrollInput.period == period,
            PayrollInput.source == source,
            PayrollInput.deleted_at.is_(None),
        )
    ).all():
        row.deleted_at = now


def seed_from_structure(
    db: Session, *, company_id: uuid.UUID, employee: Employee, period: date
) -> list[PayrollInput]:
    """Turn the employee's salary structure into this period's inputs.

    The structure is a template. Once written into the ledger the figures
    belong to the period, so a raise on the 20th changes next month without
    silently rewriting this one.
    """
    period = period.replace(day=1)
    _replace(db, employee.id, period, "structure")

    rows = db.execute(
        select(SalaryComponent, PayComponent)
        .join(PayComponent, PayComponent.id == SalaryComponent.component_id)
        .where(
            SalaryComponent.employee_id == employee.id,
            SalaryComponent.deleted_at.is_(None),
            PayComponent.deleted_at.is_(None),
        )
    ).all()

    created: list[PayrollInput] = []
    for salary, component in rows:
        row = PayrollInput(
            company_id=company_id,
            employee_id=employee.id,
            period=period,
            kind="deduction" if component.kind == "deduction" else "earning",
            code=component.code,
            name=component.name,
            amount=statutory.money(salary.amount),
            wage_basis=component.wage_basis,
            source="structure",
            sequence=component.sequence,
        )
        db.add(row)
        created.append(row)
    db.flush()
    return created


def hourly_rate(monthly_wage: Decimal, working_days: int) -> Decimal:
    """A monthly wage expressed per hour, for overtime.

    Uses the period's own working days rather than a fixed 26 or 30, so the
    rate is consistent with how the same month prorates loss of pay. A month
    with no working days yields zero rather than dividing by it.
    """
    if working_days <= 0:
        return ZERO
    return monthly_wage / (Decimal(working_days) * STANDARD_DAY_HOURS)


def seed_from_work_facts(
    db: Session,
    *,
    company_id: uuid.UUID,
    employee: Employee,
    period: date,
    working_days: int,
) -> list[PayrollInput]:
    """Turn approved work facts into payable inputs.

    The engine derives the amount from hours and a multiplier — it never
    accepts an overtime *amount*. That is what makes an overtime policy change
    replayable, and what stops a typo becoming a payment.
    """
    period = period.replace(day=1)
    start, end = month_bounds(period)
    _replace(db, employee.id, period, "work_facts")

    facts = approved_work_facts(db, employee.id, start, end)
    if not facts:
        db.flush()
        return []

    ot_hours = sum((f.overtime_hours for f in facts), start=ZERO)
    premium_days = sum(1 for f in facts if f.premium_day and f.status == "worked")

    # Overtime is paid on ordinary wages — the statutory-wage components, not
    # the whole package, and not the allowances the 50% test may later add back.
    wage_inputs = [
        i for i in inputs_for(db, employee.id, period) if i.wage_basis == "wages"
    ]
    ordinary_wage = sum((i.amount for i in wage_inputs), start=ZERO)
    rate = hourly_rate(ordinary_wage, working_days)

    created: list[PayrollInput] = []
    if ot_hours > ZERO and rate > ZERO:
        created.append(
            PayrollInput(
                company_id=company_id, employee_id=employee.id, period=period,
                kind="overtime", code="OT", name="Overtime",
                quantity=ot_hours, rate=statutory.money(rate * OVERTIME_MULTIPLIER),
                amount=statutory.money(ot_hours * rate * OVERTIME_MULTIPLIER),
                # Overtime counts toward remuneration for the 50% test but is
                # not itself "wages" — the Code is explicit on this.
                wage_basis="excluded",
                source="work_facts", sequence=400,
                approved_at=datetime.now(UTC),
            )
        )
    if premium_days and rate > ZERO:
        daily = rate * STANDARD_DAY_HOURS
        created.append(
            PayrollInput(
                company_id=company_id, employee_id=employee.id, period=period,
                kind="overtime", code="PREMIUM", name="Holiday / weekly-off work",
                quantity=Decimal(premium_days),
                rate=statutory.money(daily * PREMIUM_DAY_MULTIPLIER),
                amount=statutory.money(
                    Decimal(premium_days) * daily * PREMIUM_DAY_MULTIPLIER
                ),
                wage_basis="excluded",
                source="work_facts", sequence=410,
                approved_at=datetime.now(UTC),
            )
        )

    db.add_all(created)
    db.flush()
    return created


def unpaid_days_from_facts(db: Session, employee_id: uuid.UUID, period: date) -> int | None:
    """Days recorded as absent in approved work facts.

    Returns None when no facts exist for the period at all — meaning "this
    company doesn't record work facts", which is different from "nobody was
    absent". The caller falls back to leave-derived loss of pay.
    """
    start, end = month_bounds(period.replace(day=1))
    facts = approved_work_facts(db, employee_id, start, end)
    if not facts:
        return None
    return sum(1 for f in facts if f.status == "absent")


def rebuild(
    db: Session, *, company_id: uuid.UUID, employee: Employee, period: date, working_days: int
) -> list[PayrollInput]:
    """Regenerate the derived half of the ledger, then return the whole thing."""
    seed_from_structure(db, company_id=company_id, employee=employee, period=period)
    seed_from_work_facts(
        db, company_id=company_id, employee=employee, period=period,
        working_days=working_days,
    )
    return inputs_for(db, employee.id, period)


def rebuild_period(
    db: Session, *, company_id: uuid.UUID, period: date, employee_id: uuid.UUID | None = None
) -> dict:
    """Regenerate the derived half of the ledger for a period, without
    computing payroll.

    Exists because looking at the ledger and paying against it are separate
    acts. An operator wants to see what August will consist of — and correct
    it — before anything is calculated, and a payroll run is a heavy and
    consequential way to ask that question.

    Idempotent: derived rows are replaced, manual entries and adjustments are
    left standing. Running it twice produces the same ledger.
    """
    from app.modules.payroll import service

    period = period.replace(day=1)
    _, end = month_bounds(period)
    _, _, working_days = service.calendar_context(db, company_id, period)

    stmt = select(Employee).where(
        Employee.status != "exited", Employee.deleted_at.is_(None)
    )
    if employee_id is not None:
        stmt = stmt.where(Employee.id == employee_id)
    employees = db.scalars(stmt).all()

    rebuilt = 0
    derived = 0
    preserved = 0
    pending = 0
    for employee in employees:
        # Nobody is on the payroll for a month that ended before they joined,
        # so generating inputs for them would be noise in the exception list.
        if employee.joined_on and employee.joined_on > end:
            continue
        rebuild(
            db, company_id=company_id, employee=employee, period=period,
            working_days=working_days,
        )
        rebuilt += 1
        # Counted through the OPERATOR's view, not the engine's. `inputs_for`
        # hides unapproved rows because payroll must not pay them — but an
        # unapproved entry is precisely what somebody needs to go and approve,
        # so reporting it as "gone" would be a lie about their own data.
        rows = inputs_for(db, employee.id, period, include_unapproved=True)
        derived += sum(1 for r in rows if r.source in ("structure", "work_facts"))
        preserved += sum(1 for r in rows if r.source not in ("structure", "work_facts"))
        pending += sum(
            1 for r in rows if r.source != "structure" and r.approved_at is None
        )

    return {
        "period": period,
        "employees": rebuilt,
        "derived": derived,
        "preserved": preserved,
        "pending": pending,
    }


def statutory_wage_from_inputs(
    inputs: list[PayrollInput], period: date
) -> rules.WageBasis:
    """Derive the statutory wage from the ledger rather than the salary record.

    This is why `wage_basis` is carried on every input: overtime and one-off
    allowances have to be able to participate in the 50% test, and they only
    exist in the ledger.
    """
    lines = [
        (i.amount, i.wage_basis)
        for i in inputs
        if i.kind in ("earning", "overtime")
    ]
    return rules.statutory_wage(lines, rules.wage_definition_for(period))
