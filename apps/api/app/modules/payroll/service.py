"""Computing a month's payroll.

The shape of a run:

    draft  → every active employee gets a payslip, computed
           → HR reviews, corrects LOP or TDS, recomputes as often as they like
    finalized → frozen. Never recomputed, never deleted.

Proration is by WORKING days, not calendar days: a month's pay is divided by
the working days the company's calendar says the month has, and multiplied by
the days actually paid. Both conventions exist in Indian payroll; this one is
chosen because leave is already billed in working days, and a system that
counts a day one way for the balance and another way for the money will be
asked to explain the difference. Both figures are on the payslip so the
arithmetic can be checked by hand.

ponytail: runs synchronously, and costs ~14 queries and ~11ms per employee —
measured, not guessed: 25 → 0.41s, 100 → 1.18s, 300 → 3.35s against a local
Postgres. Linear, every hot column indexed, so the cost is round-trip COUNT,
not scan time; over a real network the per-query latency dominates.

That puts the ceiling near a thousand employees for a request anyone would
wait on. Two fixes in order when a customer gets there: hoist the per-employee
lookups (settings, establishment, PT slabs, rules) out of the loop and prefetch
salary structures and ledger inputs in bulk — that alone should take 14 q/emp
to about 2 — then move the run to a Celery job. The run row already has the
status field a job would report into.
"""
import calendar
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.hr_core.models import Employee
from app.modules.leave.models import LeaveRequest, LeaveType
from app.modules.payroll import ledger, rules, statutory, validation
from app.modules.payroll.models import (
    PayrollRun,
    PayrollSettings,
    Payslip,
    ProfessionalTaxSlab,
)
from app.modules.work_calendar import service as work_calendar

ZERO = Decimal("0")


class RunFinalized(ValueError):
    """A finalized run can't be changed. Routes map this to a 409."""


def month_bounds(period: date) -> tuple[date, date]:
    first = period.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def _jurisdiction_of(db: Session, employee: Employee) -> str | None:
    """The state whose rules apply to this person.

    Their establishment's state code, or None for the centre. The same
    resolution PT already uses — jurisdiction hangs off the establishment, not
    off the company, because a company can sit in several states at once.
    """
    if employee.establishment_id is None:
        return None
    from app.modules.payroll.workforce import Establishment

    est = db.get(Establishment, employee.establishment_id)
    return est.state_code if est is not None else None


def calendar_context(
    db: Session,
    company_id: uuid.UUID,
    period: date,
    *,
    establishment_id: uuid.UUID | None = None,
) -> tuple[str, set[date], int]:
    """The work-week pattern, the month's holidays, and its working-day count —
    **for one employee's establishment**.

    ONE implementation, because several things divide by that count: leave
    proration, mid-month joiner proration, and the overtime hourly rate. A
    second copy would price overtime differently depending on which code path
    reached it, which is the kind of bug nobody finds until an employee does.

    `establishment_id` is what makes it correct rather than merely consistent.
    Omitting it resolves the company calendar, which is right for a
    company-wide view (readiness) and **wrong for anybody's pay** — the
    denominator differs per site, so a Mumbai employee's unpaid day is not
    worth the same as a Bengaluru one's.
    """
    start, end = month_bounds(period)
    resolved = work_calendar.resolve_for(
        db, company_id=company_id, establishment_id=establishment_id,
        start=start, end=end,
    )
    return (
        resolved.working_days,
        set(resolved.holidays),
        resolved.working_days_between(start, end),
    )


def get_settings(db: Session, company_id: uuid.UUID) -> PayrollSettings:
    row = db.scalar(select(PayrollSettings).where(PayrollSettings.deleted_at.is_(None)))
    if row is None:
        row = PayrollSettings(company_id=company_id)
        db.add(row)
        db.flush()
    return row


def pt_slabs(
    db: Session, establishment_id: uuid.UUID | None = None
) -> list[tuple[Decimal | None, Decimal]]:
    """The professional tax schedule that applies to one employee.

    Resolution is deliberately NOT a fallback chain. If the employee is
    attached to an establishment, only that establishment's schedule applies —
    an empty one means no PT, because that IS the answer in Delhi, Haryana and
    UP. Falling back to a company-wide schedule there would deduct Maharashtra's
    tax from a Delhi salary, which is worse than deducting nothing.

    An employee with no establishment gets the company-wide schedule, which is
    the correct and simplest arrangement for a single-state customer.
    """
    stmt = select(ProfessionalTaxSlab).where(ProfessionalTaxSlab.deleted_at.is_(None))
    stmt = stmt.where(
        ProfessionalTaxSlab.establishment_id == establishment_id
        if establishment_id is not None
        else ProfessionalTaxSlab.establishment_id.is_(None)
    )
    return [(s.up_to, s.amount) for s in db.scalars(stmt).all()]


