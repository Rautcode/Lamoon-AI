"""Leave request/response schemas."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel


class LeaveTypeIn(BaseModel):
    name: str
    annual_quota: int
    paid: bool = True  # False => loss of pay in payroll
    #: Comp-off is EARNED by working a day off, so a policy cannot allocate it
    #: and `annual_quota` is ignored for this type.
    comp_off: bool = False


class LeavePolicyIn(BaseModel):
    leave_type_id: uuid.UUID
    scope_type: str = "company"
    scope_id: uuid.UUID | None = None
    scope_value: str | None = None
    annual_days: float = 0
    accrual_method: str = "annual"
    prorate_on_joining: bool = True
    prorate_on_exit: bool = True
    accrue_during_probation: bool = True
    allow_negative_balance: bool = False
    encashable: bool = False
    carry_forward_max: float | None = None
    carry_forward_expires_months: int | None = None
    effective_from: date
    effective_to: date | None = None


class LeavePolicyOut(LeavePolicyIn):
    id: uuid.UUID

    model_config = {"from_attributes": True}


class LeaveTypeOut(BaseModel):
    id: uuid.UUID
    name: str
    annual_quota: int
    paid: bool

    model_config = {"from_attributes": True}


class LeaveRequestIn(BaseModel):
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    reason: str | None = None
    #: A half day off. Only meaningful for a single-day request — half of a
    #: five-day absence is not a thing anybody means, and letting it through
    #: would bill 2.5 days for a week away.
    half_day: bool = False


class LeaveRequestOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    days: float
    reason: str | None
    status: str
    decided_by: uuid.UUID | None
    decided_at: datetime | None

    model_config = {"from_attributes": True}


class LeaveBalanceOut(BaseModel):
    leave_type_id: uuid.UUID
    leave_type_name: str
    allocated: float
    used: float
    remaining: float
