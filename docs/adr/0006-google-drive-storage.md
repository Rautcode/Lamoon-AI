# ADR 0006 — Google Drive before S3

**Status:** Accepted · 2026-08-06

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
