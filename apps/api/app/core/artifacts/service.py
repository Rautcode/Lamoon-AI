"""ArtifactService — the only thing payroll and compliance talk to.

The domain says "render this register and keep it". It does not know about
buckets, keys, checksums or presigned URLs, and it must not: the moment a
payroll module imports boto3, the storage decision is welded into the domain.

Failure is explicit. A generation that raises leaves the row `failed` with the
error on it, never a half-written `ready` row pointing at bytes that were
never uploaded. Payroll is financial software; a silent success is worse than
a loud failure.
"""
import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.artifacts.models import Artifact
from app.core.config import get_settings
from app.core.storage.base import checksum, get_blob_store


class ArtifactFailed(Exception):
    """Generation failed. The row records why."""


def _scope_hash(scope_key: str) -> str:
    """Short, stable, filesystem-safe. Deterministic so the same scope always
    lands at the same place and a key can be reconstructed from the row."""
    return hashlib.sha256(scope_key.encode()).hexdigest()[:12]


def storage_key(
    *, company_id: uuid.UUID, kind: str, period: date | None, scope_key: str,
    version: int, extension: str,
) -> str:
    """Deterministic, and readable when somebody is staring at a bucket
    listing trying to find August's register."""
    when = period.strftime("%Y-%m") if period else "all"
    return f"{company_id}/{kind}/{when}/{_scope_hash(scope_key)}-v{version}.{extension}"


def next_version(db: Session, *, company_id: uuid.UUID, kind: str, scope_key: str) -> int:
    """Regenerating never overwrites. An artifact somebody already has a link
    to keeps its bytes and its checksum; the new render is v2."""
    latest = db.scalar(
        select(Artifact.version)
        .where(
            Artifact.company_id == company_id,
            Artifact.kind == kind,
            Artifact.scope_key == scope_key,
            Artifact.deleted_at.is_(None),
        )
        .order_by(Artifact.version.desc())
        .limit(1)
    )
    return (latest or 0) + 1


async def generate(
    db: Session,
    *,
    company_id: uuid.UUID,
    kind: str,
    scope_key: str,
    render: Callable[[], tuple[bytes, str, str]],
    period: date | None = None,
    run_id: uuid.UUID | None = None,
    filename: str | None = None,
    generated_by: uuid.UUID | None = None,
) -> Artifact:
    """Render, store, and record — in that order, with the row created first.

    `render` returns (bytes, content_type, extension) and is called INSIDE the
    try, so a renderer that raises produces a `failed` artifact somebody can
    see and retry rather than a silent absence.

    Synchronous today, deliberately: the first artifacts are small and the
    Celery job wrapper is a later increment. The signature already takes
    everything a task would need, so moving it is a call-site change.
    """
    version = next_version(db, company_id=company_id, kind=kind, scope_key=scope_key)
    settings = get_settings()
    provider = settings.storage_backend_payroll

    row = Artifact(
        company_id=company_id, kind=kind, scope_key=scope_key, version=version,
        period=period, run_id=run_id, status="generating",
        storage_provider=provider, filename=filename or f"{kind}-{version}",
        # TenantBase already carries `created_by`; "who generated this" and
        # "who created this row" are the same fact and do not need two columns.
        created_by=generated_by,
    )
    db.add(row)
    db.flush()

    try:
        data, content_type, extension = render()
        key = storage_key(
            company_id=company_id, kind=kind, period=period,
            scope_key=scope_key, version=version, extension=extension,
        )
        await get_blob_store("payroll").put(key, data, content_type=content_type)
    except Exception as e:
        row.status = "failed"
        row.error = f"{type(e).__name__}: {e}"[:2000]
        db.flush()
        raise ArtifactFailed(row.error) from e

    row.storage_key = key
    row.content_type = content_type
    row.size_bytes = len(data)
    row.checksum_sha256 = checksum(data)
    row.filename = filename or f"{kind}-{(period or date.today()).strftime('%Y-%m')}.{extension}"
    row.status = "ready"
    row.generated_at = datetime.now(UTC)
    db.flush()
    return row


async def read(artifact: Artifact) -> bytes:
    """The bytes, verified against the checksum recorded when they were
    written.

    Checking costs a hash over a file that was just fetched, and buys the one
    thing an evidence store has to be able to say: this is the same file.
    """
    if artifact.status != "ready" or not artifact.storage_key:
        raise ArtifactFailed(f"artifact is {artifact.status}, not ready to download")

    data = await get_blob_store("payroll").get(artifact.storage_key)
    if artifact.checksum_sha256 and checksum(data) != artifact.checksum_sha256:
        raise ArtifactFailed(
            "stored file does not match the checksum recorded when it was generated — "
            "it has been altered or corrupted since"
        )
    return data
