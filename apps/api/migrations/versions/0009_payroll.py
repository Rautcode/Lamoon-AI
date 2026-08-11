"""Payroll: components, salary structures, statutory config, runs, payslips (RLS).

Also adds leave_types.paid, defaulting TRUE. Defaulting true is deliberate:
every leave type that already exists was created in a product with no concept
of unpaid leave, so flipping any of them to unpaid retroactively would dock
people for time already taken and approved.

Money columns are NUMERIC(12,2) throughout. No FLOAT anywhere in this
migration, and none should ever appear in a table on this list.

Uniqueness is enforced by PARTIAL indexes (`WHERE deleted_at IS NULL`) rather
than plain UNIQUE constraints. Every table here soft-deletes, and a
soft-deleted row still occupies a plain unique constraint — so replacing a
salary structure (delete the old rows, insert the new) would fail on the
second edit. 0010 fixes the same latent bug on the tables that predate this.

Revision ID: 0009_payroll
Revises: 0008_work_calendar
Create Date: 2026-08-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009_payroll"
down_revision: str | None = "0008_work_calendar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(12, 2)

RLS_TABLES = [
    "pay_components",
    "salary_components",
    "payroll_settings",
    "pt_slabs",
    "payroll_runs",
    "payslips",
]


#: Uniqueness among LIVE rows only. A plain UNIQUE here would make the second
#: edit of a salary structure fail, because the soft-deleted rows from the
#: first edit still hold the key.
LIVE_UNIQUE = [
    ("uq_pay_component_code", "pay_components", ["company_id", "code"]),
    ("uq_salary_employee_component", "salary_components", ["employee_id", "component_id"]),
    # One payroll per month per company: what stops a double run paying twice.
    ("uq_payroll_run_period", "payroll_runs", ["company_id", "period"]),
    ("uq_payslip_run_employee", "payslips", ["run_id", "employee_id"]),
]


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
    op.add_column(
        "leave_types",
        sa.Column("paid", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )

    op.create_table(
        "pay_components",
        *_base(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), server_default="earning", nullable=False),
        sa.Column("pf_wage", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("esi_wage", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("taxable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="100", nullable=False),
    )

    op.create_table(
        "salary_components",
        *_base(),
        sa.Column("employee_id", pg.UUID(as_uuid=True), sa.ForeignKey("employees.id"),
                  nullable=False, index=True),
        sa.Column("component_id", pg.UUID(as_uuid=True), sa.ForeignKey("pay_components.id"),
                  nullable=False),
        sa.Column("amount", MONEY, nullable=False),
    )

    op.create_table(
        "payroll_settings",
        *_base(),
        # PF/ESI default OFF: registration is mandatory only above 20/10
        # employees, and deducting for a scheme the employer isn't registered
        # under is worse than not deducting at all.
        sa.Column("pf_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("esi_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("pf_wage_ceiling", MONEY, server_default="15000", nullable=False),
        sa.Column("esi_wage_ceiling", MONEY, server_default="21000", nullable=False),
        sa.Column("pf_on_full_wage", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
    )

    op.create_table(
        "pt_slabs",
        *_base(),
        # NULL up_to = the unbounded top slab.
        sa.Column("up_to", MONEY, nullable=True),
        sa.Column("amount", MONEY, nullable=False),
    )

    op.create_table(
        "payroll_runs",
        *_base(),
        sa.Column("period", sa.Date(), nullable=False, index=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("gross_total", MONEY, server_default="0", nullable=False),
        sa.Column("deductions_total", MONEY, server_default="0", nullable=False),
        sa.Column("net_total", MONEY, server_default="0", nullable=False),
        sa.Column("employer_cost_total", MONEY, server_default="0", nullable=False),
    )

    op.create_table(
        "payslips",
        *_base(),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("payroll_runs.id"),
                  nullable=False, index=True),
        sa.Column("employee_id", pg.UUID(as_uuid=True), sa.ForeignKey("employees.id"),
                  nullable=False, index=True),
        # Snapshot, so a payslip survives the employee being renamed or exited.
        sa.Column("employee_name", sa.String(200), nullable=False),
        sa.Column("working_days", sa.Integer(), nullable=False),
        sa.Column("paid_days", sa.Integer(), nullable=False),
        sa.Column("lop_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lop_overridden", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("gross", MONEY, server_default="0", nullable=False),
        sa.Column("deductions", MONEY, server_default="0", nullable=False),
        sa.Column("net", MONEY, server_default="0", nullable=False),
        sa.Column("employer_cost", MONEY, server_default="0", nullable=False),
        sa.Column("tds", MONEY, server_default="0", nullable=False),
        sa.Column("esi_employee", MONEY, server_default="0", nullable=False),
        sa.Column("breakdown", pg.JSONB(), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
    )

    for name, table, cols in LIVE_UNIQUE:
        op.create_index(
            name, table, cols, unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
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
    for t in reversed(RLS_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.drop_column("leave_types", "paid")
