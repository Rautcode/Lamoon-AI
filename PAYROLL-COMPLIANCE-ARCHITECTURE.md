# Payroll & Compliance — Architecture Assessment and Plan

**Status:** Phase 0 deliverable. No code has been changed.
**Audit date:** 13 August 2026, against `main` @ `6127629`.
**Method:** every claim was checked against code, schema, a measurement, or an
existing architecture document. Absences are marked **MISSING**, not described
in the future tense. Numbers are measured, not estimated.

**Prior art read before writing this:** `ARCHITECTURE.md`,
`ARCHITECTURE-2-PLATFORM.md`, `DESIGN.md`, and ADRs 0001–0008. Several
conclusions below changed because of them, and §2 records where this plan is
constrained by decisions already accepted.

---

## 1. Executive assessment

The payroll **calculation core is sound** and unusually well-founded: work
facts → input ledger → statutory wage basis → period-resolved rule version →
deterministic calculation, with `Decimal` money end-to-end and provenance
stamped into every payslip. That chain is real, tested (233 payroll tests of
373 total), and should not be rebuilt.

**Everything around the calculation is missing or wrong.** The run executes
inside the HTTP request, nothing can be exported, nothing can be searched,
compensation overwrites its own history, attendance is ignored entirely, and
compliance does not exist as a concept.

One sentence: **this is a correct payroll calculator inside an HRMS; it is not
yet a payroll system.** The brief's §2 distinction between calculation and
compliance operations is exactly the right frame, and it is the gap.

**Ranked by severity, the five things that matter:**

1. Payroll does not read attendance — a full month of no-shows is paid in full (§4.1).
2. Compensation overwrites; there is no salary history (§4.2).
3. A module import cycle and misplaced table ownership between payroll and hr_core (§4.3).
4. Synchronous runs at ~14 queries/employee, ceiling ≈1,000 people (§4.4).
5. No exports, no search, no artifacts — three subsystems the brief assumed existed (§4.5).

---

## 2. Reconciliation with the accepted architecture

This is not a greenfield design. Eight ADRs and two architecture documents are
already accepted, and this plan is constrained by them.

### 2.1 Decisions this plan adopts unchanged

| Decision | Source | Bearing on payroll |
|---|---|---|
| Modular monolith, one FastAPI app, one Postgres | ADR-0001 | No payroll microservice. Ever, on current evidence |
| Shared schema + RLS, three enforcement layers | ADR-0002 | All new tables inherit it |
| Celery + Redis, queues `high`/`normal`/`background` | ADR-0004 | Payroll calc → `high`; artifacts → `normal` |
| Postgres FTS + `pg_trgm` behind a `Search` interface | ADR-0007 | No Elasticsearch for payroll search |
| In-app RBAC as a dict until custom roles are sold | ADR-0008 + code comment | New roles are dict entries, not tables |
| 3× retry, per-task idempotency keys, `failed_tasks` dead-letter | Platform §8 | The payroll job adopts this contract, does not invent one |
| Five-identifier context; audit keeps a **before/after** jsonb payload | Platform §10 | §14 is implementation lag, not new design |

**ADR-0008 is not drift.** I initially read the hardcoded `ROLE_PERMISSIONS`
dict as divergence from the ADR's `roles`/`role_permissions` tables. The code
comment states the reasoning explicitly: four fixed V1 roles are a dict; the
tables were designed for when tenants define *custom* roles. That trigger has
not fired. New roles in §15 are therefore dict entries.

### 2.2 Accepted decisions this plan proposes to amend

Two, both with reasons.

**ADR-0001's module boundary rule is unworkable as written.** It states a
module "imports only `core/` and itself," enforced in review. Measured reality:
**48 cross-module imports across 9 modules — every module violates it.** But
the rule is what is wrong, not the code. Payroll *must* read `Employee` and
`LeaveRequest`; the brief's §1 explicitly demands it ("the HRMS is the system of
record"). A rule that forbids the product's core data flow will be ignored, and
a rule that is routinely ignored stops protecting anything.

Proposed amendment: replace "no cross-module imports" with a **declared
dependency DAG** — a module may import models (read-only) from a declared
upstream, never from a downstream, and cycles are a build error. Then enforce it
in lint, where it will actually hold. Two structural fixes make the DAG acyclic;
see §4.3.

**ADR-0006 (Google Drive before S3) should be revisited for payroll artifacts.**
Drive was chosen for résumés, where shareable links are a feature.
Payslips, ECR files and challan evidence are statutory records under a
retention obligation, and `DriveBlobStore` is still a stub
(`raise NotImplementedError`). Recommend S3-compatible object storage for
payroll/compliance artifacts, keeping Drive for ATS. The `BlobStore` interface
already makes this a per-kind backend choice rather than a rewrite. **This is a
decision to be taken, not one I should take unilaterally** — flagged in §26.

### 2.3 Platform contracts designed but not built

These exist in `ARCHITECTURE-2-PLATFORM.md` as accepted design and are **MISSING
from the code**. Payroll needs three of them, so the plan builds them as
platform work rather than payroll work:

| Contract | Platform § | State |
|---|---|---|
| `failed_tasks` dead-letter table | §8 | MISSING — no migration |
| `feature_flags` table | §5 | MISSING — `core/flags.py` exists, table does not |
| Audit `before`/`after`, `actor_ip`, `user_agent` | §10 | Partially MISSING — row has `correlation_id`/`source` but no diff, no IP, no UA |
| `Search.query()` | §6 | MISSING — `raise NotImplementedError` |
| `DriveBlobStore` | §3 | MISSING — stub; `LocalBlobStore` works |
| `Notifier` | §4 | `core/notify/` exists; `modules/notifications/` is empty |

---

## 3. Corrections to the brief

Per the brief's §53, which asks me not to agree blindly.

**Three items in the brief's "preserve this good work" list do not exist.**
Search is a `NotImplementedError` stub; there is no export/download code of any
kind (no CSV, XLSX, or PDF writer — PyMuPDF only *reads* résumés in ATS); there
is no artifact model. "Calculation traces" is half-real: `payslip.breakdown`
carries `basis` and `rule_versions`, but no UI exposes it. These are greenfield
subsystems, not hardening tasks, and the plan sizes them accordingly.

**The FINANCE domain does not exist** — no expense, reimbursement, loan,
advance, recovery or bonus table anywhere. So "do not duplicate finance master
data in payroll" is moot. **Recommendation against the brief:** do not build a
Finance module to satisfy an architectural rule. Reimbursements and one-off
bonuses should enter as **payroll inputs with a typed source** — the ledger
already has a `source` enum; add `expense`, `loan`, `advance`, `arrear` plus a
nullable `source_ref`. Build Finance when a *second consumer* appears. **Loans
are the exception**: a loan carries a balance across periods, and a balance
cannot live in a per-period ledger without being recomputed wrongly. Loans get a
table before the first deduction.

**Bank details, PAN and designation do not exist.** No `bank_account`, `ifsc`,
or `pan` column anywhere; `Employee` has `uan` but not `pan`. This blocks the
brief's §7 readiness check ("2 missing bank details") and all of §29 (payments).
Schema change first.

**`OVERDUE` must not be a stored obligation status** (brief §21). It is derived
from `(due_date, status, now)`. Storing it guarantees rows that are wrong until
a cron touches them. Derive it in the query.

