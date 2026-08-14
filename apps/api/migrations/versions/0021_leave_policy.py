"""leave policies — quota stops being the same for everybody

Revision ID: 0021_leave_policy
Revises: 0020_inbox

`LeaveType.annual_quota` carried the comment "days/year, same for every
employee in V1". No real company gives the same leave to a probationer and a
ten-year employee, or to a factory and a head office, so that comment was the
single thing blocking any real customer from using leave.

`annual_quota` is NOT dropped. It stays as the fallback for a leave type nobody
has written a policy for, and the back-fill creates one company-scope policy
per existing type — so every tenant behaves identically until somebody writes
a more specific one.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0021_leave_policy"
down_revision = "0020_inbox"
branch_labels = None
depends_on = None

EPOCH = "2000-01-01"


def upgrade() -> None:
    op.create_table(
        "leave_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("leave_type_id", UUID(as_uuid=True), sa.ForeignKey("leave_types.id"),
                  nullable=False, index=True),
        sa.Column("scope_type", sa.String(20), nullable=False, server_default="company"),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("scope_value", sa.String(40), nullable=True),
        sa.Column("annual_days", sa.Numeric(5, 1), nullable=False, server_default="0"),
        sa.Column("accrual_method", sa.String(10), nullable=False, server_default="annual"),
        sa.Column("prorate_on_joining", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("prorate_on_exit", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_leave_policy_dates",
        ),
    )
    op.create_index(
        "ix_leave_policy_lookup", "leave_policies",
        ["company_id", "leave_type_id", "scope_type"],
    )

    # --- back-fill ----------------------------------------------------------
    #
    # RLS is FORCEd and migrations run as a non-superuser, so an unguarded
    # INSERT...SELECT reads zero rows from the source and reports success.
    for t in ("leave_types", "leave_policies"):
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")

    op.execute(f"""
        INSERT INTO leave_policies
            (id, company_id, leave_type_id, scope_type, scope_id, scope_value,
             annual_days, accrual_method, prorate_on_joining, prorate_on_exit,
             effective_from, effective_to, created_at, updated_at)
        SELECT gen_random_uuid(), t.company_id, t.id, 'company', NULL, NULL,
               t.annual_quota, 'annual',
               -- Proration OFF for the back-fill. The old behaviour granted the
               -- full quota to everybody regardless of joining date, and a
               -- migration must not quietly reduce anybody's leave balance.
               FALSE, FALSE,
               DATE '{EPOCH}', NULL, now(), now()
          FROM leave_types t
         WHERE t.deleted_at IS NULL
    """)

    conn = op.get_bind()
    types = conn.execute(sa.text(
        "SELECT count(*) FROM leave_types WHERE deleted_at IS NULL"
    )).scalar_one()
    policies = conn.execute(sa.text("SELECT count(*) FROM leave_policies")).scalar_one()
    assert policies == types, (
        f"{policies} policies for {types} leave types — RLS may have hidden rows, "
        "and a type without a policy silently falls back to its own quota"
    )

    for t in ("leave_types", "leave_policies"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")

    op.execute(
        "CREATE POLICY tenant_isolation ON leave_policies "
        "USING (company_id = current_setting('app.company_id', true)::uuid) "
        "WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS leave_policies CASCADE")
