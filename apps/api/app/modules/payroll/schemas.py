"""Payroll request/response schemas.

Money crosses the wire as a JSON number via `Decimal`; pydantic serializes it
without going through float, so a rupee that is exact in Postgres is exact in
the response.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.payroll.rules import WAGE_BASIS_VALUES
from app.modules.payroll.workforce import FACT_SOURCES, INPUT_KINDS, WORK_DAY_STATUSES


class PayComponentIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    kind: str = "earning"
    #: "wages" | "excluded" | "outside" — see rules.WAGE_BASIS_VALUES. Decides
    #: the statutory wage, and therefore real money from 21 Nov 2025.
    wage_basis: str = "excluded"
    pf_wage: bool = False
    esi_wage: bool = True
    taxable: bool = True
    sequence: int = 100

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("earning", "deduction"):
            raise ValueError("kind must be 'earning' or 'deduction'")
        return v

    @field_validator("wage_basis")
    @classmethod
    def _wage_basis(cls, v: str) -> str:
        if v not in WAGE_BASIS_VALUES:
            raise ValueError(f"wage_basis must be one of {', '.join(WAGE_BASIS_VALUES)}")
        return v

    @model_validator(mode="after")
    def _sync_legacy_pf_wage(self) -> "PayComponentIn":
        """`pf_wage` predates `wage_basis`. Honour it when a caller sends only
        the old field, so existing integrations keep working."""
        if self.pf_wage and self.wage_basis == "excluded":
            object.__setattr__(self, "wage_basis", "wages")
        object.__setattr__(self, "pf_wage", self.wage_basis == "wages")
        return self

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        return v.strip().upper()


class PayComponentOut(PayComponentIn):
    id: uuid.UUID
    model_config = {"from_attributes": True}


class SalaryComponentIn(BaseModel):
    component_id: uuid.UUID
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class SalaryStructureIn(BaseModel):
    """A whole structure at once. Replacing the set is the sane unit of edit —
    a salary is a shape, not a bag of independent numbers."""

    components: list[SalaryComponentIn]


class SalaryComponentOut(BaseModel):
    component_id: uuid.UUID
    code: str
    name: str
    kind: str
    amount: Decimal


class SalaryStructureOut(BaseModel):
    employee_id: uuid.UUID
    components: list[SalaryComponentOut]
    monthly_gross: Decimal


class PayrollSettingsIn(BaseModel):
    pf_enabled: bool = False
    esi_enabled: bool = False
    pf_wage_ceiling: Decimal = Field(default=Decimal("15000"), ge=0, max_digits=12,
                                     decimal_places=2)
    esi_wage_ceiling: Decimal = Field(default=Decimal("21000"), ge=0, max_digits=12,
                                      decimal_places=2)
    pf_on_full_wage: bool = False


class PayrollSettingsOut(PayrollSettingsIn):
    id: uuid.UUID
    model_config = {"from_attributes": True}


class PTSlabIn(BaseModel):
    up_to: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class PTSlabOut(PTSlabIn):
    id: uuid.UUID
    model_config = {"from_attributes": True}


class RunIn(BaseModel):
    """`period` is any date in the target month; the day is normalized away."""

    period: date


class PayslipOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    employee_id: uuid.UUID
    employee_name: str
    period: date
    working_days: int
    paid_days: int
    lop_days: int
    lop_overridden: bool
    gross: Decimal
    deductions: Decimal
    net: Decimal
    employer_cost: Decimal
    tds: Decimal
    tds_source: str | None = None
    tds_tax_year: str | None = None
    tds_note: str | None = None
    tds_provided_at: datetime | None = None
    breakdown: dict

    model_config = {"from_attributes": True}


class PayslipAdjustIn(BaseModel):
    """The two numbers the system cannot know: what the CA says to deduct, and
    days unpaid for a reason no leave request records (an exit mid-month, an
    unpaid sabbatical)."""

    lop_days: int | None = Field(default=None, ge=0)
    tds: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    #: Where the TDS figure came from. Recorded alongside the amount so the
    #: deduction can be explained months later.
    tds_source: str | None = Field(default=None, max_length=120)
    tds_tax_year: str | None = Field(default=None, max_length=9)
    tds_note: str | None = None


class RunOut(BaseModel):
    id: uuid.UUID
    period: date
    status: str
    finalized_at: datetime | None
    gross_total: Decimal
    deductions_total: Decimal
    net_total: Decimal
    employer_cost_total: Decimal
    admin_shortfall: Decimal = Decimal("0")

    model_config = {"from_attributes": True}


class RunDetailOut(RunOut):
    payslips: list[PayslipOut]


# --- establishments ---------------------------------------------------------


class EstablishmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    #: ISO 3166-2 subdivision without the country prefix — "MH", "KA", "DL".
    state_code: str = Field(min_length=2, max_length=4)
    pf_establishment_code: str | None = Field(default=None, max_length=30)
    esi_code: str | None = Field(default=None, max_length=30)
    pt_registration: str | None = Field(default=None, max_length=40)
    minimum_daily_wage: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    is_default: bool = False

    @field_validator("state_code")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class EstablishmentOut(EstablishmentIn):
    id: uuid.UUID
    model_config = {"from_attributes": True}


# --- work facts -------------------------------------------------------------


class WorkFactIn(BaseModel):
    """One day for one person. Facts only — never an amount."""

    employee_id: uuid.UUID
    day: date
    status: str = "worked"
    hours_worked: Decimal = Field(default=Decimal("0"), ge=0, le=24, decimal_places=2)
    overtime_hours: Decimal = Field(default=Decimal("0"), ge=0, le=24, decimal_places=2)
    premium_day: bool = False
    night_shift: bool = False
    site: str | None = Field(default=None, max_length=120)
    shift: str | None = Field(default=None, max_length=60)
    source: str = "manual"
    note: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in WORK_DAY_STATUSES:
            raise ValueError(f"status must be one of {', '.join(WORK_DAY_STATUSES)}")
        return v

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if v not in FACT_SOURCES:
            raise ValueError(f"source must be one of {', '.join(FACT_SOURCES)}")
        return v


class WorkFactOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    day: date
    status: str
    hours_worked: Decimal
    overtime_hours: Decimal
    premium_day: bool
    night_shift: bool
    site: str | None
    shift: str | None
    source: str
    note: str | None
    approved_at: datetime | None
    approved_by: uuid.UUID | None

    model_config = {"from_attributes": True}


class ApproveIn(BaseModel):
    """Bulk approval. Ids are explicit — approving "everything shown" depends
    on a filter the server can't see, and would sign off rows nobody read."""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