**The brief's §39 run state machine mixes two lifecycles.** `QUEUED`,
`CALCULATING`, `CALCULATED` are *job* states; `DRAFT`, `APPROVED`, `FINALIZED`
are *business* states. One enum means a crashed worker corrupts the business
state. Use two columns — `status` and `job_state` (§9.1).

**"Test at 50,000" (§41) is the wrong target.** ADR-0001 scopes the product at
20–500 employees; the brief wants SME-first but scalable. Both can be true, but
50,000 in one tenant needs a different persistence strategy (partitioning,
`COPY`-based load) and is not on the path. Plan and test to **10,000**, publish
the measured ceiling, treat beyond that as a separate exercise.

**The brief's §38 navigation adds three top-level items** to a product whose own
design system states "every additional top-level item is a tax on the product's
clarity." FINANCE has no content; REPORTS is a container, not a destination.
Recommend **PAYROLL + COMPLIANCE only**, both permission-gated, reports living
next to the data they describe.

---

## 4. The five defects, in detail

### 4.1 Payroll does not read attendance — CRITICAL

- `AttendanceEvent` is imported by **zero** payroll modules.
- LOP derives *only* from approved leave on unpaid leave types
  (`service.unpaid_leave_days`).
- `WorkFact` rows are created *only* by `POST /workforce/facts`. Nothing derives
  them from punches.

**An employee who never punches in for a month and files no leave is paid in
full.** The attendance module is live — `/me/attendance/punch` works — so this
is not a dormant feature, it is a module producing data payroll silently
ignores.

This is also the system's real duplication, and not the one the brief warns
about: two independent answers to "did this person work today" —
`attendance_events` and `work_facts` — with no bridge and no reconciliation.

### 4.2 Compensation overwrites history — CRITICAL

`SalaryComponent`'s own docstring concedes it: *"not effective-dated. A raise
overwrites the amount, and history survives because finalized payslips froze
their own breakdown."*

Frozen payslips are an audit record, not a salary model. Without versions:
arrears cannot be computed, retro corrections cannot be expressed, F&F cannot
resolve a leaver's last effective salary, and movement analysis cannot
distinguish "raise" from "data fix." This blocks brief §10 and most of Phase 4.

### 4.3 Module cycle and misplaced ownership — HIGH

Measured cross-module imports:

```
assistant  → ats hr_core leave        ess       → attendance hr_core leave payroll
attendance → audit hr_core work_calendar        hr_core   → audit auth payroll
ats        → audit auth               leave     → audit work_calendar
payroll    → audit hr_core leave work_calendar  work_calendar → audit
```

Two structural problems inside that:

**(a) A genuine cycle.** `hr_core/schemas.py:15` imports `WORKER_TYPES` from
`payroll.workforce`, while seven payroll files import `hr_core.models.Employee`.
`worker_type` is a **column on `employees`** — an hr_core table. The constant is
simply in the wrong module. Moving it to `hr_core` is a one-line fix that breaks
the cycle.

**(b) Payroll owns two tables that are not payroll concepts.** `establishments`
(a registered place of business) and `contractors` (a vendor) live in
`payroll/workforce.py`, and `employees` carries FKs into both. So the *schema*
says hr_core depends on payroll. An establishment is an organisation concept
that payroll *consumes* for jurisdiction; a contractor is a vendor. Both belong
in `hr_core` (or a small `org` module), with payroll reading them. This aligns
the FK direction with the dependency direction and matches the brief's §6
ownership thinking.

**(c) `audit` is cross-cutting**, imported by 6 of 9 modules. Under the ADR's
own rule, modules may import `core/`. Moving `modules/audit` → `core/audit`
reclassifies ~a third of the violations as legal by construction.

### 4.4 Synchronous runs and N+1 — HIGH

Measured, on throwaway tenants against local Postgres:

| Employees | Wall time | Queries | Per employee |
|---:|---:|---:|---:|
| 25 | 0.41s | 361 | 14.4 q · 16.4 ms |
| 100 | 1.18s | 1,411 | 14.1 q · 11.8 ms |
| 300 | 3.35s | 4,211 | 14.0 q · 11.2 ms |

Cleanly linear; every hot column already indexed (verified in `pg_indexes`:
`payroll_inputs(employee_id, period)`, `work_facts(employee_id, day)`,
`salary_components(employee_id, component_id)`, all partial on live rows). **The
cost is round-trip count, not scan time** — so adding indexes will not help;
batching will. Over a network, latency dominates: 1,000 employees ≈ 14,000 round
trips ≈ +14s at 1ms RTT.

**Honest ceiling today: ~1,000 employees.** That is above ADR-0001's stated
20–500 target and below the brief's ambition, which is precisely the tension to
resolve in Phase 1.

### 4.5 No exports, no search, no artifacts — HIGH

Covered in §3. Consequence for payroll specifically: **no payslip has ever been
downloaded by anyone**, and compliance evidence has nowhere to live.

### 4.6 Lower-severity findings

| Finding | Severity |
|---|---|
| One global `LABOUR_CODE_START` date for all statutes (§16.1) | High |
| ESI coverage wage not separated from contribution wage | Medium |
| Audit has no before/after and no query route (Platform §10 lag) | Medium |
| Run states are only `draft \| finalized`; no approval step | Medium |
| No pagination on any list endpoint | Medium |
| No `updated_by` on money-bearing rows (only `finalized_by`) | Medium |
| PT cannot distinguish NOT_APPLICABLE from NOT_CONFIGURED | Medium |
| Frontend renders all payslips unvirtualized | Low now, High at 2,000 |
| No contractor delete route (dev cleanup needed raw SQL) | Low |

---

## 5. Functionality inventory

### 5.1 Present and correct — do not rebuild

Decimal money end-to-end · tenant RLS with FORCE + non-superuser role ·
immutable finalized runs · adjustments into later periods · work facts
(hours/site/shift/night/premium + approval) · payroll input ledger with `source`
provenance · establishment config · rule versioning resolved **by period** ·
readiness (9 checks) · validation (6 codes, blocking/warning/info) · risk ·
movement decomposition · payslip provenance (`basis` + `rule_versions`) ·
blue-collar rendering with no `worker_type` branch · contractor reconciliation ·
manager holds `workfact.approve` but **not** `payroll.read` · 21 invariant
tests · ESS finalized-only payslips (filter in service, not handler) · Lamoon
monochrome design system · AI grounding contract with a test asserting the model
cannot invent clickable results.

### 5.2 Readiness checks that exist

`employees`, `pay_components`, `salary_coverage`, `work_calendar`,
`provident_fund`, `esi`, `professional_tax`, `jurisdiction`,
`statutory_identity`.

**MISSING:** bank details (column absent), attendance completeness, work-fact
approval (today only a validation warning), compensation effective-date
conflicts.

### 5.3 MISSING entirely

Compliance (all) · payments and bank files · every export/download · artifacts ·
search · audit query · effective-dated compensation · arrears · F&F · gratuity ·
statutory bonus · LWF · TDS computation (deliberate, §16.7) · ECR/24Q/ESI
returns · challans · compliance calendar · `failed_tasks` · `feature_flags` ·
notifications implementation · `finance`/`compliance` roles · PAN · bank details
· designation.

---

## 6. KEEP / CHANGE / REMOVE / ADD / DEFER

### KEEP — resist "improving" these

- **EPF employer split by subtraction** (`employer_epf = employer_total − EPS`).
  Brief §15 warns against rounding both halves independently; the code already
  avoids it. Any refactor must preserve this exactly.
