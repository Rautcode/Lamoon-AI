"""Payroll: salary structures, statutory config, monthly runs, payslips.

Two design rules drive every table here.

**Money is Numeric, never float.** `Numeric(12, 2)` maps to `Decimal`. A float
rupee is a rounding bug waiting for an audit.

**A finalized payslip is a record, not a view.** It stores the employee's name
and the full line-by-line `breakdown` as frozen JSON, so a payslip issued in
April still reads correctly after the person is renamed, moves department,
gets a raise, or leaves. Nothing about a finalized run is re-derived — the
same principle that stops leave history being rewritten when the work
calendar changes.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase

MONEY = Numeric(12, 2)


#: Unique among LIVE rows only. A plain UniqueConstraint would keep the key
#: reserved after a soft delete, so re-adding the same value 500s — see
#: migration 0010, which fixes exactly that bug on three older tables.
def live_unique(name: str, *cols: str) -> Index:
    return Index(name, *cols, unique=True, postgresql_where=text("deleted_at IS NULL"))


class PayComponent(TenantBase):
    """A line that can appear on a payslip — Basic, HRA, Conveyance, and so on.

    The three booleans are the whole reason this is configurable rather than a
    fixed list: they're what the statutory engine reads. "Is this part of PF
    wages?" has no answer the software can infer from a component's name, and
    guessing by string-matching "Basic" would quietly mis-remit PF.
    """

    __tablename__ = "pay_components"
    __table_args__ = (live_unique("uq_pay_component_code", "company_id", "code"),)

    code: Mapped[str] = mapped_column(String(40))  # stable key, e.g. "BASIC"
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="earning")  # earning|deduction
    #: How this component is treated when deriving the STATUTORY WAGE
    #: (rules.WAGE_BASIS_VALUES):
    #:   "wages"    basic, DA, retaining allowance — always in
    #:   "excluded" HRA, conveyance, special allowance, OT — out of wages, but
    #:              counted in the remuneration the 50% test measures against
    #:   "outside"  not remuneration at all (reimbursement of actual expense)
    #: From 21 Nov 2025 the excluded portion above half of remuneration is
    #: added back, so this classification decides real money — see rules.py.
    wage_basis: Mapped[str] = mapped_column(String(10), default="excluded")
    #: Superseded by `wage_basis` and kept so pre-existing structures and any
    #: caller still sending it keep working. `wage_basis` is what the engine
    #: reads; migration 0012 back-fills it from this.
    pf_wage: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Counts toward ESI gross wages. Nearly all earnings do.
    esi_wage: Mapped[bool] = mapped_column(Boolean, default=True)
    taxable: Mapped[bool] = mapped_column(Boolean, default=True)
    sequence: Mapped[int] = mapped_column(Integer, default=100)  # payslip display order


class SalaryComponent(TenantBase):
    """One employee's monthly amount for one component. The salary structure is
    just the set of these rows for a person.

    ponytail: not effective-dated. A raise overwrites the amount, and history
    survives because finalized payslips froze their own breakdown. Add
    effective_from when someone needs to schedule a raise in advance.
    """

    __tablename__ = "salary_components"
    __table_args__ = (
        live_unique("uq_salary_employee_component", "employee_id", "component_id"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pay_components.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY)


class PayrollSettings(TenantBase):
    """One row per company. Rates are statutory and live in `statutory.py`;
    what varies per company is whether a scheme applies at all, and the two
    wage ceilings (which move when the government moves them, so they are data
    rather than constants).
    """

    __tablename__ = "payroll_settings"

    #: PF registration is mandatory at 20+ employees, ESI at 10+. A smaller
    #: company legitimately has neither, so both default OFF: deducting PF from
    #: someone whose employer isn't registered is worse than not deducting.
    pf_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    esi_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pf_wage_ceiling: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("15000"))
    esi_wage_ceiling: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("21000"))
    #: Some employers contribute on the whole PF wage instead of capping at the
    #: ceiling. Both are lawful; only the employer knows which they committed to.
    pf_on_full_wage: Mapped[bool] = mapped_column(Boolean, default=False)


class ProfessionalTaxSlab(TenantBase):
    """Professional tax is levied by STATE, and the slabs differ in every one
    of them — several states don't levy it at all.

    So this is a per-company table the customer fills in from their own state's
    schedule, not a national rule table shipped in code. Shipping twenty
    states' slabs would mean shipping twenty chances to be quietly wrong about
    somebody's statutory deduction, and they change by state budget.
    """

    __tablename__ = "pt_slabs"

    #: The establishment whose state levies this. NULL = a company-wide
    #: schedule, correct and simplest for a single-state customer.
    establishment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("establishments.id"), nullable=True
    )
    #: Inclusive upper bound of monthly gross. NULL = the top, unbounded slab.
    up_to: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)


class PayrollRun(TenantBase):
    """A month's payroll. `period` is the first day of the month.

    draft → finalized, one way. A finalized run is never recomputed: it is what
    was paid.
    """

    __tablename__ = "payroll_runs"
    __table_args__ = (live_unique("uq_payroll_run_period", "company_id", "period"),)

    period: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|finalized
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    gross_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    deductions_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    net_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    #: Gross + employer contributions + `admin_shortfall`. What the month costs.
    employer_cost_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    #: Top-up to reach the EPF administration minimum, which is levied per
    #: ESTABLISHMENT per month. Not attributable to any employee, so it lives
    #: here rather than being smeared across payslips where it would break each
    #: payslip's own arithmetic.
    admin_shortfall: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))


class Payslip(TenantBase):
    __tablename__ = "payslips"
    __table_args__ = (live_unique("uq_payslip_run_employee", "run_id", "employee_id"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payroll_runs.id"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    #: The month, snapshotted from the run for the same reason as the name: a
    #: payslip should be readable on its own, without joining back to anything.
    period: Mapped[date] = mapped_column(Date, index=True)
    #: Snapshotted, not joined. A payslip has to stay readable after the
    #: employee row is renamed or soft-deleted.
    employee_name: Mapped[str] = mapped_column(String(200))

    working_days: Mapped[int] = mapped_column(Integer)
    #: Fractional, because half-day leave exists. `working_days` above stays
    #: whole: it counts days in a calendar, and half a working day is not a
    #: thing a calendar can contain.
    paid_days: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    lop_days: Mapped[Decimal] = mapped_column(Numeric(5, 1), default=0)
    #: True once HR has typed an LOP figure by hand. Recomputing a draft must
    #: not silently discard it — an exit, an unpaid sabbatical, or a correction
    #: is knowledge the system doesn't have.
    lop_overridden: Mapped[bool] = mapped_column(Boolean, default=False)

    gross: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    deductions: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    net: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    employer_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    #: Income tax is NOT computed here — see statutory.py. This is what the
    #: employer (in practice, their CA) says to deduct.
    tds: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    #: ...and where that figure came from. Six months on, somebody will ask why
    #: ₹4,850 was deducted, and "because it was typed in" is not an answer. A
    #: bare amount with no provenance is not auditable.
    tds_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tds_tax_year: Mapped[str | None] = mapped_column(String(9), nullable=True)
    tds_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    tds_provided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    tds_provided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    esi_employee: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    #: Every line, frozen. The payslip renders from this and nothing else.
    breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)
