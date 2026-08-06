"""ATS request/response + the AI screening output contract."""
import uuid

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
