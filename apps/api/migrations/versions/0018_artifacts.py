"""artifacts — generated files as records

Revision ID: 0018_artifacts
Revises: 0017_compensation_versions

Metadata only. The bytes live in object storage: a payroll register for 1,200
people does not belong in a Postgres row, and a database restore should not be
how you recover a challan receipt.

`storage_provider` is per row on purpose. A tenant that starts on local disk
and later moves to S3 must still be able to fetch what was written before the
move — a deployment-wide setting would orphan every older artifact.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0018_artifacts"
down_revision = "0017_compensation_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("scope_key", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("period", sa.Date(), nullable=True, index=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("payroll_runs.id"),
                  nullable=True, index=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("storage_provider", sa.String(10), nullable=False, server_default="local"),
        sa.Column("storage_key", sa.String(400), nullable=True),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(80), nullable=False,
                  server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Regenerating is additive: one row per (kind, scope, version), so v1 keeps
    # its bytes and its checksum when v2 is rendered.
    op.create_index(
        "uq_artifact_version", "artifacts",
        ["company_id", "kind", "scope_key", "version"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_artifact_lookup", "artifacts", ["company_id", "kind", "period"])

    op.execute("ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE artifacts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON artifacts "
        "USING (company_id = current_setting('app.company_id', true)::uuid) "
        "WITH CHECK (company_id = current_setting('app.company_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS artifacts CASCADE")
