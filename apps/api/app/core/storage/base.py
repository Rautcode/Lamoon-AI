"""Storage seam (ADR-0006 as amended, platform §3).

**Nothing above this file knows where a byte lives.** Payroll and compliance
talk to `ArtifactService`, which talks to `BlobStore`, which talks to whatever
the deployment configured. Swapping S3 for MinIO, or a tenant onto their own
bucket, is a config change and not a code change.

Backends are chosen **per purpose**, because the two kinds of file this
product stores have different lives:

    ats       résumés and candidate documents — collaborative, shareable,
              short-lived. Google Drive, per the original ADR-0006.
    payroll   payslips, registers, ECR files, challans, acknowledgements —
              statutory records under a retention obligation, read by an
              auditor years later and never edited. S3-compatible.

A single `get_blob_store()` returning one implementation would force both into
whichever backend was chosen last.
"""
import hashlib
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> str: ...  # -> uri
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def url(self, key: str, *, ttl: int = 3600) -> str: ...


def checksum(data: bytes) -> str:
    """SHA-256 — the dedup gate, the AI cache key, and the thing that proves a
    file fetched next year is the artifact that was generated. Pure, so it's
    testable without any backend at all."""
    return hashlib.sha256(data).hexdigest()


class LocalBlobStore:
    """Dev and test. Real files on disk, so the whole flow runs with no cloud
    credentials and a failing test can be opened in an editor."""

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()  # absolute so blob URIs (.as_uri()) are valid

    def _p(self, key: str) -> Path:
        # A key is built by this application, never by a user, but a traversal
        # here would write outside the storage root — cheap to rule out.
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"blob key escapes the storage root: {key}")
        return p

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p.as_uri()

    async def get(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    async def exists(self, key: str) -> bool:
        return self._p(key).is_file()

    async def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    async def url(self, key: str, *, ttl: int = 3600) -> str:
        return self._p(key).as_uri()


class S3BlobStore:
    """S3-compatible object storage — AWS S3, MinIO, R2, Wasabi.

    `endpoint_url` is what makes "compatible" real: leave it empty for AWS,
    point it at MinIO in a self-hosted deployment. Nothing above this class
    changes either way.

    boto3 is imported inside the constructor so that dev, CI and every test
    run neither install-gate nor credential-gate on a backend they don't use.
    """

    def __init__(
        self, bucket: str, *, endpoint_url: str = "", region: str = "", prefix: str = ""
    ) -> None:
        import boto3  # noqa: PLC0415 — deliberately lazy, see the docstring

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region or None,
        )

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self._client.put_object(
            Bucket=self.bucket, Key=self._k(key), Body=data, ContentType=content_type
        )
        return f"s3://{self.bucket}/{self._k(key)}"

    async def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=self._k(key))
        return bytes(obj["Body"].read())

    async def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError  # noqa: PLC0415

        try:
            self._client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError:
            return False

    async def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=self._k(key))

    async def url(self, key: str, *, ttl: int = 3600) -> str:
        """A pre-signed, time-limited URL.

        The bucket stays private: a payslip is not a public object, and a
        permanent link to one is a leak waiting for somebody to forward an
        email.
        """
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": self._k(key)},
                ExpiresIn=ttl,
            )
        )


class DriveBlobStore:
    """Google Drive, for ATS documents (ADR-0006). Still unimplemented — ATS
    runs on LocalBlobStore today, which predates this change."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise NotImplementedError("DriveBlobStore: implement when ATS moves off local storage")

    async def get(self, key: str) -> bytes:
        raise NotImplementedError

    async def exists(self, key: str) -> bool:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def url(self, key: str, *, ttl: int = 3600) -> str:
        raise NotImplementedError


def get_blob_store(purpose: str = "payroll") -> BlobStore:
    """The store for a purpose — "payroll" or "ats".

    Defaults to local everywhere, so a fresh checkout and CI work with no
    configuration at all. Production sets the backend per purpose.
    """
    s = get_settings()
    backend = s.storage_backend_ats if purpose == "ats" else s.storage_backend_payroll

    if backend == "s3":
        return S3BlobStore(
            s.s3_bucket, endpoint_url=s.s3_endpoint_url,
            region=s.s3_region, prefix=s.s3_prefix,
        )
    if backend == "drive":
        return DriveBlobStore()
    return LocalBlobStore(s.storage_dir)
