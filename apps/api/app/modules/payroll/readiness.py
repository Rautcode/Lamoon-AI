"""Readiness — can payroll run at all?

The third of the three questions, and the one most easily confused with the
second:

    READINESS    per COMPANY.   Is this business set up to pay anybody?
    VALIDATION   per EMPLOYEE.  Are this person's inputs valid?
    RISK         per EMPLOYEE.  Does this figure look unusual?

"Nobody has a salary structure" is a readiness problem. "Meera has no salary
structure" is a validation one. Merging them gives an operator a percentage
that moves when one person's record changes, which tells them nothing about
whether the company is configured.

TWO RULES THIS MODULE KEEPS
---------------------------
**A check that cannot be evaluated is UNKNOWN, never passing.** An empty
professional-tax schedule is correct in Delhi and indistinguishable from having
forgotten to enter one. Reporting that as a tick would be a lie of exactly the
kind payroll cannot afford.

**The percentage is never the answer on its own.** One blocking check means
payroll cannot be run correctly however high the number reads, so `blocking` is
returned beside it and callers are expected to lead with the worse of the two.
100% with zero employees is not ready, which is why headcount is itself a
blocking check.
"""
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.hr_core.models import Employee
from app.modules.payroll import ledger
from app.modules.payroll.models import PayComponent, PayrollSettings, ProfessionalTaxSlab
from app.modules.payroll.workforce import Establishment

OK = "ok"
WARNING = "warning"
BLOCKING = "blocking"
#: Cannot be determined from what is stored. Excluded from the percentage
#: rather than counted as a failure — an unanswerable question is not a
#: problem, but it is not a pass either.
UNKNOWN = "unknown"


@dataclass
class Check:
    code: str
    label: str
    status: str
    detail: str
    count: int | None = None


