"""HR Core request/response schemas."""
import uuid
from datetime import date

from pydantic import BaseModel


class DepartmentIn(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None


class DepartmentOut(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    manager_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class EmployeeIn(BaseModel):
    full_name: str
    email: str | None = None
    department_id: uuid.UUID | None = None
    reporting_manager_id: uuid.UUID | None = None
    status: str = "active"
    joined_on: date | None = None


class EmployeeOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    department_id: uuid.UUID | None
    reporting_manager_id: uuid.UUID | None
    status: str
    joined_on: date | None

    model_config = {"from_attributes": True}
