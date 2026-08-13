import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer

from app.modules.compensation.models import REASONS


class LineIn(BaseModel):
    component_id: uuid.UUID
    amount: Decimal = Field(ge=0)


class VersionIn(BaseModel):
    effective_from: date
    lines: list[LineIn]
    reason: str = "revision"
    note: str | None = None

    def validated_reason(self) -> str:
        return self.reason if self.reason in REASONS else "revision"


class LineOut(BaseModel):
    component_id: uuid.UUID
    code: str
    name: str
    amount: Decimal

    # Money crosses the wire as a string. A float here would round somebody's
    # salary in the browser.
    @field_serializer("amount")
    def _amount(self, v: Decimal) -> str:
        return str(v)

    model_config = {"from_attributes": True}


class VersionOut(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    effective_from: date
    effective_to: date | None
    reason: str
    note: str | None
    gross: Decimal
    lines: list[LineOut]

    @field_serializer("gross")
    def _gross(self, v: Decimal) -> str:
        return str(v)

    model_config = {"from_attributes": True}
