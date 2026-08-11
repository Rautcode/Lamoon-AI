"""Statutory identity on employees, EDLI/admin at run level, TDS provenance.

Three gaps, all of which made the engine assert things it could not know.

**EPS applicability.** The engine computed pension for everybody, because it
had no date of birth and no EPF-membership date to decide otherwise. EPS is
not payable to a member aged 58+, nor to someone first joining EPF on or after
1 Sep 2014 above the wage ceiling; in both cases the employer's whole 12% goes
to EPF. `pf_first_joined_on` is first membership ANYWHERE, not the joining
date here, because the exclusion turns on first membership.

**EDLI and administration charges.** Employer-only, roughly 1% of PF wages on
top of the 12%, and previously absent — so every cost-to-company figure was
understated. Administration additionally carries a per-ESTABLISHMENT monthly
minimum, which is not attributable to any employee; `payroll_runs.admin
_shortfall` holds the top-up rather than smearing it across payslips and
breaking each payslip's own arithmetic.

**TDS provenance.** The amount was stored with no record of where it came
from. An auditable deduction needs source, tax year, and who entered it when.

Nothing is back-filled. Existing finalized payslips are frozen records: they
were computed without these charges and must keep the figures they were paid
at. New runs pick the charges up.

Revision ID: 0013_statutory_identity
Revises: 0012_wage_basis
Create Date: 2026-08-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0013_statutory_identity"
down_revision: str | None = "0012_wage_basis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(12, 2)


def upgrade() -> None:
    op.add_column("employees", sa.Column("date_of_birth", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("pf_first_joined_on", sa.Date(), nullable=True))
    op.add_column(
        "employees",
        sa.Column("is_international_worker", sa.Boolean(),
                  server_default=sa.text("false"), nullable=False),
    )
    op.add_column("employees", sa.Column("uan", sa.String(20), nullable=True))

    op.add_column(
        "payroll_runs",
        sa.Column("admin_shortfall", MONEY, server_default="0", nullable=False),
    )

    op.add_column("payslips", sa.Column("tds_source", sa.String(120), nullable=True))
    op.add_column("payslips", sa.Column("tds_tax_year", sa.String(9), nullable=True))
    op.add_column("payslips", sa.Column("tds_note", sa.Text(), nullable=True))
    op.add_column(
        "payslips",
        sa.Column("tds_provided_by", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column(
        "payslips",
        sa.Column("tds_provided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for col in ("tds_provided_at", "tds_provided_by", "tds_note", "tds_tax_year",
                "tds_source"):
        op.drop_column("payslips", col)
    op.drop_column("payroll_runs", "admin_shortfall")
    for col in ("uan", "is_international_worker", "pf_first_joined_on", "date_of_birth"):
        op.drop_column("employees", col)
