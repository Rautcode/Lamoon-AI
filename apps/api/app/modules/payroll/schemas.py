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
