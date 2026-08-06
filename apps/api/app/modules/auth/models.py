"""Auth tables. Company is the tenant ROOT — the only table without company_id
and NOT under RLS (it must be readable at login before a tenant is established).
User is tenant-scoped (TenantBase → RLS)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantBase


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    subdomain: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(20), default="starter")
    seat_limit: Mapped[int] = mapped_column(Integer, default=25)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(TenantBase):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("company_id", "email", name="uq_user_email"),)
    email: Mapped[str] = mapped_column(String(200), index=True)
    # password_hash: null if OAuth-only
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    oauth_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="employee")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
