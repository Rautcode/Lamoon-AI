"""init ATS + audit tables with row-level security

Revision ID: 0001_init_ats
Revises:
Create Date: 2026-08-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001_init_ats"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ["job_openings", "candidates", "applications", "ai_analyses", "audit_events"]


def _base() -> list[sa.Column]:
    """The columns every tenant table carries (TenantBase)."""
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
        "job_openings",
        *_base(),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("required_skills", pg.JSONB(), server_default="[]", nullable=False),
        sa.Column("preferred_skills", pg.JSONB(), server_default="[]", nullable=False),
        sa.Column("min_experience_years", sa.Integer(), server_default="0", nullable=False),
        sa.Column("location", sa.String(120), nullable=True),
        sa.Column("education", sa.String(120), nullable=True),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
    )

    op.create_table(
        "candidates",
        *_base(),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("resume_blob_key", sa.String(400), nullable=False),
        sa.Column("resume_url", sa.String(600), nullable=True),
        sa.Column("resume_sha256", sa.String(64), nullable=False, index=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "resume_sha256", name="uq_candidate_resume"),
    )

    op.create_table(
        "applications",
        *_base(),
        sa.Column("candidate_id", pg.UUID(as_uuid=True), sa.ForeignKey("candidates.id"), nullable=False),
        sa.Column("job_opening_id", pg.UUID(as_uuid=True), sa.ForeignKey("job_openings.id"), nullable=True),
        sa.Column("source", sa.String(20), server_default="webhook", nullable=False),
        sa.Column("status", sa.String(20), server_default="received", nullable=False),
        sa.Column("tier", sa.String(1), nullable=True),
        sa.Column("recommended_action", sa.String(40), nullable=True),
    )

    op.create_table(
        "ai_analyses",
        *_base(),
        sa.Column("application_id", pg.UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("resume_sha256", sa.String(64), nullable=False, index=True),
        sa.Column("recipe_hash", sa.String(64), nullable=False),
        sa.Column("extracted", pg.JSONB(), server_default="{}", nullable=False),
        sa.Column("technical_score", sa.Float(), nullable=False),
        sa.Column("experience_score", sa.Float(), nullable=False),
        sa.Column("education_score", sa.Float(), nullable=False),
        sa.Column("communication_score", sa.Float(), nullable=False),
        sa.Column("overall_ai_score", sa.Float(), nullable=False),
        sa.Column("job_match_pct", sa.Float(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("matched_skills", pg.JSONB(), server_default="[]", nullable=False),
        sa.Column("missing_skills", pg.JSONB(), server_default="[]", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
    )

    op.create_table(
        "audit_events",
        *_base(),
        sa.Column("entity", sa.String(60), nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("actor_user_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
        sa.Column("payload", pg.JSONB(), server_default="{}", nullable=False),
    )

    # Row-Level Security — the real tenant guard (ADR-0002). FORCE so the table
    # owner (the app's DB user) is subject to it too. current_setting(..., true)
    # returns NULL when unset → policy denies, which is the safe default.
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (company_id = current_setting('app.company_id', true)::uuid) "
            f"WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
        )


def downgrade() -> None:
    for t in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
