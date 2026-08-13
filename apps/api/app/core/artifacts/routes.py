"""Artifact endpoints.

Authorization is re-checked at DOWNLOAD, not only at generation. An artifact
id is a handle, never a capability — somebody whose access was revoked last
week must not still be able to pull August's register with a link they kept.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.artifacts import service
from app.core.artifacts.models import Artifact
from app.core.db import get_db
from app.core.rbac import require
from app.core.tenant import resolve_tenant

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
CAN_READ = [Depends(require("payroll.read"))]


class ArtifactOut(BaseModel):
    id: uuid.UUID
    kind: str
    version: int
    status: str
    filename: str
    content_type: str
    size_bytes: int | None
    checksum_sha256: str | None
    period: str | None
    generated_at: datetime | None
    error: str | None

    model_config = {"from_attributes": True}


def _out(a: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=a.id, kind=a.kind, version=a.version, status=a.status,
        filename=a.filename, content_type=a.content_type, size_bytes=a.size_bytes,
        checksum_sha256=a.checksum_sha256,
        period=a.period.isoformat() if a.period else None,
        generated_at=a.generated_at, error=a.error,
    )


@router.get("", response_model=list[ArtifactOut], dependencies=CAN_READ)
def list_artifacts(
    kind: str | None = None,
    run_id: uuid.UUID | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _cid: str = Depends(resolve_tenant),
):
    """Newest first. Bounded by default — an artifacts list grows forever and
    nobody scrolls to 2024."""
    stmt = select(Artifact).where(Artifact.deleted_at.is_(None))
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    if run_id:
        stmt = stmt.where(Artifact.run_id == run_id)
    rows = db.scalars(
        stmt.order_by(Artifact.created_at.desc()).limit(max(1, min(limit, 200)))
    ).all()
    return [_out(a) for a in rows]


@router.get("/{artifact_id}", response_model=ArtifactOut, dependencies=CAN_READ)
def get_artifact(
    artifact_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    row = db.get(Artifact, artifact_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "artifact not found")
    return _out(row)


@router.get("/{artifact_id}/download", dependencies=CAN_READ)
async def download(
    artifact_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    """The bytes, checked against the checksum recorded when they were written.

    RLS scopes the lookup, so another tenant's id simply does not resolve.
    """
    row = db.get(Artifact, artifact_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "artifact not found")
    try:
        data = await service.read(row)
    except service.ArtifactFailed as e:
        raise HTTPException(409, str(e)) from None
    return Response(
        content=data,
        media_type=row.content_type,
        headers={"Content-Disposition": f'attachment; filename="{row.filename}"'},
    )