- **PT with no fallback chain.** No slabs ⇒ no PT, never "borrow the company's."
- **TDS as an explicit labelled input** (brief §19 agrees).
- **Invoice recorded always, approved only at zero variance.**
- **`invoiced = None ≠ 0`.**
- **Work facts store hours, never amounts.**
- **Manager has `workfact.approve`, not `payroll.read`.**
- **Rules resolved by period, never `today()`.**

### CHANGE

Effective-dated compensation · attendance→work-fact bridge · async runs with
split `status`/`job_state` · bulk `PayrollContext` · per-statute wage basis ·
ESI coverage vs contribution wage · audit before/after + query API · pagination
+ virtualization · readiness additions · module DAG fixes (§4.3).

### REMOVE

- Global `LABOUR_CODE_START` constant — becomes a rule row, not deleted logic.
- `PayComponent.pf_wage`, already superseded by `wage_basis` and kept for
  back-compat. Drop after one release, with a migration asserting the back-fill
  is complete.

### ADD

Compliance obligation engine · compliance calendar · artifacts + async
generation · payslip PDF · payroll register · bank details + PAN · payment
lifecycle · bank file export · search · audit UI · `finance`/`compliance` roles
· loans · arrears · run approval step · contractor delete · `failed_tasks` ·
`feature_flags`.

### DEFER — with the trigger that un-defers each

