"""Attendance request/response schemas."""
import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, Field


class PunchIn(BaseModel):
    """The time is the SERVER's, never the client's — a self-reported clock is
    not attendance data. HR corrections use `at` on the on-behalf route."""

    kind: str = Field(pattern="^(in|out)$")
    note: str | None = None


class PunchOnBehalfIn(PunchIn):
    employee_id: uuid.UUID
    #: HR fixing a missed punch after the fact.
    at: datetime | None = None


class DaySummaryOut(BaseModel):
    day: date
    first_in: datetime | None
    last_out: datetime | None
    worked_minutes: int
    open: bool
    late: bool
    short: bool
    anomalies: list[str] = []
    working_day: bool = True
    holiday: str | None = None

    # service.DaySummary is a dataclass. FastAPI coerces it when it's the
    # response_model, but NOT when it's nested inside another model built by
    # hand (EmployeeAttendanceOut) — that path validates strictly and 500s.
    model_config = {"from_attributes": True}


class PresenceOut(BaseModel):
    """One row of "who's in today"."""

    employee_id: uuid.UUID
    full_name: str
    #: Is this person at work right now. in | out | absent
    status: str
    #: What this DAY is — service.DAY_STATES. A different question from
    #: `status`: somebody on approved leave is not "absent", they are accounted
    #: for, and payroll must never treat the two the same.
    state: str = "absent"
    #: Name of the holiday, when `state` is holiday. Worth showing: "Diwali"
    #: explains an empty office in a way "holiday" alone does not.
    holiday: str | None = None
    first_in: datetime | None = None
    last_out: datetime | None = None
    worked_minutes: int = 0
    late: bool = False


class PolicyOut(BaseModel):
    workday_start: time
    expected_minutes: int
    grace_minutes: int
    timezone: str

    model_config = {"from_attributes": True}


class PolicyIn(BaseModel):
    workday_start: time
    expected_minutes: int = Field(ge=1, le=24 * 60)
    grace_minutes: int = Field(ge=0, le=240)
    timezone: str


class EmployeeAttendanceOut(BaseModel):
    """One employee's recent days — the heatmap row."""

    employee_id: uuid.UUID
    full_name: str
    days: list[DaySummaryOut]