def unpaid_leave_days(
    db: Session, employee_id: uuid.UUID, start: date, end: date
) -> Decimal:
    """Approved leave in the month, on leave types marked unpaid.

    Counted by overlap with the month rather than by the request's own `days`,
    so a request spanning a month boundary is charged to the right months.
    """
    requests = db.scalars(
        select(LeaveRequest)
        .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
        .where(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= end,
            LeaveRequest.end_date >= start,
            LeaveType.paid.is_(False),
            LeaveRequest.deleted_at.is_(None),
            LeaveType.deleted_at.is_(None),
        )
    ).all()
    if not requests:
        return ZERO

    # The employee's OWN calendar, not the company's: a day that is a holiday
    # at their site is not an unpaid day for them.
    establishment_id = db.scalar(
        select(Employee.establishment_id).where(Employee.id == employee_id)
    )
    resolved = work_calendar.resolve_for(
        db, company_id=requests[0].company_id, establishment_id=establishment_id,
        start=start, end=end,
    )
    # A request's own `days` is what it was BILLED — including halves — but a
    # request straddling a month boundary must only cost this month its own
    # share. So bill the overlap in working days, then scale by the fraction
    # the request itself represents: a half-day request over one day is 0.5,
    # not 1, and payroll must deduct exactly that.
    total = ZERO
    for r in requests:
        overlap = resolved.working_days_between(
            max(r.start_date, start), min(r.end_date, end)
        )
        whole = resolved.working_days_between(r.start_date, r.end_date)
        if whole <= 0:
            continue
        total += (Decimal(r.days) * Decimal(overlap) / Decimal(whole)).quantize(
            Decimal("0.1")
        )
    return total


def esi_locked_in(db: Session, employee_id: uuid.UUID, period: date) -> bool:
    """Did this employee already contribute to ESI earlier in this ESI
    contribution period? If so a mid-period raise past the ceiling does not
    stop contributions — they run to the end of the period.

    Only finalized runs count. A draft is a number someone is still editing.
    """
    since = statutory.contribution_period_start(period)
    return db.scalar(
        select(Payslip.id)
        .join(PayrollRun, PayrollRun.id == Payslip.run_id)
        .where(
            Payslip.employee_id == employee_id,
            Payslip.esi_employee > 0,
            PayrollRun.status == "finalized",
            PayrollRun.period >= since,
            PayrollRun.period < period,
            Payslip.deleted_at.is_(None),
        )
        .limit(1)
    ) is not None


def finalized_payslips_for(db: Session, employee_id: uuid.UUID) -> list[Payslip]:
    """An employee's own payslips, FINALIZED runs only.

    ESS reads through here rather than querying Payslip directly, so the
    draft filter can't be forgotten at a call site. A draft payslip is a
    number HR is still editing; showing it to the person as their pay is how
    you get asked why your salary changed overnight.
    """
    return list(
        db.scalars(
            select(Payslip)
            .join(PayrollRun, PayrollRun.id == Payslip.run_id)
            .where(
                Payslip.employee_id == employee_id,
                Payslip.deleted_at.is_(None),
                PayrollRun.status == "finalized",
            )
            .order_by(PayrollRun.period.desc())
        ).all()
    )


def pre_joining_days(
    db: Session, company_id: uuid.UUID, employee: Employee, period: date
) -> Decimal:
    """Working days in the month before this person joined — days they simply
    aren't owed. Derived from `joined_on` every time, never stored on its own."""
    if not employee.joined_on:
        return ZERO
    start, end = month_bounds(period)
    if employee.joined_on <= start:
        return ZERO

    pattern, holidays, working_days = calendar_context(
        db, company_id, period, establishment_id=employee.establishment_id
    )
    if employee.joined_on > end:
        return Decimal(working_days)  # not on the payroll for any of this month
    payable = work_calendar.count_working_days(employee.joined_on, end, pattern, holidays)
    return Decimal(working_days - payable)


def confirmed_absences(db: Session, employee_id: uuid.UUID, start: date, end: date) -> Decimal:
    """Days a human looked at and confirmed as unexplained absence.

    APPROVED absences only. An unapproved one is a question, not a deduction —
    that is the whole safety property of the attendance bridge, and reading
    unapproved facts here would undo it.
    """
    from app.modules.payroll.workforce import WorkFact

    rows = db.scalars(
        select(WorkFact).where(
            WorkFact.employee_id == employee_id,
            WorkFact.day >= start, WorkFact.day <= end,
            WorkFact.status == "absent",
            WorkFact.approved_at.is_not(None),
            WorkFact.deleted_at.is_(None),
        )
    ).all()
    return Decimal(len(rows))


