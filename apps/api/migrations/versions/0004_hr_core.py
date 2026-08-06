"""departments + employees (RLS). manager_id/employee FK cycle resolved by
adding departments.manager_id via ALTER after employees exists.

Revision ID: 0004_hr_core
Revises: 0003_app_ts
Create Date: 2026-08-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004_hr_core"
down_revision: str | None = "0003_app_ts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ["departments", "employees"]


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
        "departments",
        *_base(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parent_id", pg.UUID(as_uuid=True), sa.ForeignKey("departments.id"), nullable=True),
        # manager_id added below, after employees exists.
    )

    op.create_table(
        "employees",
        *_base(),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("department_id", pg.UUID(as_uuid=True), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("reporting_manager_id", pg.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("joined_on", sa.Date(), nullable=True),
    )

    op.add_column(
        "departments",
        sa.Column("manager_id", pg.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=True),
    )

    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (company_id = current_setting('app.company_id', true)::uuid) "
            f"WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS employees CASCADE")
    op.execute("DROP TABLE IF EXISTS departments CASCADE")
