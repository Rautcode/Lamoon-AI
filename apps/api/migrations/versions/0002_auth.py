"""companies (tenant root, no RLS) + users (tenant-scoped, RLS)

Revision ID: 0002_auth
Revises: 0001_init_ats
Create Date: 2026-08-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002_auth"
down_revision: str | None = "0001_init_ats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # companies is the tenant root: NOT under RLS (must be readable at login,
    # before a tenant is established).
    op.create_table(
        "companies",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("subdomain", sa.String(60), nullable=False, unique=True, index=True),
        sa.Column("plan", sa.String(20), server_default="starter", nullable=False),
        sa.Column("seat_limit", sa.Integer(), server_default="25", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email", sa.String(200), nullable=False, index=True),
        sa.Column("password_hash", sa.String(200), nullable=True),
        sa.Column("oauth_provider", sa.String(20), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("role", sa.String(20), server_default="employee", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.UniqueConstraint("company_id", "email", name="uq_user_email"),
    )

    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON users "
        "USING (company_id = current_setting('app.company_id', true)::uuid) "
        "WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TABLE IF EXISTS companies CASCADE")
