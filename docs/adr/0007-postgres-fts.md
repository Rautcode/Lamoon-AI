# ADR 0007 — Postgres FTS over Elasticsearch

**Status:** Accepted · 2026-08-06

## Context
"Everything searchable" — candidates by skill, employees by name, applications
by attribute. At SME scale a tenant's corpus is thousands to low-millions of
rows, not a web-scale index.

## Decision
Postgres full-text search (`tsvector` + GIN) plus `pg_trgm` for fuzzy name/skill
matching, behind a `Search` interface (platform §6). No separate search engine.

## Consequences
- No new service to run, sync, or secure — search lives in the DB that already
  holds the data, inside the same RLS tenant boundary (no separate index to
  leak across tenants).
- Good-enough relevance and typo tolerance for HR search at this scale.
- Heavy relevance ranking / semantic search is limited; embeddings
  (`text-embedding-004`) already cover semantic JD-matching separately.
- Index maintenance is just DB indexes.

## Alternatives considered
- **Elasticsearch / OpenSearch** — rejected: a cluster to run, secure, and keep
  in sync, plus a second copy of tenant data to isolate. **Trigger to adopt:**
  relevance/typo quality becomes a *business* problem on a genuinely large
  corpus. Then add an impl behind `Search`.
- **Meilisearch** — noted as the lighter first step if that trigger fires, before
  reaching for ES.
