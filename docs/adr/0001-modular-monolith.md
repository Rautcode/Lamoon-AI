# ADR 0001 — Modular monolith over microservices

**Status:** Accepted · 2026-08-06

## Context
Target tenants are Indian SMEs (20–500 employees). The team is small and pre-
revenue. The spec says modules must be "independently deployable later." The
real early risk is shipping speed and operational simplicity, not per-module
scale.

## Decision
Build one FastAPI application, one Postgres database. Modules are Python
packages under `modules/<name>/` with enforced import boundaries (a module
imports only `core/` and itself). Only the AI/email workers run as a separately
scalable tier (Celery).

## Consequences
- One deploy, one DB migration, one place to debug. Fast iteration.
- "Independently deployable later" is preserved by boundaries: extracting a
  module to its own service is a move (it already has a clean interface), not a
  rewrite.
- Discipline required: no cross-module imports. Enforced in review / lint.
- If one module ever needs independent scale, split just that one — the event
  seam (ADR-0004) already supports out-of-process consumers.

## Alternatives considered
- **Microservices now** — rejected: buys independent scaling/deploys we don't
  need at this size, costs a service mesh, N databases, distributed tracing, and
  slow local dev. Premature.
- **Unstructured monolith** — rejected: no boundaries means the "extract later"
  option is lost; you get a big ball of mud.
