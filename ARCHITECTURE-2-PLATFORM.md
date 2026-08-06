# Lamoon HR — Architecture v2: Platform / Cross-Cutting Layer

Companion to `ARCHITECTURE.md`. Adds the ten cross-cutting concerns a CTO wants
settled before coding. **Governing principle:** each is added as a *seam*
(interface + smallest real implementation) so the heavy version drops in later
without touching business logic. None of these pulls in new infrastructure at
V1 — no Kafka, no Elasticsearch, no flag SaaS, no payment integration yet. The
seam is the deliverable; the trigger for the heavy version is named each time.

## Summary

| # | Concern | V1 implementation | Seam | Heavy version — trigger |
|---|---|---|---|---|
| 1 | Event bus | In-process dispatcher + Celery fan-out | `events.publish(DomainEvent)` | Redis Streams / Kafka — when a *separate service* must consume |
| 2 | AI platform | `GeminiProvider` behind one interface | `AIProvider` | Multi-model router — when a 2nd model earns its place |
| 3 | Storage | Google Drive | `BlobStore` | S3 — when Drive quotas/latency bite |
| 4 | Notification | Email (SMTP/Graph/Gmail) | `Notifier` + channel | SMS/Slack/in-app — when a channel is asked for |
| 5 | Feature flags | `feature_flags` table + helper | `flags.enabled(key, company)` | Flag SaaS — never, probably |
| 6 | Search | Postgres FTS (tsvector + trigram) | `Search` | Meilisearch/ES — when FTS misses relevance |
| 7 | Billing | Entitlement engine (typed entitlements), no gateway | `PaymentGateway` | Razorpay — when self-serve checkout ships |
| 8 | Queue strategy | 3 Celery queues + retry/idempotency rules | (config) | Priority broker — at worker-scale pain |
| 9 | Prompt versioning | Versioned prompt registry, stamped on output | `prompts.get(key, ver)` | A/B prompt eval — when tuning at scale |
| 10 | Correlation + audit | `correlation_id` end-to-end, enriched audit | middleware + Celery header | Tracing (OTel) — when multi-service |

---

## 1. Event bus — dispatcher now, broker later

Domain events (`ApplicationScored`, `CandidateRejected`, `EmployeeJoined`)
decouple producers from side effects (email, audit, analytics). But a message
broker between packages *in one process* is infrastructure for a problem you
don't have.

```python
# core/events.py
@dataclass
class DomainEvent:
    name: str; company_id: UUID; payload: dict; correlation_id: str

def publish(e: DomainEvent) -> None:
    audit.record(e)                     # always
    for handler in _subscribers[e.name]:
        celery_dispatch.delay(handler, e)   # async side effects
```

Handlers subscribe by event name. **Trigger for a real broker (Redis Streams,
then Kafka):** the day a *separate deployable* (analytics, a second service)
must consume these — not before. Publishers already speak `publish()`, so that
swap is one function body.

## 2. AI platform layer — the moat's plumbing

Everything AI goes through one interface, and **prompts never live in business
logic**. The internal chain is explicit — each stage its own seam:

```
AIProvider.analyze()
  → PromptRegistry.get(key, ver)      # prompt is data, versioned (§9)
  → Model (Gemini Flash | Pro)        # selection + retries + cost
  → OutputParser (schema-validated)   # raw text → typed AIResult, never trust free text
  → Cache (by cache_key)              # 0-cost on hit
```

```python
# core/ai/provider.py
class AIProvider(Protocol):
    async def analyze(self, *, prompt_key: str, prompt_ver: str,
                      inputs: dict, output_schema: type, cache_key: str | None) -> AIResult: ...
    async def embed(self, texts: list[str]) -> list[Vector]: ...

# AIResult carries: output (parsed to output_schema), model, prompt_ver,
#                   tokens_in/out, cost, cache_hit
```

`ats/ai.py` calls `analyze(prompt_key="resume_screen", output_schema=Screening)`
and gets a validated object back — it never sees a prompt string, a model name,
or raw model output.

V1 impl `GeminiProvider`:
- **Cache first** on `cache_key` (= `resume_sha256 + prompt_ver`) → 0-cost on hit.
- **Flash by default**; `tier="deep"` selects Pro and debits AI credits.
- Every call records tokens + cost to `ai_usage` (feeds billing §7).
- 3× exponential backoff on transient errors (shared retry util, §8).

