"""ESS request schemas.

Note what's absent: there is no `employee_id`. That omission is the security
control — an employee cannot express "file this for someone else" even if they
craft the request by hand, because the field doesn't exist in the contract.
"""
import uuid
from datetime import date

from pydantic import BaseModel


class MyLeaveRequestIn(BaseModel):
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    reason: str | None = None
