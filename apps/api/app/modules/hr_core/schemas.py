"""HR Core request/response schemas.

The `*Update` schemas exist because PATCH is not POST. Reusing the create
schema for a partial update means every field the caller didn't send arrives
as its default, and writing those defaults back erases real data — see the
route handlers, which apply `exclude_unset=True` so only what was actually
sent is written.
"""
import uuid
from datetime import date

from pydantic import BaseModel, field_validator
from pydantic_core.core_schema import ValidationInfo


class DepartmentIn(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None


class DepartmentUpdate(BaseModel):
    """Every field optional. Omitting one leaves it alone; sending an explicit
    null clears it — which is what PATCH should mean.

    Spelled out rather than subclassing `DepartmentIn`: widening `name` from
    `str` to `str | None` in a subclass is a Liskov violation that mypy is
    right to reject. Keep the field list in step with `DepartmentIn`.
    """

    name: str | None = None
    parent_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _name_not_null(cls, v: str | None) -> str:
        # Only runs when the key was actually present, so this rejects an
        # explicit `"name": null` without affecting an omitted name.
        if v is None:
            raise ValueError("name cannot be null")
        return v


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
    date_of_birth: date | None = None
    pf_first_joined_on: date | None = None
    is_international_worker: bool = False
    uan: str | None = None


class EmployeeUpdate(BaseModel):
    """Every field optional.

    This is not cosmetic. `status` and `joined_on` are read by payroll:
    `joined_on` prorates a mid-month joiner's pay, and `status` is how a run
    excludes leavers. When PATCH reused the create schema, renaming somebody
    nulled their joining date and reset `exited` back to `active` — quietly
    putting a departed employee into the next payroll run.

    Spelled out rather than subclassing `EmployeeIn`, for the same reason as
    `DepartmentUpdate`. Keep the field list in step with `EmployeeIn`.
    """

    full_name: str | None = None
    email: str | None = None
    department_id: uuid.UUID | None = None
    reporting_manager_id: uuid.UUID | None = None
    status: str | None = None
    joined_on: date | None = None
    date_of_birth: date | None = None
    pf_first_joined_on: date | None = None
    # None, not False — a PATCH schema must not imply a default that would be
    # written back. `exclude_unset` drops it when the caller omits it.
    is_international_worker: bool | None = None
    uan: str | None = None

    @field_validator("full_name", "status")
    @classmethod
    def _not_null(cls, v: str | None, info: ValidationInfo) -> str:
        # These two back NOT NULL columns, so "optional for PATCH" must not be
        # read as "nullable". Omitting them is fine; sending an explicit null
        # is a 422 rather than a 500 from the database.
        if v is None:
            raise ValueError(f"{info.field_name} cannot be null")
        return v


class EmployeeOut(BaseModel):
    id: uuid.UUID
    #: Set once self-service access is granted. Lets the UI show "has access"
    #: without a second call; it's an internal id, not a credential.
    user_id: uuid.UUID | None = None
    full_name: str
    email: str | None
    department_id: uuid.UUID | None
    reporting_manager_id: uuid.UUID | None
    status: str
    joined_on: date | None
    date_of_birth: date | None = None
    pf_first_joined_on: date | None = None
    is_international_worker: bool = False
    uan: str | None = None

    model_config = {"from_attributes": True}
