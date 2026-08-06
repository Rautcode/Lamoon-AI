"""interview_slots + interviews (RLS) + interview_booking_links (no RLS —
magic-link tokens must resolve before a tenant is known, same reasoning as
`companies`).

Revision ID: 0005_interviews
Revises: 0004_hr_core
Create Date: 2026-08-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005_interviews"
down_revision: str | None = "0004_hr_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ["interview_slots", "interviews"]


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "interview_slots",
        *_base(),
        sa.Column("application_id", pg.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
    )

    op.create_table(
        "interviews",
        *_base(),
        sa.Column("application_id", pg.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("slot_id", pg.UUID(as_uuid=True), sa.ForeignKey("interview_slots.id"), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="booked", nullable=False),
        sa.Column("calendar_event_id", sa.String(200), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    for t in RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (company_id = current_setting('app.company_id', true)::uuid) "
            f"WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
        )

    # NOT under RLS — must be resolvable by an unauthenticated candidate before
    # any tenant is known (mirrors `companies`, see model docstring).
    op.create_table(
        "interview_booking_links",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("token", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("company_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS interview_booking_links CASCADE")
    op.execute("DROP TABLE IF EXISTS interviews CASCADE")
    op.execute("DROP TABLE IF EXISTS interview_slots CASCADE")
