"""attendance_events (punch ledger) + attendance_policies, both RLS.

No per-day summary table: a day is derived from its punches
(modules/attendance/service.py), the same way leave balance is derived from
approved requests rather than stored in a counter that can drift.

Revision ID: 0007_attendance
Revises: 0006_leave
Create Date: 2026-08-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007_attendance"
down_revision: str | None = "0006_leave"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ["attendance_events", "attendance_policies"]


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
        "attendance_events",
        *_base(),
        sa.Column("employee_id", pg.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(4), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("source", sa.String(10), server_default="ess", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )
    # The hot query is "this employee's punches in this window" — one composite
    # index serves both the personal history and the company presence view.
    op.create_index(
        "ix_attendance_events_employee_at", "attendance_events", ["employee_id", "at"]
    )

    op.create_table(
        "attendance_policies",
        *_base(),
        sa.Column("workday_start", sa.Time(), server_default="09:30", nullable=False),
        sa.Column("expected_minutes", sa.Integer(), server_default="480", nullable=False),
        sa.Column("grace_minutes", sa.Integer(), server_default="15", nullable=False),
        # Not decoration: grouping punches by UTC misfiles a 2am IST punch onto
        # the previous day, which would corrupt night-shift attendance for the
        # Indian SMEs this product targets.
        sa.Column("timezone", sa.String(64), server_default="Asia/Kolkata", nullable=False),
    )

    for t in RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (company_id = current_setting('app.company_id', true)::uuid) "
            f"WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS attendance_events CASCADE")
    op.execute("DROP TABLE IF EXISTS attendance_policies CASCADE")