def derive_lop(
    db: Session, *, company_id: uuid.UUID, employee: Employee, period: date
) -> Decimal:
    """The system's view of total unpaid days: approved unpaid leave plus any
    days before joining.

    THE single place unpaid days are derived. It previously happened inside
    `compute_payslip`, which *added* the pre-joining shortfall to whatever
    `lop_days` it was handed — so feeding a stored total back in counted the
    shortfall twice. A mid-month joiner's pay silently collapsed to zero the
    first time anyone edited their TDS.
    """
    start, end = month_bounds(period)
    return (
        unpaid_leave_days(db, employee.id, start, end)
        # A confirmed absence is unpaid — that is what approving one MEANS.
        # Unapproved ones are deliberately absent from this sum: they are a
        # question, and reading them here would undo the bridge's whole safety
        # property.
        + confirmed_absences(db, employee.id, start, end)
        + pre_joining_days(db, company_id, employee, period)
    )


def lop_for(
    db: Session, *, company_id: uuid.UUID, employee: Employee, period: date, prior: Payslip | None
) -> Decimal:
    """Unpaid days for a (re)computation, honouring a human's correction.

    An overridden figure is the TOTAL, taken as final — the person typing it is
    looking at the number the payslip displays, and nothing further is added to
    it. Otherwise the system derives it fresh, so a recompute is idempotent.
    """
    if prior is not None and prior.lop_overridden:
        return Decimal(prior.lop_days)
    return derive_lop(db, company_id=company_id, employee=employee, period=period)


