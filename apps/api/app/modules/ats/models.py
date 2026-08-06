"""ATS tables (ARCH §3). All inherit TenantBase → uuid PK, company_id, audit cols.
RLS policies are added in the migration, not here."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantBase


class JobOpening(TenantBase):
    __tablename__ = "job_openings"
    title: Mapped[str] = mapped_column(String(200))
    required_skills: Mapped[list] = mapped_column(JSONB, default=list)
    preferred_skills: Mapped[list] = mapped_column(JSONB, default=list)
    min_experience_years: Mapped[int] = mapped_column(Integer, default=0)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    education: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")


class Candidate(TenantBase):
    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint("company_id", "resume_sha256", name="uq_candidate_resume"),
    )
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resume_blob_key: Mapped[str] = mapped_column(String(400))
    resume_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    resume_sha256: Mapped[str] = mapped_column(String(64), index=True)  # dedup + AI cache key
    # extracted_text: stored once → no repeat OCR/parse (cost lever)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class Application(TenantBase):
    __tablename__ = "applications"
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"))
    job_opening_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_openings.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(20), default="webhook")
    status: Mapped[str] = mapped_column(String(20), default="received")
    tier: Mapped[str | None] = mapped_column(String(1), nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # screened_at anchors the 10-day auto-reject window (Tier C/D); rejected_at
    # records when the scheduled job actually rejected the application.
    screened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIAnalysis(TenantBase):
    __tablename__ = "ai_analyses"
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"))
    resume_sha256: Mapped[str] = mapped_column(String(64), index=True)
    recipe_hash: Mapped[str] = mapped_column(String(64))
    extracted: Mapped[dict] = mapped_column(JSONB, default=dict)
    technical_score: Mapped[float] = mapped_column(Float)
    experience_score: Mapped[float] = mapped_column(Float)
    education_score: Mapped[float] = mapped_column(Float)
    communication_score: Mapped[float] = mapped_column(Float)
    overall_ai_score: Mapped[float] = mapped_column(Float)
    job_match_pct: Mapped[float] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float)
    matched_skills: Mapped[list] = mapped_column(JSONB, default=list)
    missing_skills: Mapped[list] = mapped_column(JSONB, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(60))
    prompt_version: Mapped[str] = mapped_column(String(20))


class InterviewSlot(TenantBase):
    """A time HR offered for an application. status: open|booked|cancelled."""

    __tablename__ = "interview_slots"
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="open")


class Interview(TenantBase):
    """The booked outcome. status: booked|done|no_show|cancelled.
    calendar_event_id: nullable, unused today — ponytail: real Google/Microsoft
    Calendar push is a later swap-in (needs per-company OAuth we don't have
    credentials for yet), not a seam worth building with zero real callers.
    """

    __tablename__ = "interviews"
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"))
    slot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interview_slots.id"), nullable=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="booked")
    calendar_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InterviewBookingLink(Base):
    """A magic-link token → (company, application), resolvable with NO tenant
    context — the candidate has no login. Deliberately NOT RLS-scoped, same
    reasoning as `companies`: it must be readable before a tenant is known.
    The token itself (32 bytes, urlsafe) is the only secret; the table holds
    no candidate data, just a correlation to what token-holders may access.
    """

    __tablename__ = "interview_booking_links"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
