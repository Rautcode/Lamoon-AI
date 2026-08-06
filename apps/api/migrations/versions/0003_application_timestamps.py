"""applications.screened_at / rejected_at — anchors for the 10-day auto-reject window

Revision ID: 0003_app_ts
Revises: 0002_auth
Create Date: 2026-08-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_app_ts"
down_revision: str | None = "0002_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("screened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "rejected_at")
    op.drop_column("applications", "screened_at")
