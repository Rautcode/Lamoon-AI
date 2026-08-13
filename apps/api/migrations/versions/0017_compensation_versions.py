"""compensation versions — effective-dated salary

Revision ID: 0017_compensation_versions
Revises: 0016_contractors

Salary stopped being a value and became a timeline. The old model kept one
live row per employee-component and soft-deleted the rest on a change, which
records WHEN somebody edited the salary but not WHEN THE SALARY APPLIED —
and payroll needs the second one.

The back-fill gives every employee one open-ended version holding their
current structure. Its effective_from is deliberately early (joining date, or
the earliest period they have a payslip for) so that re-running any historical
period resolves to the same numbers it produced before this migration.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0017_compensation_versions"
down_revision = "0016_contractors"
branch_labels = None
depends_on = None

RLS_TABLES = ("compensation_versions", "compensation_lines")


def upgrade() -> None:
    op.create_table(
        "compensation_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"),
                  nullable=False, index=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("reason", sa.String(20), nullable=False, server_default="revision"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("supersedes_id", UUID(as_uuid=True),
                  sa.ForeignKey("compensation_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_compensation_version_dates",
        ),
    )
    op.create_index(
        "uq_compensation_version_start", "compensation_versions",
        ["employee_id", "effective_from"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_compensation_version_lookup", "compensation_versions",
        ["employee_id", "effective_from"],
    )

    op.create_table(
        "compensation_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("version_id", UUID(as_uuid=True),
                  sa.ForeignKey("compensation_versions.id"), nullable=False, index=True),
        sa.Column("component_id", UUID(as_uuid=True),
                  sa.ForeignKey("pay_components.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_compensation_line", "compensation_lines", ["version_id", "component_id"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- back-fill ----------------------------------------------------------
    #
    # RLS is FORCEd and migrations run as a non-superuser, so an unguarded
    # INSERT...SELECT reads ZERO rows from the source tables and reports
    # success (migrations 0011 and 0012 learned this the hard way). Suspend it
    # on every table touched — including the READ side of the join, which is
    # the part that is easy to forget.
    touched = ("compensation_versions", "compensation_lines",
               "salary_components", "employees", "payslips")
    for t in touched:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")

    # One open-ended version per employee who currently has a salary.
    #
    # effective_from is the earliest date the salary could have applied: their
    # joining date, or the first period they were paid for if that is earlier
    # (data predating a joining date exists), or a floor. Starting "today"
    # would make every historical period resolve to nothing.
    op.execute("""
        INSERT INTO compensation_versions
            (id, company_id, employee_id, effective_from, effective_to,
             reason, note, created_at, updated_at)
        SELECT gen_random_uuid(),
               e.company_id,
               e.id,
               LEAST(
                   COALESCE(e.joined_on, DATE '2000-01-01'),
                   COALESCE((SELECT MIN(p.period) FROM payslips p
                              WHERE p.employee_id = e.id AND p.deleted_at IS NULL),
                            DATE '9999-12-31')
               ),
               NULL,
               'migration',
               'created by migration 0017 from the pre-versioning salary structure',
               now(), now()
          FROM employees e
         WHERE e.deleted_at IS NULL
           AND EXISTS (SELECT 1 FROM salary_components s
                        WHERE s.employee_id = e.id AND s.deleted_at IS NULL)
    """)

    op.execute("""
        INSERT INTO compensation_lines
            (id, company_id, version_id, component_id, amount, created_at, updated_at)
        SELECT gen_random_uuid(), s.company_id, v.id, s.component_id, s.amount,
               now(), now()
          FROM salary_components s
          JOIN compensation_versions v
            ON v.employee_id = s.employee_id AND v.reason = 'migration'
         WHERE s.deleted_at IS NULL
    """)

    # Prove the back-fill actually moved data. Without this a silently empty
    # migration passes and the failure only surfaces as everybody's pay
    # dropping to zero on the next run.
    #
    # Scoped to LIVE employees, exactly as the inserts are. A soft-deleted
    # employee can still own live salary_components rows — nothing ever
    # cascaded them — and carrying those forward would resurrect compensation
    # for somebody who is not on the payroll. Counting them here instead
    # produced an off-by-one that looked like RLS hiding rows.
    conn = op.get_bind()
    employees_with_salary = conn.execute(sa.text("""
        SELECT count(DISTINCT s.employee_id)
          FROM salary_components s
          JOIN employees e ON e.id = s.employee_id
         WHERE s.deleted_at IS NULL AND e.deleted_at IS NULL
    """)).scalar_one()
    live_components = conn.execute(sa.text("""
        SELECT count(*)
          FROM salary_components s
          JOIN employees e ON e.id = s.employee_id
         WHERE s.deleted_at IS NULL AND e.deleted_at IS NULL
    """)).scalar_one()
    versions = conn.execute(sa.text(
        "SELECT count(*) FROM compensation_versions WHERE reason = 'migration'"
    )).scalar_one()
    lines = conn.execute(sa.text("SELECT count(*) FROM compensation_lines")).scalar_one()

    assert versions == employees_with_salary, (
        f"back-fill created {versions} versions for {employees_with_salary} "
        "live employees with a salary — RLS may have hidden rows"
    )
    assert lines == live_components, (
        f"back-fill copied {lines} lines from {live_components} live salary components"
    )

    for t in touched:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")

    for t in RLS_TABLES:
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (company_id = current_setting('app.company_id', true)::uuid) "
            f"WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
        )


def downgrade() -> None:
    for t in reversed(RLS_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
