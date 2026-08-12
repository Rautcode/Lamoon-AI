"""Contractors, their invoices, and worker type.

Two things, related but distinct.

**worker_type** decides how somebody's pay is EXPLAINED: a monthly salary
measured against attendance, or days and hours measured against a rate. It is
not a job title — a salaried supervisor on a site is white collar here. The
engine already computes both; this is what lets the interface stop showing a
site worker a "monthly salary" they do not have.

**Contractors** are not employees with a flag. A contractor is paid by invoice
against what their workers did; the company owes the contractor, and the
contractor owes the worker. Modelling them as employees would make the payroll
register claim to pay people there is no employment relationship with.

The variance between what attendance says a contractor is owed and what they
invoiced is the point of the whole table. Billing for days nobody worked is
the most common leak in site payroll and is invisible until the two figures
sit next to each other.

Revision ID: 0016_contractors
Revises: 0015_adjustments
Create Date: 2026-08-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0016_contractors"
down_revision: str | None = "0015_adjustments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(12, 2)
RLS_TABLES = ["contractors", "contractor_invoices"]


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
        "contractors", *_base(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("code", sa.String(40), nullable=True),
        sa.Column("contact_email", sa.String(200), nullable=True),
        # Statutory identity the principal employer keeps on file. Recorded,
        # not validated — the format is not ours to police.
        sa.Column("licence_number", sa.String(60), nullable=True),
        sa.Column("gstin", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"),
                  nullable=False),
    )

    op.create_table(
        "contractor_invoices", *_base(),
        sa.Column("contractor_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("contractors.id"), nullable=False, index=True),
        sa.Column("period", sa.Date(), nullable=False, index=True),
        sa.Column("reference", sa.String(60), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        # A variance does not block RECORDING the invoice — it blocks agreeing
        # to it, which is a different act with a different button.
        sa.Column("status", sa.String(12), server_default="received", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("approved_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"),
                  nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_contractor_invoice_period", "contractor_invoices",
        ["contractor_id", "period"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.add_column(
        "employees",
        sa.Column("worker_type", sa.String(14), server_default="white_collar",
                  nullable=False),
    )
    op.add_column(
        "employees",
        sa.Column("contractor_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("contractors.id"), nullable=True),
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
    op.drop_column("employees", "contractor_id")
    op.drop_column("employees", "worker_type")
    for t in reversed(RLS_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
