"""Classify pay components for the statutory wage definition.

From 21 November 2025 the Code on Wages definition applies: if the excluded
allowances exceed half of total remuneration, the excess is added back into
wages. Deciding that needs each component classified as wages / excluded /
outside remuneration, which a single `pf_wage` boolean cannot express — it
says "counts toward PF" but not "counts toward the denominator of the 50%
test", and those are different questions.

Back-filled from `pf_wage` so existing structures keep their current PF basis:
a component that counted toward PF was, in practice, basic or DA. `pf_wage` is
retained rather than dropped, so any caller still sending it keeps working;
the engine reads `wage_basis`.

This migration changes no computed figure on its own. The wage definition only
engages for periods on or after 21 Nov 2025, and pre-existing payslips are
frozen snapshots that are never recomputed.

Revision ID: 0012_wage_basis
Revises: 0011_payslip_period
Create Date: 2026-08-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_wage_basis"
down_revision: str | None = "0011_payslip_period"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pay_components",
        sa.Column("wage_basis", sa.String(10), server_default="excluded", nullable=False),
    )
    # Data migration: RLS is FORCEd and migrations run as a non-superuser, so a
    # bare UPDATE matches zero rows and reports success (see 0011). Suspend it
    # inside this transaction.
    op.execute("ALTER TABLE pay_components NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pay_components DISABLE ROW LEVEL SECURITY")
    op.execute("UPDATE pay_components SET wage_basis = 'wages' WHERE pf_wage IS TRUE")
    op.execute("ALTER TABLE pay_components ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pay_components FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column("pay_components", "wage_basis")