def evaluate(db: Session, *, company_id: uuid.UUID, period: date) -> dict:
    """Configuration and coverage for a period."""
    from app.modules.payroll import service
    from app.modules.work_calendar import service as work_calendar

    period = period.replace(day=1)
    checks: list[Check] = []

    employees = list(
        db.scalars(
            select(Employee).where(
                Employee.status != "exited", Employee.deleted_at.is_(None)
            )
        ).all()
    )
    checks.append(
        Check(
            "employees", "Employees on the payroll",
            OK if employees else BLOCKING,
            f"{len(employees)} active"
            if employees
            else "Nobody to pay — add employees first",
            len(employees),
        )
    )

    components = list(
        db.scalars(select(PayComponent).where(PayComponent.deleted_at.is_(None))).all()
    )
    earnings = [c for c in components if c.kind == "earning"]
    checks.append(
        Check(
            "pay_components", "Pay components",
            OK if earnings else BLOCKING,
            f"{len(earnings)} {'earning' if len(earnings) == 1 else 'earnings'}, "
            f"{len(components) - len(earnings)} "
            f"{'deduction' if len(components) - len(earnings) == 1 else 'deductions'}"
            if earnings
            else "No earnings defined — a salary cannot be built from nothing",
            len(components),
        )
    )

    # COVERAGE, not correctness: who has pay inputs, not whether theirs add up.
    # The per-person version of this question is a validation finding.
    with_pay = sum(
        1
        for e in employees
        if any(
            i.kind in ("earning", "overtime")
            for i in ledger.inputs_for(db, e.id, period)
        )
    )
    missing = len(employees) - with_pay
    if not employees:
        coverage = UNKNOWN
    elif with_pay == 0:
        coverage = BLOCKING
    elif missing:
        coverage = WARNING
    else:
        coverage = OK
    checks.append(
        Check(
            "salary_coverage", "Salary structures", coverage,
            f"{with_pay} of {len(employees)} have pay inputs"
            + (f" — {missing} would be excluded from the run" if missing else "")
            if employees
            else "No employees to cover",
            missing if employees else None,
        )
    )

    # Company-scope on purpose: this is a company-wide readiness view, not
    # anybody's pay. Per-establishment differences surface in validation.
    _, _, working_days = service.calendar_context(db, company_id, period)
    cal = work_calendar.default_calendar(db, company_id)
    checks.append(
        Check(
            "work_calendar", "Working calendar",
            OK if working_days > 0 else BLOCKING,
            f"{working_days} working days this month, pattern {cal.working_days}"
            if working_days
            else "No working days — every day is a holiday or outside the work week",
            working_days,
        )
    )

    settings = db.scalar(
        select(PayrollSettings).where(PayrollSettings.deleted_at.is_(None))
    )
    pf_on = bool(settings and settings.pf_enabled)
    wage_components = [c for c in components if c.wage_basis == "wages"]
    checks.append(
        Check(
            "provident_fund", "Provident fund",
            OK if not pf_on or wage_components else BLOCKING,
            "Not enabled"
            if not pf_on
            else f"On, {len(wage_components)} "
                 f"{'component counts' if len(wage_components) == 1 else 'components count'}"
                 " as wages"
            if wage_components
            else "On, but nothing counts as wages — PF would compute on zero",
            len(wage_components) if pf_on else None,
        )
    )
    checks.append(
        Check(
            "esi", "ESI", OK,
            "Not enabled"
            if not (settings and settings.esi_enabled)
            else f"On, ceiling {settings.esi_wage_ceiling}",
        )
    )

    # Genuinely unanswerable. No schedule is correct in Delhi, Haryana and UP,
    # and identical to having forgotten one.
    slabs = (
        db.scalar(
            select(func.count())
            .select_from(ProfessionalTaxSlab)
            .where(ProfessionalTaxSlab.deleted_at.is_(None))
        )
        or 0
    )
    checks.append(
        Check(
            "professional_tax", "Professional tax",
            OK if slabs else UNKNOWN,
            f"{slabs} {'slab' if slabs == 1 else 'slabs'} configured"
            if slabs
            else "No schedule — no PT will be deducted, which is correct in states "
                 "that do not levy it",
            slabs,
        )
    )

    establishments = list(
        db.scalars(select(Establishment).where(Establishment.deleted_at.is_(None))).all()
    )
    if len(establishments) > 1:
        unassigned = sum(1 for e in employees if e.establishment_id is None)
        checks.append(
            Check(
                "jurisdiction", "Establishment coverage",
                OK if unassigned == 0 else WARNING,
                f"{len(establishments)} establishments, everyone assigned"
                if unassigned == 0
                else f"{unassigned} unassigned — they fall back to the company-wide "
                     "tax schedule, which may be the wrong state",
                unassigned,
            )
        )

    if pf_on and employees:
        # Pension eligibility is ASSUMED when these are missing, and the payslip
        # says so. Worth surfacing because the assumption favours contributing.
        no_dates = sum(
            1
            for e in employees
            if e.date_of_birth is None or e.pf_first_joined_on is None
        )
        checks.append(
            Check(
                "statutory_identity", "Statutory identity",
                OK if no_dates == 0 else WARNING,
                "Date of birth and EPF joining date on record for everyone"
                if no_dates == 0
                else f"{no_dates} {'employee is' if no_dates == 1 else 'employees are'} "
                     "missing a date of birth or EPF joining date — pension "
                     "eligibility is assumed for them",
                no_dates,
            )
        )

    evaluable = [c for c in checks if c.status != UNKNOWN]
    passing = sum(1 for c in evaluable if c.status == OK)
    return {
        "period": period,
        # Rounded DOWN. 99% is not 100%, and payroll is not the place to be
        # generous with a number somebody will read as "fine".
        "percent": int(passing * 100 / len(evaluable)) if evaluable else 0,
        "blocking": sum(1 for c in checks if c.status == BLOCKING),
        "warnings": sum(1 for c in checks if c.status == WARNING),
        "unknown": sum(1 for c in checks if c.status == UNKNOWN),
        "checks": [
            {
                "code": c.code, "label": c.label, "status": c.status,
                "detail": c.detail, "count": c.count,
            }
            for c in checks
        ],
    }
