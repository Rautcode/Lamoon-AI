"""Snapshot the pay period onto the payslip.

A payslip already snapshots the employee's name so it stays readable after the
person is renamed. The month deserves the same treatment for a plainer reason:
self-service lists a person's payslips, and labelling each one required
joining back to payroll_runs for a value that can never change once the run is
finalized.

Added forward-only in three steps — nullable, backfill, NOT NULL — so it can
run against a database that already has payslips in it. Nothing is deleted and
no existing value is rewritten: the backfill copies each payslip's own run
period, which is by definition the month it was already for.

READ THIS BEFORE WRITING ANOTHER DATA MIGRATION
-----------------------------------------------
Migrations connect as `app`, which is deliberately NOT a superuser and has no
BYPASSRLS (ADR-0002, db/init/01-app-role.sql). Every tenant table also has
FORCE ROW LEVEL SECURITY. So a bare `UPDATE` in a migration matches **zero
rows** — `app.company_id` is unset, the policy evaluates to NULL, and Postgres
reports a perfectly successful statement that changed nothing. This was caught
here only because the following SET NOT NULL failed; a backfill without one
would have shipped silently empty.

DDL is not filtered, which is why every migration up to this one was fine.
Data migrations must suspend RLS around the statement, as below. It is
suspended and restored inside the same transaction, so no other session ever
sees the table unprotected.

Revision ID: 0011_payslip_period
Revises: 0010_partial_unique
Create Date: 2026-08-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_payslip_period"
down_revision: str | None = "0010_partial_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payslips", sa.Column("period", sa.Date(), nullable=True))

    # See the module docstring: without this the UPDATE below silently matches
    # nothing. BOTH tables need it — payroll_runs is only read, but the read
    # side of the join is filtered too, and a filtered join finds no rows to
    # copy from. Same transaction, so the window is not observable.
    for table in ("payslips", "payroll_runs"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute(
        "UPDATE payslips SET period = payroll_runs.period "
        "FROM payroll_runs WHERE payroll_runs.id = payslips.run_id"
    )

    for table in ("payslips", "payroll_runs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Fails loudly if the backfill missed anything, rather than leaving the
    # column half-populated.
    op.alter_column("payslips", "period", nullable=False)
    op.create_index("ix_payslips_period", "payslips", ["period"])


def downgrade() -> None:
    op.drop_index("ix_payslips_period", table_name="payslips")
    op.drop_column("payslips", "period")
