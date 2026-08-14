"""overtime multipliers become configuration

Revision ID: 0023_overtime_policy
Revises: 0022_fractional_days

Three constants sat in `ledger.py` deciding real money: the overtime
multiplier, the premium-day multiplier, and the standard hours that turn a
monthly wage into an hourly rate. Their own comment said to lift them out "the
moment a customer needs a different multiplier", and unlike PF rates these
genuinely vary by scheduled employment rather than by statute.

Defaults reproduce today's behaviour exactly — 2× overtime per the Code on
Wages, 2× for premium days, 8-hour standard day — so no existing tenant's pay
moves.

NOT effective-dated, deliberately. Effective-dating these means a rules table
keyed by (statute, jurisdiction, period), which is D-1.2, and that task will
absorb every statutory parameter at once. Building a second, parallel
effective-dating mechanism here would be a thing to migrate away from later.
"""
import sqlalchemy as sa
from alembic import op

revision = "0023_overtime_policy"
down_revision = "0022_fractional_days"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payroll_settings",
        sa.Column("overtime_multiplier", sa.Numeric(4, 2), nullable=False,
                  server_default="2.0"),
    )
    op.add_column(
        "payroll_settings",
        sa.Column("premium_day_multiplier", sa.Numeric(4, 2), nullable=False,
                  server_default="2.0"),
    )
    op.add_column(
        "payroll_settings",
        sa.Column("standard_day_hours", sa.Numeric(4, 2), nullable=False,
                  server_default="8.0"),
    )


def downgrade() -> None:
    for col in ("standard_day_hours", "premium_day_multiplier", "overtime_multiplier"):
        op.drop_column("payroll_settings", col)
