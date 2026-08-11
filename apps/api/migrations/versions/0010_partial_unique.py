"""Make the pre-existing unique constraints ignore soft-deleted rows.

A bug, found while building payroll and confirmed against a running API:

    POST   /calendar/holidays  {"day": "2026-12-25"}   -> 200
    DELETE /calendar/holidays/{id}                     -> 204
    POST   /calendar/holidays  {"day": "2026-12-25"}   -> 500 UniqueViolation

Everything in this product soft-deletes (`deleted_at`), but these constraints
were plain UNIQUE, so a deleted row keeps holding its key forever. The
handler's `WHERE deleted_at IS NULL` lookup finds nothing, inserts, and hits
the constraint on a row it can't see. Same shape on three tables:

    holidays    re-adding a holiday you deleted
    users       re-inviting someone whose login was removed
    candidates  re-uploading a resume for a candidate who was deleted

The fix is the same everywhere: a partial unique index over live rows only.

Any duplicate that already exists among LIVE rows would block the index
build. There shouldn't be any (the old constraint was stricter than the new
one), so this deliberately does NOT clean up — if it fails, the right response
is to look at the data, not to have a migration delete rows unattended.

Revision ID: 0010_partial_unique
Revises: 0009_payroll
Create Date: 2026-08-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_partial_unique"
down_revision: str | None = "0009_payroll"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (constraint/index name, table, columns)
CONSTRAINTS = [
    ("uq_holiday_company_day", "holidays", ["company_id", "day"]),
    ("uq_user_email", "users", ["company_id", "email"]),
    ("uq_candidate_resume", "candidates", ["company_id", "resume_sha256"]),
]


def upgrade() -> None:
    for name, table, cols in CONSTRAINTS:
        op.drop_constraint(name, table, type_="unique")
        op.create_index(
            name, table, cols, unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    for name, table, cols in CONSTRAINTS:
        op.drop_index(name, table_name=table)
        op.create_unique_constraint(name, table, cols)
