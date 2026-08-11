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

ponytail: runs synchronously. At a few hundred employees that's a second or
two inside the request. Move to a Celery job when a customer's run starts
timing out — the run row already has the status field a job would report into.
"""
import calendar
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.hr_core.models import Employee
from app.modules.leave.models import LeaveRequest, LeaveType
from app.modules.payroll import statutory
from app.modules.payroll.models import (
    PayComponent,
    PayrollRun,
    PayrollSettings,
    Payslip,
    ProfessionalTaxSlab,
    SalaryComponent,
)
from app.modules.work_calendar import service as work_calendar

ZERO = Decimal("0")


class RunFinalized(ValueError):
    """A finalized run can't be changed. Routes map this to a 409."""


def month_bounds(period: date) -> tuple[date, date]:
    first = period.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def get_settings(db: Session, company_id: uuid.UUID) -> PayrollSettings:
    row = db.scalar(select(PayrollSettings).where(PayrollSettings.deleted_at.is_(None)))
    if row is None:
        row = PayrollSettings(company_id=company_id)
        db.add(row)
        db.flush()
    return row


def pt_slabs(db: Session) -> list[tuple[Decimal | None, Decimal]]:
    rows = db.scalars(
        select(ProfessionalTaxSlab).where(ProfessionalTaxSlab.deleted_at.is_(None))
    ).all()
    return [(s.up_to, s.amount) for s in rows]


def unpaid_leave_days(db: Session, employee_id: uuid.UUID, start: date, end: date) -> int:
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
        return 0

    cal = work_calendar.get_calendar(db, requests[0].company_id)
    holidays = set(work_calendar.holidays_between(db, start, end))
    return sum(
        work_calendar.count_working_days(
            max(r.start_date, start), min(r.end_date, end), cal.working_days, holidays
        )
        for r in requests
    )


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


def pre_joining_days(db: Session, company_id: uuid.UUID, employee: Employee, period: date) -> int:
    """Working days in the month before this person joined — days they simply
    aren't owed. Derived from `joined_on` every time, never stored on its own."""
    if not employee.joined_on:
        return 0
    start, end = month_bounds(period)
    if employee.joined_on <= start:
        return 0

    cal = work_calendar.get_calendar(db, company_id)
    holidays = set(work_calendar.holidays_between(db, start, end))
    working_days = work_calendar.count_working_days(start, end, cal.working_days, holidays)
    if employee.joined_on > end:
        return working_days  # not on the payroll for any of this month
    payable = work_calendar.count_working_days(
        employee.joined_on, end, cal.working_days, holidays
    )
    return working_days - payable


def derive_lop(db: Session, *, company_id: uuid.UUID, employee: Employee, period: date) -> int:
    """The system's view of total unpaid days: approved unpaid leave plus any
    days before joining.

    THE single place unpaid days are derived. It previously happened inside
    `compute_payslip`, which *added* the pre-joining shortfall to whatever
    `lop_days` it was handed — so feeding a stored total back in counted the
    shortfall twice. A mid-month joiner's pay silently collapsed to zero the
    first time anyone edited their TDS.
    """
    start, end = month_bounds(period)
    return unpaid_leave_days(db, employee.id, start, end) + pre_joining_days(
        db, company_id, employee, period
    )


def lop_for(
    db: Session, *, company_id: uuid.UUID, employee: Employee, period: date, prior: Payslip | None
) -> int:
    """Unpaid days for a (re)computation, honouring a human's correction.

    An overridden figure is the TOTAL, taken as final — the person typing it is
    looking at the number the payslip displays, and nothing further is added to
    it. Otherwise the system derives it fresh, so a recompute is idempotent.
    """
    if prior is not None and prior.lop_overridden:
        return prior.lop_days
    return derive_lop(db, company_id=company_id, employee=employee, period=period)


def compute_payslip(
    db: Session,
    *,
    company_id: uuid.UUID,
    employee: Employee,
    period: date,
    lop_days: int,
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
    start, end = month_bounds(period)
    cal = work_calendar.get_calendar(db, company_id)
    holidays = set(work_calendar.holidays_between(db, start, end))
    working_days = work_calendar.count_working_days(start, end, cal.working_days, holidays)

    lop_days = max(0, min(lop_days, working_days))
    paid_days = working_days - lop_days
    # A zero-working-day month (every day a declared holiday) would otherwise
    # divide by zero. Treat it as fully paid rather than paying nobody.
    ratio = Decimal(paid_days) / Decimal(working_days) if working_days else Decimal("1")

    rows = db.execute(
        select(SalaryComponent, PayComponent)
        .join(PayComponent, PayComponent.id == SalaryComponent.component_id)
        .where(
            SalaryComponent.employee_id == employee.id,
            SalaryComponent.deleted_at.is_(None),
            PayComponent.deleted_at.is_(None),
        )
    ).all()

    earnings: list[dict] = []
    other_deductions: list[dict] = []
    gross = ZERO
    pf_wage = ZERO
    esi_wage = ZERO

    for salary, component in sorted(rows, key=lambda r: (r[1].sequence, r[1].name)):
        amount = statutory.money(salary.amount * ratio)
        line = {"code": component.code, "name": component.name, "amount": str(amount)}
        if component.kind == "deduction":
            other_deductions.append(line)
            continue
        earnings.append(line)
        gross += amount
        if component.pf_wage:
            pf_wage += amount
        if component.esi_wage:
            esi_wage += amount

    settings = get_settings(db, company_id)

    pf = (
        statutory.provident_fund(
            pf_wage, ceiling=settings.pf_wage_ceiling, on_full_wage=settings.pf_on_full_wage
        )
        if settings.pf_enabled
        else {"employee": ZERO, "employer_epf": ZERO, "employer_eps": ZERO, "wage": ZERO}
    )
    esi_amounts = (
        statutory.esi(
            esi_wage,
            ceiling=settings.esi_wage_ceiling,
            locked_in=esi_locked_in(db, employee.id, period),
        )
        if settings.esi_enabled
        else {"employee": ZERO, "employer": ZERO, "wage": ZERO}
    )
    pt = statutory.professional_tax(gross, pt_slabs(db))

    statutory_deductions = pf["employee"] + esi_amounts["employee"] + pt + tds
    manual = sum(Decimal(d["amount"]) for d in other_deductions)
    deductions = statutory.money(statutory_deductions + manual)
    net = statutory.money(gross - deductions)
    employer_cost = statutory.money(
        gross + pf["employer_epf"] + pf["employer_eps"] + esi_amounts["employer"]
    )

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
                {"code": "ESI_ER", "name": "Employer ESI", "amount": str(esi_amounts["employer"])},
            ],
            "basis": {
                "pf_wage": str(pf["wage"]),
                "esi_wage": str(esi_amounts["wage"]),
                "proration": f"{paid_days}/{working_days} working days",
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

    for employee in employees:
        # Not on the payroll for a month that ended before they joined.
        if employee.joined_on and employee.joined_on > end:
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

        gross_total += computed["gross"]
        deductions_total += computed["deductions"]
        net_total += computed["net"]
        employer_total += computed["employer_cost"]

    run.gross_total = gross_total
    run.deductions_total = deductions_total
    run.net_total = net_total
    run.employer_cost_total = employer_total
    db.flush()
    return run