**Trigger for a multi-model router:** a second model measurably beats Gemini on
a real task. Until then, one provider, no router.

## 3. Storage abstraction — `BlobStore`

```python
# core/storage/base.py
class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> str: ...  # returns url
    async def get(self, key: str) -> bytes: ...
    async def url(self, key: str, *, ttl: int = 3600) -> str: ...
    async def checksum(self, data: bytes) -> str: ...  # sha256, dedup gate
```

V1 = `DriveBlobStore` (Google Drive, naming `Name_Role_Resume.pdf`, checksum
dedup per spec). **Trigger for `S3BlobStore`:** Drive API quotas or signed-URL
latency become real. Application code only ever calls `BlobStore` — the resume
archive, payslips, and documents all ride it.

## 4. Notification abstraction — `Notifier`

```python
# core/notify/base.py
class Notifier(Protocol):
    async def send(self, *, to: str, template: str, ctx: dict,
                   channel: str = "email") -> None: ...
```

V1 = email only, provider-pluggable underneath (SMTP / Microsoft Graph / Gmail
API) selected per company. Templates versioned in the DB (admin-editable, per
spec). Candidate emails, HR alerts, interview links all go through `send()`.
**Trigger for SMS/Slack/in-app:** a customer asks. New channel = new impl, same
interface, zero caller changes.

## 5. Feature flags — one table, not a service

Two *different* things people lump as "flags":
- **Entitlements** (what you paid for) → already `company_modules` (§ARCH-1).
- **Ops flags** (kill-switch, gradual rollout, per-tenant beta) → this.

```python
# core/flags.py  — backed by a `feature_flags(company_id, key, enabled)` table
def enabled(key: str, company_id: UUID) -> bool: ...
```

That's the whole thing: a table, a cached helper, an admin toggle. **A flag SaaS
(LaunchDarkly) is almost certainly never justified** at this scale — the trigger
would be needing percentage rollouts across many services, which the monolith
doesn't have.

## 6. Search abstraction — Postgres FTS behind an interface

Spec says "everything searchable." That is **not** a reason to run a search
engine. Postgres `tsvector` + GIN (full-text) and `pg_trgm` (fuzzy name/skill)
cover candidate and employee search well past 500-employee tenants.

```python
# core/search/base.py
class Search(Protocol):
    async def index(self, doc: SearchDoc) -> None: ...
    async def query(self, *, company_id: UUID, q: str, kind: str) -> list[Hit]: ...
```

