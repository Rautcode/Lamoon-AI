"""Storage seam (ADR-0006, platform §3). Dev uses local FS so the flow runs
without cloud creds; Drive/S3 slot in behind the same BlobStore.
"""
import hashlib
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> str: ...  # -> url
    async def get(self, key: str) -> bytes: ...
    async def url(self, key: str, *, ttl: int = 3600) -> str: ...


def checksum(data: bytes) -> str:
    """SHA-256 — the dedup gate and AI cache key. Pure, so it's testable now."""
    return hashlib.sha256(data).hexdigest()


class LocalBlobStore:
    """Dev impl: write under settings.storage_dir. ponytail: swap for DriveBlobStore in V1 prod."""

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()  # absolute so blob URIs (.as_uri()) are valid

    def _p(self, key: str) -> Path:
        return self.root / key

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p.as_uri()

    async def get(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    async def url(self, key: str, *, ttl: int = 3600) -> str:
        return self._p(key).as_uri()


class DriveBlobStore:
    """V1 prod impl over Google Drive API (naming Name_Role_Resume.pdf, checksum dedup)."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise NotImplementedError  # ponytail: implement when moving off local dev storage

    async def get(self, key: str) -> bytes:
        raise NotImplementedError

    async def url(self, key: str, *, ttl: int = 3600) -> str:
        raise NotImplementedError


def get_blob_store() -> BlobStore:
    return LocalBlobStore(get_settings().storage_dir)
