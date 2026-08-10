"""work_calendars + holidays (RLS).

Fixes a real accounting bug: leave was billed in CALENDAR days, so a
Friday-to-Monday absence cost 4 days of balance instead of 2. From here
`leave.create_request` bills working days only.

Existing leave_requests keep the day counts they were approved with — this
migration does NOT rewrite them. Those numbers are a record of what was
actually agreed, and silently re-deriving history would be worse than the
original bug.

Revision ID: 0008_work_calendar
Revises: 0007_attendance
Create Date: 2026-08-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0008_work_calendar"
down_revision: str | None = "0007_attendance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = ["work_calendars", "holidays"]


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
        "work_calendars",
        *_base(),
        # Monday-first, "1" = worked. Default Mon–Fri, but explicitly
        # configurable: Mon–Sat ("1111110") is common in Indian SMEs.
        sa.Column("working_days", sa.String(7), server_default="1111100", nullable=False),
    )

    op.create_table(
        "holidays",
        *_base(),
        sa.Column("day", sa.Date(), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.UniqueConstraint("company_id", "day", name="uq_holiday_company_day"),
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
    op.execute("DROP TABLE IF EXISTS holidays CASCADE")
    op.execute("DROP TABLE IF EXISTS work_calendars CASCADE")
