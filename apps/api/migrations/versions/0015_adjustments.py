"""Corrections to finalized periods, applied in a later one.

Every refusal to edit a closed month has pointed at this table: "corrections
belong in a later period as an adjustment". Until now that path did not exist,
which made the refusal a dead end rather than a redirection.

An adjustment records the lineage a bare payroll input cannot express — which
finalized month was wrong, why, and who agreed to settle it. Approving it is
what creates the ledger row; until then it is a proposal.

    April  finalized, short by 2,400
      └─ adjustment: source April, target May, arrear 2,400, reason
           └─ payroll input in May, source="adjustment"
                └─ May payslip: "April arrear   2,400"

The ledger already preserves `adjustment` rows through a rebuild, so nothing
about generation changes — this only adds the record of why.

Revision ID: 0015_adjustments
Revises: 0014_input_ledger
Create Date: 2026-08-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0015_adjustments"
down_revision: str | None = "0014_input_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(12, 2)


def upgrade() -> None:
    op.create_table(
        "payroll_adjustments",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"),
                  nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("employee_id", pg.UUID(as_uuid=True), sa.ForeignKey("employees.id"),
                  nullable=False, index=True),
        # Both months are kept so both stay explicable: "why is May higher"
        # is answered by pointing at April.
        sa.Column("source_period", sa.Date(), nullable=False, index=True),
        sa.Column("target_period", sa.Date(), nullable=False, index=True),

        # A direction, not a signed amount: "-2,400" on screen is ambiguous
        # about whether it was owed or overpaid, and somebody has to type it
        # correctly under time pressure.
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        # NOT NULL: a correction without a stated reason is indistinguishable
        # from somebody changing a number they did not like.
        sa.Column("reason", sa.Text(), nullable=False),

        sa.Column("approved_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"),
                  nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_input_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("payroll_inputs.id"), nullable=True),
    )

    op.execute("ALTER TABLE payroll_adjustments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payroll_adjustments FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON payroll_adjustments "
        "USING (company_id = current_setting('app.company_id', true)::uuid) "
        "WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payroll_adjustments CASCADE")
