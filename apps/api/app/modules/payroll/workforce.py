"""The input side of payroll: establishments, work facts, and the input ledger.

WHY THIS MODULE EXISTS
----------------------
Payroll used to start here:

    employee → current salary → deductions → payslip

which quietly assumes two things that aren't true of Indian payroll. That a
company is one jurisdiction, and that "what someone is paid this month" can be
read off their salary record. Neither survives contact with a second state or
a site worker.

The model is now:

    work facts → payroll input ledger → statutory wage → rules → calculation

**Work facts** are what happened: days worked, overtime hours, a holiday
worked, a night shift. They are evidence, and they are approved by a human
before they can affect money. Attendance punches remain the raw record; a work
fact is the approved *interpretation* of a day, which is a different thing and
belongs to a different person's judgement.

**Payroll inputs** are the ledger. Payroll no longer asks "what is this
employee's salary?" — it asks "what were the approved inputs for this employee
for this period?". Every input carries where it came from, who approved it and
why. That is the difference between a figure you can defend and one you can
only reproduce.

**Establishments** carry jurisdiction. Professional tax, minimum wages and the
statutory registrations are per registered place of business, not per company:
a company with Maharashtra, Karnataka and Delhi establishments has three
different PT answers and one of them is "none".
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase

MONEY = Numeric(12, 2)


class Establishment(TenantBase):
    """A registered place of business.

    Statutory identity lives here rather than on the company because a company
    can hold several registrations, and an employee is covered by the one they
    are attached to. Without this, "which PT schedule applies?" has no correct
    answer for anyone operating in more than one state.
    """

    __tablename__ = "establishments"

    name: Mapped[str] = mapped_column(String(160))
    #: ISO 3166-2 subdivision without the country prefix — "MH", "KA", "DL".
    state_code: Mapped[str] = mapped_column(String(4))
    pf_establishment_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    esi_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    pt_registration: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Daily floor for the lowest skill grade here. Customer-entered for the
    #: same reason PT slabs are: minimum wages are set per state, per scheduled
    #: employment, per skill grade, and revised twice a year. Shipping a rate
    #: table we cannot keep current would be worse than shipping none — this
    #: drives a WARNING, never a silent adjustment to anyone's pay.
    minimum_daily_wage: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


#: What a day was. Deliberately small: these are the distinctions that change
#: money, not a general attendance taxonomy.
WORK_DAY_STATUSES = ("worked", "absent", "weekly_off", "holiday", "leave")

#: Where a fact came from. `attendance` is derived from punches; `manual` is a
#: human assertion; `import` came from a device or a contractor's sheet.
FACT_SOURCES = ("attendance", "manual", "import")


class WorkFact(TenantBase):
    """What happened on one day for one person.

    Facts, never money. A fact says "18 hours of overtime across the month";
    the rate that turns those hours into rupees is a rule, and the amount is an
    input the engine derives. Keeping the three apart is what lets an overtime
    policy change without rewriting history.

    Unapproved facts are visible but do not reach payroll. Overtime that
    nobody signed off is a claim, not a cost.
    """

    __tablename__ = "work_facts"
    __table_args__ = (
        Index(
            "uq_work_fact_employee_day", "employee_id", "day", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="worked")

    hours_worked: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"))
    overtime_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"))
    #: Worked on a declared holiday or the weekly off — usually a higher
    #: multiplier than ordinary overtime, which is why it is its own fact.
    premium_day: Mapped[bool] = mapped_column(Boolean, default=False)
    night_shift: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Where the work happened. Free text for now: sites and shifts deserve
    #: their own tables once a customer has more than a handful.
    site: Mapped[str | None] = mapped_column(String(120), nullable=True)
    shift: Mapped[str | None] = mapped_column(String(60), nullable=True)

    source: Mapped[str] = mapped_column(String(12), default="attendance")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


#: What an input does to the payslip.
INPUT_KINDS = ("earning", "deduction", "overtime", "lop", "adjustment", "tax")

#: How the input got there. `structure` is generated from the salary record;
#: `work_facts` is derived from approved facts; `manual` was typed by a person;
#: `adjustment` is a correction carried from an earlier, finalized period.
INPUT_SOURCES = ("structure", "work_facts", "manual", "import", "adjustment")


class PayrollInput(TenantBase):
    """One approved input, for one employee, for one period.

    THE central change. Payroll reads this ledger instead of reading an
    employee's current salary, so the question it answers becomes "what was
    approved for August?" rather than "what is this person paid today?". Those
    give different answers the moment anyone gets a raise on the 20th.

    Every row carries provenance. A figure with no source is not auditable,
    and an auditable payroll is the entire point of the exercise.
    """

    __tablename__ = "payroll_inputs"
    __table_args__ = (
        Index("ix_payroll_input_period", "employee_id", "period"),
        # One row per employee per period per code per source: regenerating
        # from the salary structure must replace its own rows and leave a
        # human's manual entry standing.
        Index(
            "uq_payroll_input_slot", "employee_id", "period", "code", "source", unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    #: First of the month this input belongs to.
    period: Mapped[date] = mapped_column(Date, index=True)

    kind: Mapped[str] = mapped_column(String(12))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))

    #: The money. For a quantity-based input (overtime hours, LOP days) the
    #: quantity and rate are kept too, so the payslip can show the working
    #: rather than an unexplained total.
    amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(9, 2), nullable=True)
    rate: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)

    #: Mirrors PayComponent.wage_basis so the statutory wage engine can read
    #: the ledger directly — an overtime input has to be able to say it counts
    #: toward remuneration for the 50% test.
    wage_basis: Mapped[str] = mapped_column(String(10), default="excluded")

    source: Mapped[str] = mapped_column(String(12), default="structure")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Set when the period is finalized. A locked input is history.
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Ordering on the payslip, carried from the component.
    sequence: Mapped[int] = mapped_column(Integer, default=100)
