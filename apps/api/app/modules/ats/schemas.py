"""ATS request/response + the AI screening output contract."""
import uuid
from datetime import datetime

from pydantic import BaseModel


class Screening(BaseModel):
    """What the model must return — validated by OutputParser (platform §2)."""

    skills: list[str] = []
    years_experience: float = 0
    education: str = ""
    summary: str = ""
    technical_score: float
    experience_score: float
    education_score: float
    communication_score: float
    overall_ai_score: float  # 0–10


class JobIn(BaseModel):
    title: str
    required_skills: list[str] = []
    preferred_skills: list[str] = []
    min_experience_years: int = 0
    location: str | None = None
    education: str | None = None


class ApplicationOut(BaseModel):
    id: uuid.UUID
    status: str
    tier: str | None
    recommended_action: str | None
    candidate_id: uuid.UUID
    job_opening_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class SlotIn(BaseModel):
    starts_at: datetime
    ends_at: datetime


class ProposeSlotsIn(BaseModel):
    slots: list[SlotIn]


class SlotOut(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: str

    model_config = {"from_attributes": True}


class ProposeSlotsOut(BaseModel):
    booking_token: str
    slots: list[SlotOut]


class BookingSlotsOut(BaseModel):
    """Public view — no application/company internals, just what a candidate needs."""

    job_title: str
    company_name: str
    slots: list[SlotOut]


class BookIn(BaseModel):
    slot_id: uuid.UUID


class BookOut(BaseModel):
    interview_id: uuid.UUID
    scheduled_at: datetime
    status: str
