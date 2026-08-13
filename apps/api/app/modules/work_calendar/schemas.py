"""Work calendar schemas."""
import uuid
from datetime import date

from pydantic import BaseModel, Field, field_validator


class HolidayIn(BaseModel):
    day: date
    name: str = Field(min_length=1, max_length=120)


class HolidayOut(BaseModel):
    id: uuid.UUID
    day: date
    name: str

    model_config = {"from_attributes": True}


class WorkCalendarOut(BaseModel):
    working_days: str

    model_config = {"from_attributes": True}


class WorkCalendarIn(BaseModel):
    #: Monday-first, seven chars, "1" = worked. Validated rather than trusted:
    #: a bad pattern here would silently mis-bill every future leave request.
    working_days: str = Field(min_length=7, max_length=7)

    @field_validator("working_days")
    @classmethod
    def only_binary_digits(cls, v: str) -> str:
        if set(v) - {"0", "1"}:
            raise ValueError('working_days must be 7 characters of "0" or "1", Monday first')
        if "1" not in v:
            raise ValueError("at least one working day is required")
        return v


# --- multiple calendars, and who they apply to -------------------------------


class CalendarIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    working_days: str = Field(min_length=7, max_length=7, default="1111100")

    @field_validator("working_days")
    @classmethod
    def only_binary_digits(cls, v: str) -> str:
        # Spelled out rather than borrowed from WorkCalendarIn: reaching for
        # another model's validator through __func__ is the kind of clever
        # somebody decodes at 3am, and this is four lines.
        if set(v) - {"0", "1"}:
            raise ValueError('working_days must be 7 characters of "0" or "1", Monday first')
        if "1" not in v:
            raise ValueError("at least one working day is required")
        return v


class CalendarOut(BaseModel):
    id: uuid.UUID
    name: str
    working_days: str

    model_config = {"from_attributes": True}


class AssignmentIn(BaseModel):
    calendar_id: uuid.UUID
    #: company | establishment | location | employee_group. The last two are
    #: modelled but not yet assignable — neither entity exists.
    scope_type: str = "company"
    scope_id: uuid.UUID | None = None
    effective_from: date
    effective_to: date | None = None


class AssignmentOut(BaseModel):
    id: uuid.UUID
    calendar_id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID | None
    effective_from: date
    effective_to: date | None

    model_config = {"from_attributes": True}


class ResolvedOut(BaseModel):
    """What applies to one employee on one date, **and which calendar said so**
    — the provenance is the point, not a nicety."""

    calendar_id: uuid.UUID
    calendar_name: str
    working_days: str
    source: str
    is_working_day: bool
    is_holiday: bool
    holiday_name: str | None
