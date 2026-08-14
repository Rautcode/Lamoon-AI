"""inbox items — what needs one particular person

Revision ID: 0020_inbox
Revises: 0019_calendar_assignment

`modules/notifications/` was an empty file. Everything the product could send
was outbound email to somebody OUTSIDE the workspace — a candidate, a new hire's
password — and nothing could tell a person inside it that something needed
them.

The partial unique index is the load-bearing part: one OPEN item per (person,
kind, thing). Without it a nightly re-derivation produces a row and an email
every night for the same unresolved problem, which trains people to ignore the
inbox — the exact failure this is meant to fix.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0020_inbox"
down_revision = "0019_calendar_assignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbox_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("subject_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"),
                  nullable=False, index=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("scope_key", sa.String(120), nullable=False, server_default=""),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(10), nullable=False, server_default="review"),
        sa.Column("entity", sa.String(40), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("href", sa.String(200), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("state", sa.String(10), nullable=False, server_default="open"),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", UUID(as_uuid=True), nullable=True),
        sa.Column("dismissed_reason", sa.Text(), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # One OPEN item per person per thing. Partial on state rather than on
    # deleted_at, so the same problem recurring next month legitimately opens a
    # new item while the resolved one stays as a record.
    op.create_index(
        "uq_inbox_open", "inbox_items",
        ["company_id", "subject_user_id", "kind", "dedupe_key"],
        unique=True, postgresql_where=sa.text("state = 'open'"),
    )
    op.create_index(
        "ix_inbox_mine", "inbox_items", ["company_id", "subject_user_id", "state"]
    )
    op.create_index(
        "ix_inbox_scope", "inbox_items", ["company_id", "kind", "scope_key", "state"]
    )

    op.execute("ALTER TABLE inbox_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inbox_items FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON inbox_items "
        "USING (company_id = current_setting('app.company_id', true)::uuid) "
        "WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inbox_items CASCADE")
