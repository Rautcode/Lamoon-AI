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
