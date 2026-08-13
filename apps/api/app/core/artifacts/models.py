"""Generated files, as records.

The database holds **metadata only**. The bytes live in object storage, because
a payroll register for 1,200 people is not something a Postgres row should
carry, and because a database restore should not be the way you recover a
challan receipt.

What makes this a record rather than a download:

  provenance   which run, which period, who asked, when
  checksum     proof that the file fetched in two years is the one generated
  provider     WHERE it was stored, per row — a tenant that moves from local
               to S3 must still be able to fetch what was written before the
               move
  version      artifacts are immutable; regenerating makes v2, it does not
               overwrite v1

That last one is the point. An artifact attached to a finalized payroll run is
evidence. Silently replacing the file behind a link somebody already has is
how evidence stops being evidence.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase

#: Lifecycle. `expired` is set by a sweep once `expires_at` passes — the row
#: survives so the audit trail still shows the file existed and who made it.
STATUSES = ("queued", "generating", "ready", "failed", "expired")

#: What a file IS. Extend as renderers land; the storage layer is indifferent.
KINDS = (
    "payroll_register",
    "payroll_summary",
    "payslip",
    "payslip_bundle",
    "contractor_reconciliation",
)


class Artifact(TenantBase):
    __tablename__ = "artifacts"
    __table_args__ = (
        # One row per (kind, scope, version). The version is what makes
        # regeneration additive instead of destructive.
        Index(
            "uq_artifact_version", "company_id", "kind", "scope_key", "version",
            unique=True, postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_artifact_lookup", "company_id", "kind", "period"),
    )

    kind: Mapped[str] = mapped_column(String(40))
    #: Deterministic identity for the thing being rendered, e.g. "run:<uuid>".
    #: One column doing the job of a variable set of nullable foreign keys —
    #: an artifact can be scoped to a run, a period, an employee or an
    #: obligation, and only some of those tables exist yet.
    scope_key: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)

    period: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payroll_runs.id"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(12), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Where the bytes went. Per row, not per deployment: after a migration
    #: from local to S3 the old rows still know where to look.
    storage_provider: Mapped[str] = mapped_column(String(10), default="local")
    storage_key: Mapped[str | None] = mapped_column(String(400), nullable=True)

    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(80), default="application/octet-stream")
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