# --- the payroll input ledger ------------------------------------------------


class PayrollInputIn(BaseModel):
    """A manual input. Derived inputs are generated, never posted."""

    employee_id: uuid.UUID
    period: date
    kind: str
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(max_digits=12, decimal_places=2)
    wage_basis: str = "excluded"
    reason: str | None = None
    sequence: int = 300

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in INPUT_KINDS:
            raise ValueError(f"kind must be one of {', '.join(INPUT_KINDS)}")
        if v == "overtime":
            # Overtime is DERIVED from approved hours and a multiplier. Letting
            # a caller post an amount is exactly the shortcut that makes an
            # overtime policy unreplayable and a typo payable.
            raise ValueError("overtime is derived from work facts, not entered")
        return v

    @field_validator("wage_basis")
    @classmethod
    def _basis(cls, v: str) -> str:
        if v not in WAGE_BASIS_VALUES:
            raise ValueError(f"wage_basis must be one of {', '.join(WAGE_BASIS_VALUES)}")
        return v

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        return v.strip().upper()


class PayrollInputOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    period: date
    kind: str
    code: str
    name: str
    amount: Decimal
    quantity: Decimal | None
    rate: Decimal | None
    wage_basis: str
    source: str
    reason: str | None
    approved_at: datetime | None
    locked: bool
    sequence: int

    model_config = {"from_attributes": True}


# --- validation -------------------------------------------------------------


class FindingOut(BaseModel):
    code: str
    severity: str
    message: str
    employee_id: uuid.UUID | None = None
    employee_name: str | None = None
    impact: Decimal | None = None
    detail: dict = {}


class ValidationOut(BaseModel):
    """Three questions, three answers — never folded into one number."""

    period: date
    blocking: int
    warnings: int
    info: int
    impact: Decimal
    groups: list[dict]
    findings: list[FindingOut]


class AssignEmployeesIn(BaseModel):
    """Attach people to an establishment.

    Explicit ids rather than a filter: assignment decides which state's
    professional tax and minimum wage apply to somebody's pay, so it is not a
    thing to do to "everyone currently on screen".
    """

    employee_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


class AssignmentOut(BaseModel):
    establishment_id: uuid.UUID
    assigned: int
    #: Periods already finalized are untouched — this only affects future runs.
    note: str


class RebuildIn(BaseModel):
    period: date
    #: Narrow to one person. Omit to regenerate everybody's.
    employee_id: uuid.UUID | None = None


class RebuildOut(BaseModel):
    period: date
    employees: int
    #: Rows regenerated from salary structures and approved work facts.
    derived: int
    #: Manual entries and adjustments left standing — regeneration must never
    #: destroy what a person entered.
    preserved: int
    #: Of those, how many are still waiting for approval. Payroll will not pay
    #: them until somebody signs them off.
    pending: int


class ReadinessCheck(BaseModel):
    code: str
    label: str
    #: ok | warning | blocking | unknown. Never colour alone in the UI.
    status: str
    detail: str
    count: int | None = None


class ReadinessOut(BaseModel):
    """Can payroll run at all?

    `percent` is a summary, not the answer. One blocking check means payroll
    cannot be run correctly however high it reads, which is why `blocking`
    travels with it.
    """

    period: date
    percent: int
    blocking: int
    warnings: int
    #: Checks the system cannot evaluate. Excluded from the percentage rather
    #: than counted as passing.
    unknown: int
    checks: list[ReadinessCheck]
