"""Leave request/response schemas."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel


class LeaveTypeIn(BaseModel):
    name: str
    annual_quota: int


class LeaveTypeOut(BaseModel):
    id: uuid.UUID
    name: str
    annual_quota: int

    model_config = {"from_attributes": True}


class LeaveRequestIn(BaseModel):
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    reason: str | None = None


class LeaveRequestOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    days: int
    reason: str | None
    status: str
    decided_by: uuid.UUID | None
    decided_at: datetime | None

    model_config = {"from_attributes": True}


class LeaveBalanceOut(BaseModel):
    leave_type_id: uuid.UUID
    leave_type_name: str
    allocated: int
    used: int
    remaining: int