def compute_payslip(
    db: Session,
    *,
    company_id: uuid.UUID,
    employee: Employee,
    period: date,
    lop_days: Decimal,
    tds: Decimal,
) -> dict:
    """The whole calculation for one person, as a plain dict.

    Returned rather than written so the same code path serves a preview and a
    saved draft, and so it can be tested without a run row.

    `lop_days` is the FINAL total of unpaid days, including any pre-joining
    days — this function adds nothing to it. That is what makes it a pure
    function of its arguments, and therefore idempotent: feeding its own output
    back in produces the same result. Use `lop_for()` to obtain the value.
    """
    start, _ = month_bounds(period)
    _, _, working_days = calendar_context(
        db, company_id, period, establishment_id=employee.establishment_id
    )
    # Clamped in Decimal: a half-day must survive the clamp, and comparing a
    # Decimal against an int working-day count is exact either way.
    lop_days = max(ZERO, min(Decimal(lop_days), Decimal(working_days)))
    paid_days = Decimal(working_days) - lop_days
    # A zero-working-day month (every day a declared holiday) would otherwise
    # divide by zero. Treat it as fully paid rather than paying nobody.
    ratio = Decimal(paid_days) / Decimal(working_days) if working_days else Decimal("1")

    # Payroll asks the ledger what was APPROVED for this period, not what the
    # employee's salary record says today. A raise on the 20th therefore
    # changes next month rather than silently rewriting this one.
    inputs = ledger.inputs_for(db, employee.id, period)

    earnings: list[dict] = []
    other_deductions: list[dict] = []
    wage_lines: list[tuple[Decimal, str]] = []
    gross = ZERO
    esi_wage = ZERO
    entered_tax = ZERO

    for item in inputs:
        # Overtime is paid for hours actually worked, so it is NOT prorated by
        # attendance a second time — the hours already encode the attendance.
        prorate = item.kind == "earning"
        amount = statutory.money(item.amount * ratio) if prorate else statutory.money(item.amount)
        line = {"code": item.code, "name": item.name, "amount": str(amount),
                "source": item.source}
        if item.quantity is not None and item.rate is not None:
            line["basis"] = f"{item.quantity} x {item.rate}"

        if item.kind == "tax":
            entered_tax += amount
            continue
        if item.kind in ("deduction", "lop", "adjustment") and item.kind != "adjustment":
            other_deductions.append(line)
            continue
        if item.kind == "adjustment":
            (earnings if amount >= ZERO else other_deductions).append(line)
            if amount >= ZERO:
                gross += amount
                wage_lines.append((amount, item.wage_basis))
            continue

        earnings.append(line)
        gross += amount
        wage_lines.append((amount, item.wage_basis))
        # Nearly all earnings count toward ESI gross wages; overtime is
        # excluded when deciding COVERAGE but included once covered, which the
        # ceiling test below handles by using the wage at structure level.
        esi_wage += amount

    # The statutory wage is DERIVED, not nominated. From 21 Nov 2025 an
    # employer can't shrink their PF liability by moving pay into allowances:
    # excluded pay above half of remuneration is added back.
    #
    # Asked PER STATUTE and per jurisdiction, because the Codes were not
    # notified all at once — state rules follow the Centre separately, so a
    # Maharashtra establishment can sit on a different basis from a Karnataka
    # one in the same run.
    jurisdiction = _jurisdiction_of(db, employee)
    basis = rules.basis_for(
        wage_lines, statute="epf", jurisdiction=jurisdiction, period=period
    )
    wage_def = rules.definition_for("epf", jurisdiction=jurisdiction, period=period)
    pf_wage = basis.statutory_wage
    epf_rule = rules.epf_rule_for(period)
    esi_rule = rules.esi_rule_for(period)

    settings = get_settings(db, company_id)

    # EPS is not universal. Decide it from the employee's own record rather
    # than assuming an 8.33/3.67 split for everyone.
    eps = rules.eps_decision(
        period=period, rule=epf_rule, pf_wage=pf_wage,
        ceiling=settings.pf_wage_ceiling,
        date_of_birth=employee.date_of_birth,
        pf_first_joined_on=employee.pf_first_joined_on,
    )
    pf = (
        statutory.provident_fund(
            pf_wage, ceiling=settings.pf_wage_ceiling, rule=epf_rule,
            # An international worker has no wage ceiling at all.
            on_full_wage=settings.pf_on_full_wage or employee.is_international_worker,
            eps_eligible=eps.eligible,
        )
        if settings.pf_enabled
        else {
            "employee": ZERO, "employer_epf": ZERO, "employer_eps": ZERO,
            "employer_edli": ZERO, "employer_admin": ZERO, "wage": ZERO,
        }
    )
    esi_amounts = (
        statutory.esi(
            esi_wage,
            ceiling=settings.esi_wage_ceiling,
            rule=esi_rule,
            locked_in=esi_locked_in(db, employee.id, period),
        )
        if settings.esi_enabled
        else {"employee": ZERO, "employer": ZERO, "wage": ZERO}
    )
    # Jurisdiction comes from the employee's establishment, not the company.
    pt = statutory.professional_tax(gross, pt_slabs(db, employee.establishment_id))

    statutory_deductions = pf["employee"] + esi_amounts["employee"] + pt + tds + entered_tax
    manual = sum(Decimal(d["amount"]) for d in other_deductions)
    deductions = statutory.money(statutory_deductions + manual)
    net = statutory.money(gross - deductions)
    employer_contributions = (
        pf["employer_epf"] + pf["employer_eps"] + pf["employer_edli"]
        + pf["employer_admin"] + esi_amounts["employer"]
    )
    employer_cost = statutory.money(gross + employer_contributions)

    return {
        "employee_id": employee.id,
        "employee_name": employee.full_name,
        "period": start,
        "working_days": working_days,
        "paid_days": paid_days,
        "lop_days": lop_days,
        "gross": gross,
        "deductions": deductions,
        "net": net,
        "employer_cost": employer_cost,
        "tds": tds,
        "esi_employee": esi_amounts["employee"],
        "employer_admin": pf["employer_admin"],
        "breakdown": {
            "earnings": earnings,
            "deductions": [
                {"code": "EPF", "name": "Provident Fund", "amount": str(pf["employee"])},
                {"code": "ESI", "name": "ESI", "amount": str(esi_amounts["employee"])},
                {"code": "PT", "name": "Professional Tax", "amount": str(pt)},
                {"code": "TDS", "name": "Income Tax (TDS)", "amount": str(tds)},
                *other_deductions,
            ],
            # Not deducted from the employee — shown because it's what the
            # month actually costs, and because an employee is entitled to see
            # the PF being remitted in their name.
            "employer_contributions": [
                {"code": "EPF_ER", "name": "Employer PF", "amount": str(pf["employer_epf"])},
                {"code": "EPS_ER", "name": "Employer Pension", "amount": str(pf["employer_eps"])},
                {"code": "EDLI_ER", "name": "EDLI (insurance)",
                 "amount": str(pf["employer_edli"])},
                {"code": "ADMIN_ER", "name": "PF admin charges",
                 "amount": str(pf["employer_admin"])},
                {"code": "ESI_ER", "name": "Employer ESI", "amount": str(esi_amounts["employer"])},
            ],
            "basis": {
                "pf_wage": str(pf["wage"]),
                "esi_wage": str(esi_amounts["wage"]),
                "proration": f"{paid_days}/{working_days} working days",
                # How the statutory wage was arrived at. Without this a payslip
                # can only be explained by reconstructing the whole database.
                "statutory_wage": str(basis.statutory_wage),
                "nominated_wages": str(basis.nominated_wages),
                "excluded_allowances": str(basis.excluded),
                "remuneration": str(basis.remuneration),
                "added_back": str(basis.added_back),
                # Why the employer's 12% split the way it did.
                "eps": eps.reason,
            },
            # The rules this payslip was computed under, resolved by PERIOD.
            # Re-running a corrected month years later must use these, not
            # whatever is current then.
            "rule_versions": {
                "wage_definition": wage_def.version,
                "epf": epf_rule.version,
                "esi": esi_rule.version,
            },
        },
    }