V1 = `PgSearch` (a generated `tsvector` column + trigram indexes; `index()` is a
no-op since it's in-row). **Trigger for Meilisearch/ES:** relevance ranking or
typo-tolerance that FTS can't do, on a corpus that's actually large. The
interface means that's an implementation swap, not a rewrite.

## 7. Billing & entitlement engine — model now, gateway later

Reframed per review: billing is not "subscription". The core is an **entitlement
engine** answering one question everywhere — *can this company use this, right
now, within limits?* Pricing then changes as **data, never schema**.

```
plans           (key, name, price_per_employee)              # marketing/pricing
subscriptions   (company_id, plan_key, status, current_period_end)
entitlements    (company_id, key, type, value)               # the generic core
                # type ∈ boolean | limit | quota ; value is jsonb
                #   ("module.ats", boolean, true)
                #   ("employees",  limit,   100)
                #   ("storage_gb",  limit,   20)
                #   ("ai_credits",  quota,   500)   ← consumable
ai_usage        (company_id, application_id, tokens, cost, model, created_at)
credit_ledger   (company_id, entitlement_key, delta, reason, balance_after)
```

The engine — one call guards every gated action:

```python
# core/billing/entitlements.py
def can_use(company_id, key, *, amount=1) -> Decision:
    ent = get(company_id, key)
    match ent.type:
        case "boolean": return Decision(ent.value is True)
        case "limit":   return Decision(current_count(company_id, key) + amount <= ent.value)
        case "quota":   return Decision(balance(company_id, key) >= amount)  # debits on commit
```

- Module gate (§ARCH-1) becomes `can_use(cid, "module.ats")` — same engine.
- Employee create → `can_use(cid, "employees")`. Resume deep-dive →
  `can_use(cid, "ai_credits", amount=cost)` then debit `credit_ledger`.
- A new plan or a promo = inserting `entitlements` rows. **No migration, ever.**

Invoicing/checkout sits behind a `PaymentGateway` Protocol.

```python
class PaymentGateway(Protocol):
    async def create_subscription(self, company, plan) -> str: ...
    async def charge(self, company, amount, meta) -> Receipt: ...
```

V1 = `ManualGateway` (ops-provisioned, invoice offline). **Trigger for
`RazorpayGateway`** (India-first: UPI/cards, GST-compliant invoices): self-serve
signup. Model doesn't change; an implementation lands.

## 8. Queue strategy — topology + reliability rules

No new infra (Celery + Redis already chosen). This is the *contract* the spec's
reliability requirements map onto:

- **Three queues by priority:** `high` · `normal` · `background`.
  Resume AI/OCR → `high`; emails/notifications → `normal`; cleanup/dedup/
  reminders → `background`. `high` and `normal` workers scale horizontally.
- **Retries:** 3× exponential backoff on every external call (Gemini, Drive,
  SMTP, Calendar) — one shared `@retryable` decorator.
- **Idempotency:** every task keyed (e.g. `application_id + step`); re-delivery
  is a no-op. `POST /apply` carries an idempotency key → no duplicate pipelines.
- **Isolation:** one bad resume fails its own task only; the queue continues.
  On unhandled failure → `Notifier` alerts HR with workflow/node/stack/resume/ts
  (per spec's error-handler workflow).
- **Dead-letter:** exhausted retries land in a `failed_tasks` table for replay.

**Trigger for a priority broker / autoscaling:** worker backlog that queue
separation can't absorb — a scale problem, addressed when measured.

## 9. Prompt versioning — registry + stamp

Prompts are code and must be versioned, because a score is only meaningful next
to the prompt that produced it.

- Prompts live in `core/ai/prompts/<key>/<version>.txt` (in VCS) with a small
  `prompts.get(key, ver)` loader; the active version per key is a config value.
- **A run is reproducible only if its full recipe is recorded.** Each analysis
  stores the recipe and a hash of it:

  ```
  prompt_run (id, prompt_key, prompt_version, output_schema_version,
              model, temperature, recipe_hash)
  recipe_hash = sha256(prompt_text + output_schema + model + temperature)
  ```

  `ai_analyses.prompt_run_id` links every score to the exact recipe that made
  it. Same `recipe_hash` + same `resume_sha256` = identical result, guaranteed.
- Re-screen (`POST /applications/{id}/screen`) can pin a version → reproducible.
- Cache key = `resume_sha256 + recipe_hash`, so any recipe change (prompt,
  model, temperature, or output schema) correctly invalidates.

**Trigger for A/B prompt eval tooling:** systematic prompt tuning against a
golden set — a later optimization, not a V1 subsystem.

## 10. Correlation IDs + richer audit

- **Five identifiers** travel together in a `ContextVar`, on every log line, as
  Celery task headers, and on every `DomainEvent` (§1):

  ```
  request_id      — one HTTP request (minted at gateway / from X-Request-ID)
  correlation_id  — one logical flow across async hops (a whole ATS pipeline)
  tenant_id       — company_id, on every line (multi-tenant triage)
  user_id         — actor, when authenticated
  job_id          — the Celery task, for worker-side tracing
  ```

  `request_id` scopes a single call; `correlation_id` stitches the pipeline's
  many tasks into one debuggable story.
- **Audit enrichment** — `audit_events` gains: `correlation_id`, `actor_ip`,
  `user_agent`, `source` (email/webhook/ui), and keeps the `jsonb` before/after
  payload. The ATS "23+ field" audit requirement is satisfied by the event row
  + the linked `ai_analyses` row, not 23 literal columns.

**Trigger for distributed tracing (OpenTelemetry):** the day there's more than
one service to trace across. In the monolith, correlation_id + structured logs
are enough.

---

## Net effect on the codebase

Six new interfaces (`AIProvider`, `BlobStore`, `Notifier`, `Search`,
`PaymentGateway`, `IdentityProvider` from v1), two helpers (`events`, `flags`),
and a handful of tables (`feature_flags`, billing set, `ai_usage`,
`failed_tasks`, audit columns). Zero new runtime services beyond Postgres +
Redis. Every "later" is a named trigger and a one-place swap — which is the
whole point of settling them now instead of retrofitting.

**This is the implementation baseline.** On approval, scaffolding follows
`ARCHITECTURE.md` §4, with these seams stubbed from day one.
