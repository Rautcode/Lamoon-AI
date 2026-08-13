# ADR 0006 — Google Drive before S3

**Status:** Accepted · 2026-08-06 · **Amended 2026-08-13 — see the amendment
at the foot of this file.** The decision below still stands for ATS documents;
payroll and compliance artifacts now go to S3-compatible object storage.

## Context
Resumes, payslips, and documents need durable blob storage with shareable
links. Early on, spend and setup friction matter more than throughput. The spec
names Drive initially, S3-compatible later.

## Decision
Google Drive API as the V1 blob backend, behind a `BlobStore` interface
(platform §3). Resume naming `Name_Role_Resume.pdf`, SHA-256 checksum dedup.

## Consequences
- Zero storage bill early (Drive quota), simple OAuth already in the stack for
  Google Login and Gmail intake.
- Shareable links for HR to view resumes without building a viewer.
- All access goes through `BlobStore` — no code knows it's Drive.
- Drive API has rate/quota limits and is not built for high-QPS object serving;
  fine at SME volumes, a known ceiling.

## Alternatives considered
- **S3 / MinIO now** — rejected for V1: introduces a paid service (or self-hosted
  MinIO ops) before it's needed. **Trigger to switch:** Drive quotas or
  signed-URL latency become real, or an enterprise tenant requires object-lock /
  region control. The swap is one `BlobStore` implementation.

---

## Amendment · 2026-08-13 — split the backend by purpose

**Status:** Accepted.

### What changed
The original decision assumed one blob backend for the whole product. Payroll
made that assumption wrong before Drive was ever implemented.

The two kinds of file this product stores have different lives:

| | ATS documents | Payroll / compliance artifacts |
|---|---|---|
| Examples | résumés, offer letters, interview attachments | payslips, registers, ECR files, challans, acknowledgements |
| Read by | recruiters, hiring managers, now | an auditor, years later |
| Edited | often | never |
| Shared | yes, that is the point | no — a permanent link to a payslip is a leak |
| Obligation | none | statutory retention |

A payslip is not a document people collaborate on; it is a record somebody has
to be able to produce unchanged on demand. Drive's strengths — shareable links,
collaborative access, a free quota — are the wrong strengths for that, and its
weaknesses (rate limits, no object-lock, no region control) sit exactly where
compliance evidence needs guarantees.

The original trigger listed *"an enterprise tenant requires object-lock /
region control"*. Statutory retention is that requirement, arriving from the
domain rather than from a customer.

### The amended decision
- **ATS** stays on Drive, per the decision above.
- **Payroll and compliance** use **S3-compatible** object storage — AWS S3,
  MinIO, R2 or Wasabi. `s3_endpoint_url` is what makes "compatible" real: empty
  for AWS, set for a self-hosted MinIO.
- The choice is per purpose: `get_blob_store("payroll")` / `get_blob_store("ats")`,
  configured by `storage_backend_payroll` and `storage_backend_ats`. **Both
  default to `local`**, so a fresh checkout and CI need no credentials.
- Every artifact row stores the `storage_provider` it was written with, so a
  tenant that starts on local disk and later moves to S3 can still fetch what
  was written before the move. A deployment-wide setting would orphan them.
- Domain code depends on `ArtifactService`, never on `BlobStore` and never on
  `boto3`. The moment a payroll module imports boto3, the storage decision is
  welded into the domain.

### Consequences
- `boto3` becomes a dependency, imported lazily inside `S3BlobStore` so nothing
  install-gates or credential-gates on a backend it does not use. Rejected the
  alternative of signing SigV4 by hand over the existing `httpx`: ~60 lines of
  crypto-adjacent code whose failures are silent and miserable to debug.
- `DriveBlobStore` is **still unimplemented**. ATS runs on `LocalBlobStore`
  today; that predates this amendment and is unchanged by it.
- **`S3BlobStore` has not been verified against a live endpoint.** Its key
  construction, checksums and lifecycle are tested; the boto3 calls are not.
  Verifying it means running MinIO in CI, which is the next storage task.