def build_run(db: Session, *, company_id: uuid.UUID, run: PayrollRun) -> PayrollRun:
    """(Re)compute every payslip in a draft run.

    Existing payslips are updated in place, keeping any LOP or TDS a human
    typed — recomputing must never quietly throw away a correction.
    """
    if run.status == "finalized":
        raise RunFinalized("this run is finalized and cannot be recomputed")

    _, end = month_bounds(run.period)
    employees = db.scalars(
        select(Employee).where(Employee.status != "exited", Employee.deleted_at.is_(None))
    ).all()
    existing = {
        p.employee_id: p
        for p in db.scalars(
            select(Payslip).where(Payslip.run_id == run.id, Payslip.deleted_at.is_(None))
        ).all()
    }

    gross_total = deductions_total = net_total = employer_total = ZERO
    admin_charged = ZERO
    contributing_members = 0
    paid: set[uuid.UUID] = set()

    # The ledger is regenerated from salary structures and approved work facts
    # before anything is computed. Manual entries and adjustments survive.
    _, _, month_working_days = calendar_context(db, company_id, run.period)
    for employee in employees:
        if employee.joined_on and employee.joined_on > end:
            continue
        ledger.rebuild(
            db, company_id=company_id, employee=employee, period=run.period,
            working_days=month_working_days,
        )

    # Validation decides who can be calculated at all. Someone with no inputs
    # is EXCLUDED and named, never paid a number nobody can defend.
    findings = validation.validate(db, company_id=company_id, period=run.period)
    blocked = validation.blocking_employee_ids(findings)

    for employee in employees:
        # Not on the payroll for a month that ended before they joined.
        if employee.joined_on and employee.joined_on > end:
            continue
        if employee.id in blocked:
            continue
        prior = existing.get(employee.id)
        lop = lop_for(
            db, company_id=company_id, employee=employee, period=run.period, prior=prior
        )
        tds = prior.tds if prior is not None else ZERO

        computed = compute_payslip(
            db, company_id=company_id, employee=employee, period=run.period,
            lop_days=lop, tds=tds,
        )
        slip = prior or Payslip(company_id=company_id, run_id=run.id, employee_id=employee.id)
        for field in (
            "employee_name", "period", "working_days", "paid_days", "lop_days",
            "gross", "deductions", "net", "employer_cost", "esi_employee", "breakdown",
        ):
            setattr(slip, field, computed[field])
        if prior is None:
            db.add(slip)
        paid.add(employee.id)

        gross_total += computed["gross"]
        deductions_total += computed["deductions"]
        net_total += computed["net"]
        employer_total += computed["employer_cost"]
        admin_charged += computed["employer_admin"]
        if computed["employer_admin"] > ZERO:
            contributing_members += 1

    # Anyone who had a payslip but is no longer eligible loses it. Without
    # this, recomputing UPDATES and CREATES but never REMOVES: exit somebody
    # mid-draft, recompute, and their payslip sits there at full pay and counts
    # toward the totals. A leaver kept being paid.
    now = datetime.now(UTC)
    for employee_id, slip in existing.items():
        if employee_id not in paid:
            slip.deleted_at = now

    # The EPF administration minimum is per establishment per month, so it can
    # only be settled once every payslip is known.
    settings = get_settings(db, company_id)
    run.admin_shortfall = (
        statutory.admin_shortfall_for(
            admin_charged,
            rule=rules.epf_rule_for(run.period),
            has_members=contributing_members > 0,
        )
        if settings.pf_enabled
        else ZERO
    )

    run.gross_total = gross_total
    run.deductions_total = deductions_total
    run.net_total = net_total
    run.employer_cost_total = statutory.money(employer_total + run.admin_shortfall)
    db.flush()
    return run
