"""half-days, exit dates, and the rest of the leave policy

Revision ID: 0022_fractional_days
Revises: 0021_leave_policy

Three things that had to happen together, because doing them separately would
mean shipping a half-day that payroll rounds away.

FRACTIONAL DAYS. A half-day of unpaid leave is half a day of loss of pay, so
`leave_requests.days`, `payslips.paid_days` and `payslips.lop_days` stop being
integers. `payslips.working_days` deliberately does NOT: it is a count of days
in a calendar and is always whole, and making it fractional would invite
somebody to put half a working day in it.

EXIT DATE. `employees.status` could say "exited" but nothing recorded WHEN, so
exit proration was implemented and unreachable. Leave entitlement, F&F and the
movement bridge all need the date, not the state.

THE REST OF THE POLICY. Carry-forward, probation and negative balance were
listed in C4 and left out of the first pass. They are policy columns, not new
machinery.

Numeric(5,1) rather than (5,2): half a day is the smallest unit anybody grants
leave in, and a column that can hold 0.33 invites somebody to write 0.33.
"""
import sqlalchemy as sa
from alembic import op

revision = "0022_fractional_days"
down_revision = "0021_leave_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- exit date ----------------------------------------------------------
    op.add_column("employees", sa.Column("exited_on", sa.Date(), nullable=True))

    # Back-fill nothing: `status = 'exited'` records THAT somebody left, never
    # when, and inventing a date would put a fabricated figure into an F&F
    # calculation. Existing leavers keep a null exit date and prorate as if
    # employed for the year — which is exactly what happened before this column
    # existed, so no balance moves.

    # --- fractional days ----------------------------------------------------
    for table, column in (
        ("leave_requests", "days"),
        ("payslips", "paid_days"),
        ("payslips", "lop_days"),
    ):
        op.alter_column(
            table, column,
            type_=sa.Numeric(5, 1),
            existing_type=sa.Integer(),
            postgresql_using=f"{column}::numeric(5,1)",
        )

    # --- the rest of the leave policy --------------------------------------
    op.add_column(
        "leave_policies",
        sa.Column("carry_forward_max", sa.Numeric(5, 1), nullable=True),
    )
    op.add_column(
        "leave_policies",
        sa.Column("carry_forward_expires_months", sa.Integer(), nullable=True),
    )
    op.add_column(
        "leave_policies",
        sa.Column("accrue_during_probation", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
    )
    op.add_column(
        "leave_policies",
        sa.Column("allow_negative_balance", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        "leave_policies",
        sa.Column("encashable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Comp-off is earned, not granted, so it is a property of the TYPE rather
    # than of an entitlement policy: a company either runs compensatory off or
    # it does not.
    op.add_column(
        "leave_types",
        sa.Column("comp_off", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("leave_types", "comp_off")
    for col in ("encashable", "allow_negative_balance", "accrue_during_probation",
                "carry_forward_expires_months", "carry_forward_max"):
        op.drop_column("leave_policies", col)
    for table, column in (
        ("payslips", "lop_days"), ("payslips", "paid_days"), ("leave_requests", "days"),
    ):
        op.alter_column(table, column, type_=sa.Integer(),
                        existing_type=sa.Numeric(5, 1),
                        postgresql_using=f"{column}::integer")
    op.drop_column("employees", "exited_on")