| Deferred | Un-defer when |
|---|---|
| Full TDS subsystem | A customer's CA rejects manual entry |
| Gratuity, F&F | First exit at 5+ years tenure |
| Statutory bonus | First qualifying employee at year end |
| Minimum-wage *engine* (beyond today's warning) | First multi-state blue-collar customer |
| Direct EPFO/ESIC/bank API integration | A published, stable API and a customer asking |
| Meilisearch/ES | Postgres FTS relevance measurably insufficient |
| 50,000-employee tenants | A signed customer at that size |
| Kafka, microservices, sharding | Never on current evidence; revisit only with measurements |

---

## 7. Domain model

Nine aggregates. Payroll owns four and *reads* the rest.

```
PERSON          employee, employment, statutory identity, bank details  [hr_core]
ORGANISATION    establishment, contractor, department          [hr_core — moved,
                                                                  see §4.3(b)]
COMPENSATION    salary version (effective-dated), lines     [compensation — new]
WORKFORCE FACT  work fact, attendance event, leave, holiday, shift
                                              [attendance/leave/payroll]
PAYROLL INPUT   one asserted figure, one person, one period, with source
                and approval                                      [payroll]
PAYROLL RUN     period, status, job_state, totals                  [payroll]
PAYSLIP         frozen snapshot incl. basis + rule versions        [payroll]
ADJUSTMENT      a correction posted into a later period            [payroll]
STATUTORY RULE  effective-dated, per statute, per jurisdiction     [rules]
OBLIGATION      what a finalized run made us liable for, and its
                filing/payment/evidence lifecycle          [compliance — new]
```

**The governing invariant:** a payslip is a pure function of (payroll inputs,
statutory rules, calendar) as they stood at finalization. Everything else is
provenance. If re-running a finalized period from its stored inputs and rule
versions does not reproduce it exactly, that is a bug — and §24 makes it a test.

---

## 8. Data ownership map

One writer per fact. Payroll reads; it does not master.

| Fact | Master | Payroll |
|---|---|---|
| Identity, department, manager | `hr_core` | read |
| Statutory identity (DOB, UAN, PF first joined, IW) | `hr_core` | read |
| Bank details, PAN | `hr_core` (new) | read |
| Establishment, contractor | `hr_core` (moved) | read |
| Salary | `compensation` (new) | read, resolved by period |
| Punches | `attendance` | read via bridge (§17) |
| Leave | `leave` | read (unpaid ⇒ LOP) |
| Working days, holidays | `work_calendar` | read |
| Work facts | `payroll` | **own** |
| Payroll inputs, runs, payslips, adjustments | `payroll` | **own** |
| Statutory rules | `rules` | read |
| Obligations, returns, challans, evidence | `compliance` (new) | reads finalized runs only |
| Artifacts | `core/artifacts` (new) | write via job |

**Rule: compliance never reads a draft run.** Its only input is a finalized one.
This keeps the obligation ledger from tracking numbers that are still moving.

---

## 9. State machines

### 9.1 Payroll run — two columns

```
status     DRAFT → CALCULATED → PENDING_APPROVAL → APPROVED → FINALIZED
job_state  IDLE | QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
```

| From | To | Guard |
|---|---|---|
| DRAFT | DRAFT | recalculation — always allowed |
| DRAFT | CALCULATED | job SUCCEEDED and no blocking findings |
| CALCULATED | DRAFT | any input changed ⇒ invalidate |
| CALCULATED | PENDING_APPROVAL | HR submits |
| PENDING_APPROVAL | APPROVED | `payroll.approve`; actor ≠ preparer when segregation is on |
| PENDING_APPROVAL | DRAFT | rejected, reason required |
| APPROVED | FINALIZED | `payroll.finalize` |
| FINALIZED | — | terminal. No transition out |

A failed worker sets `job_state = FAILED` and leaves `status` untouched — the
run stays recoverable (brief §45).

### 9.2 Obligation

The brief's §21 list minus `OVERDUE` (derived). Terminal: `RECONCILED`,
`NOT_APPLICABLE`.

### 9.3 Payment

```
DRAFT → VALIDATED → FILE_GENERATED → SUBMITTED → PROCESSING
      → PAID | PARTIALLY_PAID | FAILED → RECONCILED
```

**A payment failure never mutates payroll.** The payslip is finalized and
correct; only the `payment_item` failed.

---

## 10. Database model

### 10.1 New tables

```sql
compensation_versions(
  id, company_id, employee_id,
  effective_from date NOT NULL,   -- resolution is by payroll period
  effective_to   date,            -- NULL = open; maintained on insert
  reason  varchar(30),            -- hire|revision|promotion|correction|f_and_f
  approved_by, approved_at,
  supersedes_id uuid,             -- retro corrections chain; never overwrite
  created_by, created_at, deleted_at)
compensation_lines(id, company_id, version_id, component_id, amount NUMERIC(12,2))

employee_bank_accounts(id, company_id, employee_id, account_number_enc, ifsc,
  account_holder_name, is_primary, verified_at, verified_by, deleted_at)

loans(id, company_id, employee_id, principal, outstanding, emi, start_period,
  status, created_by)                       -- balance-bearing ⇒ a real table
loan_instalments(id, company_id, loan_id, period, amount, payslip_id)

compliance_obligations(
  id, company_id, establishment_id, statute, obligation_type, period,
  obligor varchar(12) DEFAULT 'self',       -- self|contractor (§19)
  amount NUMERIC(14,2), due_date, status, rule_version, source_run_id,
  return_ref, challan_ref, payment_ref,
  submitted_at, paid_at, reconciled_at,
  created_by, updated_by, created_at, updated_at, deleted_at)
  -- NO `overdue` column. Derived: status NOT IN (paid, reconciled,
  --   not_applicable) AND due_date < current_date
compliance_evidence(id, company_id, obligation_id, artifact_id, kind, note)

artifacts(id, company_id, kind, period, scope_json, status, storage_key,
  content_type, size_bytes, checksum_sha256, generated_by, generated_at,
  expires_at, error, deleted_at)

payment_batches(id, company_id, run_id, status, total, file_artifact_id, ...)
payment_items(id, company_id, batch_id, employee_id, amount, bank_account_id,
  status, failure_reason, reference)

financial_audit(id, company_id, entity, entity_id, action, actor_user_id,
  before JSONB, after JSONB, correlation_id, actor_ip, user_agent, created_at)

failed_tasks(...)      -- Platform §8, currently MISSING
feature_flags(company_id, key, enabled)   -- Platform §5, currently MISSING
```

### 10.2 Column additions

`employees`: `pan`, `designation`, `primary_bank_account_id`.
`payroll_runs`: `job_state`, `job_id`, `progress`, `approved_by`, `approved_at`,
`calculation_error`.
`payroll_inputs.source`: `+ expense | loan | advance | arrear`, plus nullable
`source_ref uuid` and `source_note`.
`audit_events`: `actor_ip`, `user_agent` (Platform §10 lag).

### 10.3 Indexes

Do **not** add indexes hoping to fix the N+1 — §4.4 shows the cost is
round-trips. New composites required by new access patterns only:

```
compensation_versions(employee_id, effective_from DESC) WHERE deleted_at IS NULL
compliance_obligations(company_id, status, due_date)
compliance_obligations(establishment_id, statute, period)
artifacts(company_id, kind, period)
payment_items(batch_id, status)
attendance_events(employee_id, occurred_on)     -- for the bridge
```

---

## 11. API architecture

### 11.1 Changed

```
POST /payroll/runs                 → 202 {run_id, job_id}   (breaking; §25.3)
GET  /payroll/runs/{id}            → + job_state, progress, error
GET  /payroll/runs/{id}/payslips   → NEW, paginated (today embedded, unbounded)
POST /payroll/runs/{id}/submit     → NEW  CALCULATED → PENDING_APPROVAL
POST /payroll/runs/{id}/approve    → NEW  requires payroll.approve
DELETE /payroll/contractors/{id}   → NEW  soft delete, guarded
```

### 11.2 New

```
/compensation/employees/{id}/versions           GET POST
/compensation/employees/{id}/versions/{vid}     PATCH DELETE
/compensation/resolve?employee_id=&on=          GET   -- what applied on a date

/compliance/obligations                         GET (statute, establishment,
                                                     status, period, due_before)
/compliance/obligations/{id}                    GET PATCH
/compliance/obligations/{id}/{generate|submit|challan|pay|acknowledge|reconcile}
/compliance/calendar?from=&to=                  GET
/compliance/reconciliation?period=&statute=     GET

/payments/batches                               GET POST
/payments/batches/{id}/{validate|file|submit|reconcile}
/payments/items/{id}/retry

/artifacts        GET POST   /artifacts/{id}   GET   /artifacts/{id}/download GET
/search?q=&kind=&limit=                         GET
/audit?actor=&entity=&period=&action=           GET  (audit.read)
```

### 11.3 Conventions

Every list takes `limit` (default 50, max 200) + cursor, returns
`{items, next_cursor}`. Long operations return `202` with a job handle, no
exceptions. **Money stays a string in JSON.** Idempotency: run creation is
already idempotent per (company, period) via a unique constraint; payment
submission and artifact generation take an `Idempotency-Key` header, per
Platform §8.

---

## 12. Background job architecture

Adopts the Platform §8 contract rather than inventing one: three queues, 3×
exponential backoff, per-task idempotency keys, `failed_tasks` dead-letter.

```
POST /payroll/runs
  → upsert run (unique company+period)
  → job_state=QUEUED, job_id=uuid
  → calculate_payroll.apply_async(queue="high")
  → 202 {run_id, job_id}

worker:
  advisory lock (company_id, run_id)          -- no double calculation
  if run.job_id != job_id: exit (superseded)
  job_state=RUNNING
  ctx = PayrollContext.load(...)              -- §23, bulk
  compute in memory (pure)
  bulk upsert payslips
  job_state=SUCCEEDED, status=CALCULATED
  on exception: job_state=FAILED, calculation_error set, status untouched,
                row written to failed_tasks
```

**Progress:** integer 0–100 on the run row, updated every N employees. UI polls
`GET /payroll/runs/{id}` every 2s while RUNNING. WebSockets are not worth the
infrastructure for a job measured in seconds.

**Cancellation:** permitted in QUEUED and RUNNING only before the bulk write
begins. After that it runs to completion — a half-written run is worse than a
slow one.

---

## 13. Search and index strategy

Per ADR-0007, Postgres FTS + `pg_trgm` behind the existing `Search` interface.
No new infrastructure.

Two mechanisms, because they answer different questions — conflating them is the
usual mistake:

1. **Entity lookup (fuzzy):** employee by name/code/UAN/PAN, contractor, challan
   ref, return ref, payment ref. GIN trigram indexes. This is what `⌘K` needs.
2. **Record search (filtered):** payslips, obligations, adjustments, artifacts,
   audit events — by period, establishment, status, amount range. These are
   filtered queries, not full-text; plain composite indexes. Only the employee
   name inside a payslip search wants trigram matching.

Search runs as the tenant role, so results are tenant-scoped by construction.
No cross-tenant index, ever.

---

## 14. Artifacts and downloads

```
POST /artifacts {kind, period, scope}
  → row status=QUEUED, deterministic key
  → generate_artifact.apply_async(queue="normal")
  → 202 {artifact_id}
worker: GENERATING → render → blob.put() → checksum → READY | FAILED
```

Lifecycle `QUEUED → GENERATING → READY | FAILED`, plus `EXPIRED` by sweep. Key:
`{company_id}/{kind}/{period:%Y-%m}/{scope_hash}-{version}.{ext}`.

Every artifact stores kind, period, scope, `generated_by`, `generated_at`,
`checksum_sha256`, `storage_key`, `size_bytes`, `expires_at`. **A generated file
must be findable months later** — that is what makes it evidence rather than a
download.

**Sync exception:** a single payslip PDF may render inline (~50ms). Everything
bulk is async; 10,000 payslips is never an HTTP request.

**Authorization is re-checked at download**, not only at request — an artifact id
is not a capability.

**Formats:** CSV for registers (streams, no dependency), XLSX where HR will
pivot (`openpyxl` write-only), PDF for payslips. **Storage backend is an open
decision** — see §2.2 and §26.

---

## 15. Security, RLS and permissions

### 15.1 Unchanged

Shared schema, `FORCE ROW LEVEL SECURITY`, non-superuser `app` role,
`SET LOCAL app.company_id` per request. `SET LOCAL` is lost on commit and the
next query then casts `''` to uuid and errors loudly rather than leaking — a
good failure mode, keep it.

### 15.2 New tables need policies — enforced by test

Add a test enumerating `pg_tables` that asserts every tenant table has
`rowsecurity = true` and a FORCE policy. Cheap, and it closes the hole
permanently.

### 15.3 RLS defeats data migrations — a burned finger

Migrations run as the non-superuser under FORCE RLS, so a data `UPDATE` matches
**zero rows and reports success**. This already happened here once, caught only
because a later `SET NOT NULL` failed. Every data migration must suspend RLS on
*every* table it touches — including the read side of a join — and assert
affected row counts. This governs the compensation back-fill (§25.2), the
riskiest migration in the plan.

### 15.4 Sensitive fields

Bank account numbers and PAN encrypted at rest, masked in lists (`••••3421`),
full values behind `pii.read_bank` with a `financial_audit` entry written **on
read**.

### 15.5 Permissions and roles

New permissions:
```
payroll.approve  payroll.finalize
compensation.read|write|approve
compliance.read|write|submit|pay
payment.read|write|submit
artifact.read|generate      audit.read  audit.read_financial   pii.read_bank
```

New roles — dict entries per ADR-0008, not tables:

| Role | Holds | Deliberately excludes |
|---|---|---|
| `payroll_admin` | payroll.*, compensation.*, workfact.*, artifact.* | compliance.submit, payment.submit |
| `compliance_officer` | compliance.*, artifact.read, payroll.read | compensation.*, payment.pay |
| `finance` | payment.*, artifact.read, payroll.read (totals) | compensation.read, PII beyond bank |

**Finance is the one that matters.** It must pay people without acquiring
everyone's compensation history: it sees name, bank account, net amount — not
the salary structure, movement report, or adjustment reasons.

**Segregation of duties:** preparer ≠ approver ≠ payment submitter, as a
per-company toggle (off for a 3-person SME where it deadlocks; on by default
above ~50 employees). Enforced in the service, tested.

---

## 16. Indian statutory architecture

### 16.1 Basis resolves per statute — the core change

Today `statutory_wage()` produces one number every scheme uses, gated by one
global `LABOUR_CODE_START = date(2025, 11, 21)`.

**Verified against sources:** the four labour codes took effect 21 November
2025, but **only certain provisions of the Code on Wages and the Social Security
Code were brought into force**, with Central Rules expected subsequently and
state rules following separately; the Ministry issued clarifying FAQs in January
and March 2026. A single global date is therefore wrong in principle, not merely
imprecise — different statutes may adopt the new basis on different dates, and
jurisdictions will vary.

Replace with:

```python
def basis_for(statute: str, *, period: date, jurisdiction: str,
              lines: Sequence[Line]) -> WageBasis
```

resolving `(statute, jurisdiction, period)` → an effective-dated
`WageDefinition`. EPF, ESI, gratuity, bonus and minimum wage each ask
separately. This is the brief's §14, and §0's finding makes it mandatory rather
than tidy.

### 16.2 Rules become data

Move from Python constants to effective-dated rows with a Python fallback for
the current version, so a rate change is a migration not a deploy. Each row:
`statute, jurisdiction, effective_from, effective_to, version, params JSONB,
source_note` — where `source_note` **cites the notification**. Payslips already
stamp the resolved version; keep that.

### 16.3 EPF — keep the maths, extend the inputs

12%/12%; EPS 8.33% capped on ₹15,000; **employer EPF by subtraction**; EDLI
0.5%; admin 0.5% with the ₹500/month per-establishment floor; EPS ineligible at
age ≥58 or first membership on/after 1 Sep 2014 above the ceiling. **Add:**
reduced-rate (10%) establishments, international workers outside the ceiling
(the `is_international_worker` flag exists; no rule consumes it), voluntary PF,
and PF on full wage where opted in (`on_full_wage` is already a parameter).

### 16.4 ESI — separate the two wages

0.75%/3.25%, `ROUND_CEILING` per reg. 40, Apr–Sep / Oct–Mar periods with
mid-period crossing not stopping contribution — all implemented and tested.
**Add:** coverage wage ≠ contribution wage. Coverage decides *whether* someone
is covered; contribution decides *on what*. OT is excluded from the coverage
test but included in the contribution wage — conflating them mis-covers anyone
near the limit whose overtime pushes them over.

### 16.5 Professional tax

Keep the strictness (no fallback). **Add** an explicit `NOT_APPLICABLE` (the
state does not levy) distinct from `NOT_CONFIGURED` (it does, and we have not
set it up). Today both render as "no PT," hiding a real misconfiguration.

### 16.6 LWF, minimum wage, bonus, gratuity

All jurisdiction-specific, effective-dated, deferred. The §16.2 rule table is
designed so each is data plus a calculator function — no schema change.

### 16.7 TDS — unchanged

Stays an explicit input from an authorized user, with the UI saying so. Matches
brief §19. When built, it is the complete subsystem or nothing.

### 16.8 Compliance claims

The product must never assert a filing is correct. It asserts **what it
calculated, under which rule version, from which inputs**. Given partial
notification and pending state rules, anything stronger would be false.

---

## 17. White-collar workflow

Monthly salary from the compensation version effective in the period. Proration
by **working days** (already documented and defensible — leave is billed the same
way, so the two never disagree). LOP from unpaid leave **and, newly, unexcused
absence**.

**The attendance bridge.** A nightly job, plus an on-demand PREPARE trigger,
converts attendance into work facts:

```
for each working day in the period:
  punches exist        → fact(status=worked)
  approved leave       → fact(status=leave, paid|unpaid)
  holiday / weekly off → no fact (the calendar handles it)
  nothing at all       → fact(status=absent, approved_at=NULL)
```

The `absent` fact is **unapproved by design**. It surfaces in validation as
"3 unexplained absences — regularise or confirm as LOP," and does not silently
cut anyone's pay. That closes §4.1 without making a missing punch instantly cost
someone money, which would be its own kind of wrong.

---

## 18. Blue-collar workflow

Already correct; do not rebuild. Days and hours from work facts, converted by a
*versioned rule*, never stored as an amount. The payslip work summary renders
**only when work facts exist**, which self-selects blue-collar with no
`worker_type` branch anywhere in the UI — exactly what brief §12 asks for, and
already how the code works. `worker_type` drives explanation, not calculation.
One engine.

Later: piece-rate, attendance-linked incentives, multi-rate shifts.

---

## 19. Contractor workflow

Implemented: contractor master, deployment, per-worker-day reconciliation,
record-always/approve-at-zero-variance, `invoiced = None ≠ 0`.

**Add:** authorized override with mandatory reason (`compliance.write`-gated,
written to financial audit) — brief §13 is right that forcing attendance data to
be falsified to clear a legitimate variance is worse than an audited override ·
per-line variance reasons · evidence artifacts (the contractor's own bill) ·
soft-delete route guarded against live workers or approved invoices.

**Principal-employer liability** under CLRA (has the contractor filed their own
PF/ESI for these workers?) is deferred — but the obligation model carries
`obligor` (`self | contractor`) **now**, so it is not a schema migration later.

---

## 20. Payment workflow

Wholly new. `FINALIZED ≠ PAID`; today the model conflates them by having no
payment concept at all.

```
run FINALIZED
  → batch created (net per employee)
  → bank validation: account present, IFSC well-formed, name match,
    amount > 0, no duplicate (employee, period)
  → file generated as an artifact (NEFT/RTGS CSV, bank-specific template)
  → HR downloads, uploads to the bank portal, records the reference
  → PAID | PARTIALLY_PAID | FAILED
  → per-item failure retries into a new batch; payroll untouched
  → reconcile: sum(paid items) vs run.net_total
```

Direct bank API integration deferred. File + recorded reference is what Indian
SMEs actually do, and it is honest.

---

## 21. Reconciliation

Three reconciliations, one shape: `expected`, `actual`, `variance`, `status`,
`reason`, `evidence`, `owner`, `resolved_at`.

| | Expected | Actual |
|---|---|---|
| Payment | run net total | sum of paid items |
| Compliance | calculated liability | challan / paid amount |
| Contractor | work facts × rate | invoice amount |

Contractor reconciliation is built and is the reference implementation. Reuse its
shape; do not invent a second one.

---

## 22. Audit model

`audit_events` has good bones — jsonb payload, actor, correlation id, source.
Platform §10 already specifies before/after, `actor_ip` and `user_agent`; the
code has none of them. This is **implementation lag against accepted design**,
not a new proposal.

- `financial_audit` (§10.1) with before/after for compensation, payslip
  corrections, adjustments, obligations, payments. Separate table because it
  needs `audit.read_financial` — salary history is exactly what must not leak
  through a general audit view.
- `GET /audit` with filters: actor, entity, entity_id, action, period,
  correlation id.
- Keep `correlation_id`: one run touching 1,200 employees is one traceable
  operation.

---

## 23. Scalability model

Baseline in §4.4. Target:

```
bulk load → PayrollContext (memory) → pure functions → bulk persist
```

`PayrollContext` loads once per run, bounded regardless of headcount:

```
settings, establishments, pt_slabs, pay_components, rule versions   ~6
compensation versions effective in period (all employees)            1
compensation lines for those versions                                1
work facts for period                                                1
approved unpaid leave overlapping period                             1
existing payslips                                                    1
existing ledger inputs                                               1
calendar + holidays                                                  2
                                                              ≈ 15 total
```

Compute in memory — `statutory.py` is already pure and takes no session, which
is why this refactor is tractable — then bulk upsert. **Target ~15 queries per
run**, versus 14 × N. At 1,000 employees: 15 instead of 14,000.

Test at 100 / 1,000 / 5,000 / 10,000; record wall time, query count, peak RSS,
worker throughput; publish the ceiling. Not 50,000 (§3).

**Explicitly not doing:** Kafka, microservices, sharding, read replicas,
separate payroll service. ADR-0001 and ADR-0004 already settled this and no
measurement contradicts them.

---

## 24. Testing strategy

373 tests today, ~233 payroll. The valuable ones are the 21 invariants — three
real shipped bugs were caught by invariants or by new features rather than by
review (mid-month joiner LOP accumulation, a leaver who kept being paid, PT
slabs read across jurisdictions).

**Invariants to add:**
```
resolve(compensation, period) is deterministic and total
re-running a FINALIZED period from stored inputs + rule versions reproduces it
an absent day yields a finding, never a silent deduction
obligation amount == sum of payslip statutory lines for that establishment
sum(payment items) == run.net_total
a failed payment leaves the payslip untouched
artifact checksum is stable for identical input
retrying a job does not double-write; a superseded job exits clean
no import cycle exists between modules        (lint-level, §4.3)
every tenant table has FORCE RLS              (enumerated from pg_tables)
```

**Scenarios:** mid-month joiner · mid-month exit · rehire · retro raise two
months back · mid-month revision · LOP spanning a month boundary · unapproved OT
· absent with no leave · contractor variance with override · multi-state PT ·
ESI mid-period ceiling crossing · EPS ineligible by age and by the 2014 rule.

**Security:** cross-tenant read on every new table · manager cannot reach
payroll · finance cannot read compensation · employee cannot see a draft payslip
· artifact download re-checks authorization after a role change.

**Failure:** worker killed mid-calculation → recoverable · artifact fails →
FAILED and retryable · bank file rejected → payroll unchanged · compliance
submission fails → SUBMISSION_FAILED, never silently complete.

Count is not the metric. Invariants and realistic scenarios are.

---

## 25. Migration plan

**25.1 Ordering constraint.** Compensation versioning lands **before** async
calculation: the bulk context resolves salary by period, and there is no point
building it against a model about to change.

**25.2 Compensation back-fill — the risky one.**
1. Create the two tables.
2. Back-fill one open-ended version per employee from `salary_components`,
   `effective_from = min(joined_on, earliest finalized payslip period)`,
   `reason='migration'`.
3. Dual-read behind a flag: resolve from versions, assert equality with the old
   path, log mismatches. **Run for one full payroll cycle.**
4. Cut over; keep `salary_components` one release.
5. Drop, with a migration asserting zero rows changed since cutover.

Every step suspends RLS on both tables and asserts row counts (§15.3).
**Finalized payslips are never rewritten** — they already froze their breakdown;
the back-fill reconstructs forward-looking salary only.

**25.3 Async cutover.** `POST /payroll/runs` changing from 200-with-payslips to
202-with-job is breaking. Add `?async=true` returning 202, move the frontend,
flip the default, keep `?async=false` one release capped at 200 employees.

**25.4 Module moves (§4.3).** `WORKER_TYPES` → `hr_core` is a pure code move.
`establishments`/`contractors` → `hr_core` is a code move plus SQLAlchemy
metadata change with **no table rename and no data migration** — the FKs already
point the right way; only the owning Python package changes.

---

## 26. Rollout strategy

1. Feature-flag per company — which requires building the `feature_flags` table
   first (Platform §5, currently MISSING).
2. Dogfood on `ccdemo` for a full cycle before any customer.
3. Compensation: dual-read one cycle (§25.2).
4. Async: shadow-run the bulk path against the old one on the same period,
   assert identical payslips, then switch.
5. Compliance: obligations **read-only** for one cycle — generate and display,
   let HR compare against what they filed manually, then enable the workflow.
6. Payments: bank file generation before any status tracking; HR uses the file
   for a cycle first.

Every phase is reversible except the `salary_components` drop, deliberately
last.

**One decision needed before Phase 1.6:** storage backend for payroll artifacts
(§2.2). ADR-0006 chose Drive; `DriveBlobStore` is unimplemented; payslips and
challan evidence are statutory records with retention obligations. My
recommendation is S3-compatible for payroll/compliance and Drive for ATS, but
amending an accepted ADR is a call for you, not me.

---

## 27. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Statutory rules change mid-build | High | Effective-dated rows; never rewrite history |
| Partial Labour Code notification misread | High | Per-statute basis (§16.1); cite notifications in `source_note`; claim no certainty |
| Compensation back-fill wrong | High | Dual-read a full cycle; assert equality; finalized payslips untouched |
| RLS silently voiding a data migration | High | Suspend RLS on every touched table; assert row counts; already burned once |
| Async cutover changes a number | High | Shadow-run and diff before switching |
| Module moves break imports at runtime | Medium | Pure moves, no data migration; full suite gates each |
| Bank file format varies by bank | Medium | Template per bank; start with one |
| Scope creep into a compliance SaaS | Medium | §6 DEFER list with explicit un-defer triggers |
| PII exposure via new surfaces | Medium | Encrypt at rest, mask in lists, `pii.read_bank`, audit on read |
| Over-building Finance | Medium | §3 — typed ledger inputs; only loans get a table |

**On compliance liability:** applicability depends on state, establishment,
headcount, wage level and current notifications. The product presents *what it
calculated and under which rule version* — never *this filing is correct*.
Getting this wrong is legal risk, not UX risk.

---

## 28. Explicitly deferred

Full TDS · gratuity · F&F · statutory bonus · LWF · minimum-wage engine ·
direct EPFO/ESIC/PT portal integration · direct bank API payments · contractor
principal-employer PF/ESI verification · piece-rate · Meilisearch/ES ·
50,000-employee tenants · Kafka/microservices/sharding · multi-currency ·
non-Indian statutory.

Triggers in §6. **Deferred means not built and clearly labelled as such in the
UI** — never a half-built screen implying it works.

---

# IMPLEMENTATION PLAN

Ordered by dependency. Every task carries the nine fields the brief requires.
**Nothing here has been started.**

---

## PHASE 0.5 — Structural corrections (2–3 days, unblocks clean work)

### 0.5.1 Break the hr_core ↔ payroll cycle
**Why** `hr_core/schemas.py` imports `WORKER_TYPES` from `payroll.workforce`
while 7 payroll files import `hr_core.models.Employee`. `worker_type` is a
column on `employees`, so the constant is in the wrong module. A cycle makes
every later extraction harder.
**Files** `payroll/workforce.py` (remove), `hr_core/models.py` (add),
`hr_core/schemas.py`, `payroll/schemas.py`.
**DB** None. **API** None. **Frontend** None.
**Tests** Add a lint-level import-cycle assertion to the suite.
**Migration** None. **Rollback** Revert one commit.
**Perf** None.

### 0.5.2 Move `establishments` and `contractors` to hr_core
**Why** `employees` carries FKs into both, so the schema says hr_core depends on
payroll while the code says the reverse. An establishment is an organisation
concept payroll *consumes*; a contractor is a vendor.
**Files** `payroll/workforce.py` → `hr_core/org.py`; imports in
`payroll/{contractors,readiness,validation,service,workforce_routes}.py`.
**DB** **No table rename, no data migration** — only the owning Python package
changes.
**API** Unchanged (routes keep their `/payroll/...` paths; the URL is a product
decision, not an ownership one).
**Frontend** None.
**Tests** Full suite must pass unchanged — that is the proof it was a pure move.
**Migration** None. **Rollback** Revert.
**Perf** None.

### 0.5.3 Move `modules/audit` → `core/audit`
**Why** Imported by 6 of 9 modules; it is cross-cutting. Under ADR-0001 modules
may import `core/`, so this reclassifies a third of the boundary violations as
legal by construction.
**Files** `modules/audit/**` → `core/audit/**`; ~15 import sites.
**DB** None (table name unchanged). **API** None. **Frontend** None.
**Tests** Full suite. **Migration** None. **Rollback** Revert.
**Perf** None.

### 0.5.4 Amend ADR-0001 and record the dependency DAG
**Why** A rule violated 48 times protects nothing. Document the real rule:
read-only model imports from a declared upstream, no downstream imports, cycles
are a build error.
**Files** `docs/adr/0001-modular-monolith.md` (amendment note),
new `docs/adr/0009-module-dependency-dag.md`.
**Everything else** None — this is documentation plus the lint rule from 0.5.1.

---

## PHASE 1 — Foundations

### 1.1 Effective-dated compensation
**Why** Salary overwrites today (§4.2). Without history there are no arrears, no
retro corrections, no F&F, and movement cannot distinguish a raise from a data
fix. Blocks brief §10 and most of Phase 5.
**Files** new `modules/compensation/{models,service,routes,schemas}.py`;
`payroll/service.py`, `ledger.py` (resolve by period); `salary-editor.tsx`.
**DB** `compensation_versions`, `compensation_lines`; back-fill; retain
`salary_components` one release.
**API** `/compensation/**`; `PUT /payroll/employees/{id}/salary` becomes a shim
that creates a version.
**Frontend** Salary editor gains effective-from + a history list; the person page
shows salary history.
**Tests** Resolution by period; retro correction; mid-month revision; overlapping
versions rejected; back-fill equality; the §24 determinism invariant.
**Migration** §25.2 — dual-read a full cycle. Highest-risk migration in the plan.
**Rollback** Flag back to `salary_components`; both are written during dual-read.
**Perf** One extra join, removed by 1.3.

### 1.2 Attendance → work facts bridge
**Why** §4.1. A month of no-shows is currently paid in full. Highest-severity
correctness defect in the system.
**Files** new `modules/attendance/bridge.py`; `payroll/readiness.py`,
`validation.py`; a Celery beat task.
**DB** Index `attendance_events(employee_id, occurred_on)`.
**API** `POST /workforce/facts/derive?period=`.
**Frontend** Readiness gains an attendance-completeness check; validation lists
unexplained absences with a regularise action.
**Tests** Absent day → unapproved fact → validation finding, never a silent
deduction; leave beats absence; holidays produce nothing; idempotent re-runs.
**Migration** Additive.
**Rollback** Stop the beat task; derived facts are identifiable by source and
deletable.
**Perf** Bounded by employees × days; bulk insert; runs off the request path.

### 1.3 PayrollContext bulk load — eliminate the N+1
**Why** 14 q/employee measured (§4.4). Blocks any headcount above ~1,000 and
makes 1.4 pointless without it.
**Files** new `payroll/context.py`; rewrite `build_run`, `compute_payslip`,
`ledger.rebuild` to take a context rather than a session.
**DB** None — indexes already exist; the cost is round-trips, not scans.
**API** None.
**Frontend** None.
**Tests** Shadow-run: old and new paths produce byte-identical payslips for the
same period. A query-count assertion (`≤ 25` regardless of N) that fails if the
N+1 returns.
**Migration** None.
**Rollback** Keep the old path behind a flag one release.
**Perf** The point: 14N → ~15 total. 1,000 employees: 14,000 queries → 15.

### 1.4 Async payroll calculation
**Why** Brief §40. Depends on 1.3 — making a slow thing async without making it
fast only moves the problem.
**Files** `workers/celery_app.py`, `payroll/routes.py`, `pay/page.tsx` polling.
**DB** `payroll_runs`: `job_state`, `job_id`, `progress`, `calculation_error`;
plus `failed_tasks` (Platform §8).
**API** `POST /payroll/runs` → 202 (§25.3).
**Frontend** Progress state on the run; poll every 2s while RUNNING; a failure
state that offers retry.
**Tests** Retry does not double-write; superseded job exits clean; worker killed
mid-run leaves the run recoverable; advisory lock prevents concurrent calculation.
**Migration** Additive.
**Rollback** `?async=false`.
**Perf** Removes payroll from the request path entirely.

### 1.5 Run approval state
**Why** Brief §39. Today `draft → finalized` in one step, with no named human
accepting the numbers before they become immutable.
**Files** `payroll/models.py`, `service.py`, `routes.py`; `pay/page.tsx`.
**DB** `approved_by`, `approved_at`; `status` enum widened.
**API** `POST /payroll/runs/{id}/submit`, `/approve`.
**Frontend** An approval step in the run header; frozen state reads square, per
the design system.
**Tests** Illegal transitions rejected; segregation of duties when enabled;
approval invalidated when inputs change.
**Migration** Additive; existing rows map `draft→DRAFT`, `finalized→FINALIZED`.
**Rollback** Additive columns; old flow still valid.
**Perf** None.

### 1.6 Artifact infrastructure + first exports
**Why** Nothing is downloadable (§4.5). Prerequisite for compliance evidence
and payments.
**Files** new `core/artifacts/{models,service,jobs}.py`, `render/` package.
**DB** `artifacts`.
**API** `/artifacts/**`.
**Frontend** Download affordance on the run; an artifacts list with status.
**Deliverables** Payroll register (CSV), summary (XLSX), single payslip PDF
(sync), bulk payslips (async).
**Tests** Checksum stability; FAILED retryable; authorization re-checked at
download; expiry sweep; a 10,000-payslip job does not touch the request path.
**Migration** Additive.
**Rollback** Feature-flagged.
**Perf** Async by default; only single-payslip renders inline.
**Blocked on** the storage-backend decision (§26).

### 1.7 Bank details, PAN, designation
**Why** Blocks readiness checks and all of Phase 3.
**Files** `hr_core/{models,schemas,routes}.py`; person page.
**DB** `employee_bank_accounts`; `employees.pan`, `.designation`.
**API** `/hr/employees/{id}/bank-accounts`.
**Frontend** Bank section on the person page, masked by default.
**Tests** Masking; `pii.read_bank` gate; financial audit written on full read;
IFSC validation.
**Migration** Additive.
**Rollback** Additive.
**Perf** None.

### 1.8 Pagination and virtualization
**Why** No list endpoint is bounded; a 2,000-employee tenant ships a 2,000-row
payload and lays out 2,000 DOM nodes.
**Files** `hr_core/routes.py`, `payroll/routes.py`, list components.
**DB** None.
**API** `limit`/cursor on every list; new `GET /payroll/runs/{id}/payslips`.
**Frontend** Virtualized payslip and directory lists; server-side search.
**Tests** Cursor stability under concurrent insert; default and max limits.
**Migration** None.
**Rollback** Defaults preserve current behaviour below the limit.
**Perf** The point.

---

## PHASE 2 — Compliance

### 2.1 Obligation engine
**Why** Brief §20–21; the gap between a calculator and a system.
**Files** new `modules/compliance/**`; `payroll/service.py` emits on finalize.
**DB** `compliance_obligations` (with `obligor`, without `overdue`),
`compliance_evidence`.
**API** `/compliance/obligations/**`.
**Frontend** Compliance Control Center — compact operational rows, not KPI cards.
**Tests** Emission on finalize; applicability → NOT_APPLICABLE; state-machine
legality; the §24 invariant that obligation total equals the sum of payslip
statutory lines.
**Migration** Additive; back-fill obligations for existing finalized runs
read-only.
**Rollback** Read-only for one cycle (§26.5) before the workflow is enabled.
**Perf** Emission is O(establishments × statutes), not O(employees).

### 2.2 Compliance calendar
**Why** Brief §27. HR should not maintain a separate deadline tracker.
**Files** `modules/compliance/calendar.py`; a calendar view.
**DB** Due dates derived from `(jurisdiction, statute, obligation_type, period)`
rules — never hardcoded in a component.
**API** `/compliance/calendar?from=&to=`.
**Frontend** Due soon / today / overdue / completed, filterable by statute,
establishment, month, status, owner.
**Tests** `OVERDUE` derived correctly at boundaries; jurisdiction-specific dates.
**Migration** Additive. **Rollback** Additive. **Perf** Indexed on
`(company_id, status, due_date)`.

### 2.3 Statutory registers as artifacts
**Why** Evidence must be downloadable and findable later.
**Files** `render/registers.py`; compliance routes.
**DB** None beyond `artifacts`.
**API** `POST /artifacts {kind: pf_register|esi_register|pt_register}`.
**Frontend** Generate + download from each obligation.
**Tests** Register totals reconcile to the run; checksum stability.
**Migration** None. **Rollback** Flagged. **Perf** Async.

### 2.4 Financial audit + audit query API
**Why** Platform §10 lag (§22); brief §34.
**Files** `core/audit/**`; new routes.
**DB** `financial_audit`; `audit_events.actor_ip`, `.user_agent`.
**API** `GET /audit` with filters.
**Frontend** Audit view, `audit.read` gated; financial entries need
`audit.read_financial`.
**Tests** Before/after captured on compensation and adjustment changes;
unauthorized readers see nothing; filters correct.
**Migration** Additive.
**Rollback** Additive. **Perf** Indexed on entity + period.

---

## PHASE 3 — Payments

### 3.1 Bank validation · 3.2 Batch + file artifact · 3.3 Lifecycle and per-item retry · 3.4 Reconciliation
**Why** `FINALIZED ≠ PAID` (brief §29).
**Files** new `modules/payments/**`; `render/bank_file.py`.
**DB** `payment_batches`, `payment_items`.
**API** `/payments/**`.
**Frontend** Payment batch view; failed items with retry.
**Tests** The §24 invariant that a failed payment leaves the payslip untouched;
duplicate payment prevented; partial payment reconciles.
**Migration** Additive. **Rollback** Flagged; file generation ships before
status tracking. **Perf** Batch-sized, async file generation.
**Depends on** 1.6 and 1.7.

---

## PHASE 4 — Search and reporting

**4.1** Postgres FTS + trigram entity lookup wired into `⌘K` (implements the
`Search` seam per ADR-0007). **4.2** Filtered record search across payslips,
obligations, adjustments, artifacts, audit. **4.3** The operational report set
from brief §47, all async above a threshold, all as artifacts.
**Tests** Tenant-scoped results by construction; relevance smoke tests; no
cross-tenant index. **Perf** GIN indexes; measured before/after.

---

## PHASE 5 — Statutory breadth

LWF · minimum-wage engine · statutory bonus · gratuity · F&F · arrears. Each is
a rule row plus a calculator function on the §16.2 architecture — **no schema
change**, which is the point of doing §16.2 first. Arrears additionally depend on
1.1.

---

## PHASE 6 — TDS

Only as a complete subsystem: regime election, declarations, proofs, previous
employment, projection, monthly computation, 24Q, Form 16. Until then TDS stays
an explicit authorized input, labelled as such.

---

## PHASE 7 — Lumo payroll intelligence

Explain a payslip, summarise movement, surface anomalies, find records, explain
compliance status. **Never** calculates, approves, finalizes or submits. Facts
from tools only — the existing grounding contract and its test extend unchanged.

---

## Definition of done — honest scorecard

Against the brief's §51, enumerated rather than asserted.

**Met today (11 of 29):** deterministic calculation · versioned statutory rules
· reproducible history *in principle* (inputs and rule versions are stored) ·
immutable after finalization · adjustments handle corrections · exceptions
surfaced · movement explainable · blue-collar work facts · contractor
reconciliation · RLS enforced · permissions enforced.

**Met after Phase 1 (+7 → 18):** HRMS data feeds payroll automatically (1.2) ·
no upstream duplication (1.1, 1.2) · payroll downloadable (1.6) · payslips
downloadable (1.6) · background jobs idempotent (1.4) · large runs do not block
HTTP (1.3, 1.4) · load tests exist (1.3).

**Met after Phase 2 (+6 → 24):** compliance obligations exist · statutory
outputs generated · filing states tracked · evidence preserved · audit
searchable · invariant tests extended.

**Remaining (5):** payment failures recoverable (Phase 3) · reconciliation
complete (Phase 3) · payroll searchable (Phase 4) · security tests complete
(spread across phases) · failure/retry tests complete (spread).

The criterion that is not on the brief's list and matters most: **a payroll
professional should be able to answer "why is this number what it is?" without
opening Excel.** The provenance chain already makes that answerable *in the
data*. What is missing is the UI that exposes it (brief §9) and the export that
lets them prove it to someone else.

---

**Sources for §16.1:**
[Code on Wages, 2019 (as on 21 Nov 2025) — India Code](https://www.indiacode.nic.in/bitstream/123456789/15793/1/aA2019-29.pdf) ·
[Implementation of labour codes: what changes and the road ahead — KPMG India](https://kpmg.com/in/en/blogs/2025/12/implementation-of-labour-codes-what-changes-and-road-ahead.html) ·
[Key Highlights of Labour Codes — Lexology](https://www.lexology.com/library/detail.aspx?g=487a903d-0a7d-458c-8a7f-bb85c22d7b7b)
