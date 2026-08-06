# ADR 0002 — Shared-schema multi-tenancy with Postgres RLS

**Status:** Accepted · 2026-08-06

## Context
One deployment must serve many companies cheaply. Tenant data isolation is a
hard security requirement — a cross-tenant leak is existential for an HR SaaS
holding salary and personal data.

## Decision
Shared database, shared schema. Every tenant table carries `company_id`.
Isolation is enforced at three layers, with **Postgres Row-Level Security (RLS)
as the real guard**: each tenant table has a policy
`USING (company_id = current_setting('app.company_id')::uuid)`; a per-request
middleware runs `SET app.company_id = <jwt.company_id>`. JWT carries the tenant;
a SQLAlchemy base class adds `company_id` on write and a default read filter.

## Consequences
- One DB to run, back up, and migrate regardless of tenant count → lowest cost.
- A forgotten `WHERE company_id=` in application code still cannot leak data —
  RLS blocks it at the engine. Defense in depth.
- `company_id` (uuid) is the tenant key everywhere; connection pooling must set
  the GUC per transaction (noted for implementation).
- Noisy-neighbor risk is acceptable at SME row volumes; revisit only if a large
  tenant measurably degrades others.

## Alternatives considered
- **Database per tenant** — rejected: N databases to run and migrate; cost and
  ops explode at 100+ tenants.
- **Schema per tenant** — rejected: migration fan-out across N schemas, marginal
  isolation gain over RLS.
- **App-only filtering (no RLS)** — rejected: one missing clause = a breach.
