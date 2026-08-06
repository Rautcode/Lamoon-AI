# ADR 0004 — Celery over Kafka for background work

**Status:** Accepted · 2026-08-06

## Context
The ATS pipeline is asynchronous: parse → screen (Gemini) → score → route →
email → reminders → auto-reject. Requirements include 3× retry with backoff,
idempotency, and "one bad resume never stops the queue." This is **task
processing**, not high-throughput event streaming.

## Decision
Celery with Redis as broker/result backend. Three priority queues (`high`,
`normal`, `background`). Domain events use an in-process dispatcher that fans
out to Celery tasks (see `ARCHITECTURE-2-PLATFORM.md` §1).

## Consequences
- Retries, backoff, scheduling (beat), and per-task isolation are built in —
  the reliability spec maps directly onto Celery features.
- Redis is already needed for cache/sessions; no new infrastructure.
- Workers scale horizontally where load concentrates (`high`/`normal`).
- Not an event log: no replay/retention of a stream. Acceptable — we don't need
  event sourcing at V1.

## Alternatives considered
- **Kafka** — rejected: it's an event-streaming platform for many independent
  consumers and high throughput. We have one consumer (the pipeline) in one
  process. Kafka adds a broker cluster, partitions, and consumer-group ops for
  no present benefit. **Trigger to adopt:** multiple separate services must each
  consume the same event stream independently (ATS → notifications → analytics →
  billing → webhooks).
- **RabbitMQ** — rejected: capable, but adds a broker where Redis (already
  present) suffices at this scale.
