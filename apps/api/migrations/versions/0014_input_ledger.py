"""Establishments, work facts, and the payroll input ledger.

THE ARCHITECTURAL PIVOT. Payroll used to start at

    employee -> current salary -> deductions -> payslip

which assumes a company is one jurisdiction, and that what someone is paid
this month can be read off their salary record. Neither survives a second
state or a site worker. It becomes

    work facts -> payroll input ledger -> statutory wage -> rules -> payslip

**establishments** carry jurisdiction. Professional tax, minimum wages and the
statutory registrations belong to a registered place of business; a company
with Maharashtra and Delhi establishments has two PT answers, and one of them
is none.

**work_facts** are what happened on a day: hours, overtime, premium day, site,
shift. Approved by a human before they can affect money. Attendance punches
remain the raw evidence; a work fact is the approved interpretation, which is
a different person's judgement and therefore a different record.

**payroll_inputs** are the ledger. The engine now asks what was approved for
August, instead of what this person is paid today. Those give different
answers the moment anyone gets a raise mid-month. Every row carries its
source, reason and approver.

Nothing is back-filled and no existing figure changes. Finalized payslips are
frozen records. The first run after this migration regenerates the ledger from
existing salary structures, which reproduces today's numbers exactly, because
that is where they already came from.

Revision ID: 0014_input_ledger
Revises: 0013_statutory_identity
Create Date: 2026-08-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0014_input_ledger"
down_revision: str | None = "0013_statutory_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(12, 2)
RLS_TABLES = ["establishments", "work_facts", "payroll_inputs"]


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "establishments", *_base(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("state_code", sa.String(4), nullable=False),
        sa.Column("pf_establishment_code", sa.String(30), nullable=True),
        sa.Column("esi_code", sa.String(30), nullable=True),
        sa.Column("pt_registration", sa.String(40), nullable=True),
        # Customer-entered, like PT slabs. Minimum wages vary by state, by
        # scheduled employment and by skill grade, and are revised twice a
        # year. This drives a WARNING, never a silent adjustment to pay.
        sa.Column("minimum_daily_wage", MONEY, nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
    )

    op.create_table(
        "work_facts", *_base(),
        sa.Column("employee_id", pg.UUID(as_uuid=True), sa.ForeignKey("employees.id"),
                  nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False, index=True),
        sa.Column("status", sa.String(12), server_default="worked", nullable=False),
        sa.Column("hours_worked", sa.Numeric(6, 2), server_default="0", nullable=False),
        sa.Column("overtime_hours", sa.Numeric(6, 2), server_default="0", nullable=False),
        sa.Column("premium_day", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("night_shift", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("site", sa.String(120), nullable=True),
        sa.Column("shift", sa.String(60), nullable=True),
        sa.Column("source", sa.String(12), server_default="attendance", nullable=False),
        sa.Column("approved_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"),
                  nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )

    op.create_table(
        "payroll_inputs", *_base(),
        sa.Column("employee_id", pg.UUID(as_uuid=True), sa.ForeignKey("employees.id"),
                  nullable=False, index=True),
        sa.Column("period", sa.Date(), nullable=False, index=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("amount", MONEY, server_default="0", nullable=False),
        sa.Column("quantity", sa.Numeric(9, 2), nullable=True),
        sa.Column("rate", MONEY, nullable=True),
        sa.Column("wage_basis", sa.String(10), server_default="excluded", nullable=False),
        sa.Column("source", sa.String(12), server_default="structure", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("approved_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"),
                  nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="100", nullable=False),
    )

    op.add_column(
        "employees",
        sa.Column("establishment_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("establishments.id"), nullable=True),
    )
    # PT belongs to a jurisdiction. NULL keeps the existing company-wide
    # schedule working, so nothing breaks for a single-state customer.
    op.add_column(
        "pt_slabs",
        sa.Column("establishment_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("establishments.id"), nullable=True),
    )

    # Unique among LIVE rows only, so a soft-deleted row releases its key.
    op.create_index(
        "uq_work_fact_employee_day", "work_facts", ["employee_id", "day"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_payroll_input_slot", "payroll_inputs",
        ["employee_id", "period", "code", "source"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_payroll_input_period", "payroll_inputs", ["employee_id", "period"])

    for t in RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (company_id = current_setting('app.company_id', true)::uuid) "
            f"WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
        )


def downgrade() -> None:
    op.drop_column("pt_slabs", "establishment_id")
    op.drop_column("employees", "establishment_id")
    for t in reversed(RLS_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
