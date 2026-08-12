# Payroll & Compliance — Architecture Assessment and Plan

**Status:** Phase 0 deliverable. Nothing in this document has been implemented.
**Audit date:** 12 August 2026, against `main` @ `6127629`.
**Method:** every claim below was checked against the code, the database, or a
measurement. Where something is absent, it is marked **MISSING** rather than
described in the future tense. Where the commissioning brief asserted something
exists and it does not, that is called out explicitly in §0.

---

## 0. Where the brief is wrong, and what it missed

Read this section first. It changes what the plan should be.

### 0.1 Three things the brief said to "preserve" do not exist

The brief's §3 lists current work to preserve. Three items on that list are not
implemented:

| Claimed | Reality |
|---|---|
| "searchable data" | `core/search/base.py` is a Protocol and a `PgSearch` class whose `query()` is `raise NotImplementedError`. **No search exists anywhere in the product.** |
| "downloadable artifacts" | Zero export code. No CSV, XLSX, or PDF generation. PyMuPDF is a dependency but only *reads* résumés in ATS. **No payslip has ever been downloaded.** |
| "employee finalized-payslip visibility" | This one is real and correctly built (`ess/routes.py:192`, filter enforced in the service, not the handler). |

"calculation traces" is half-real: `payslip.breakdown` carries `basis` and
`rule_versions`, which is genuine provenance. There is no UI that exposes it.

**Consequence:** search, downloads and artifacts are not hardening tasks. They
are greenfield subsystems, and the plan sizes them accordingly.

### 0.2 The FINANCE domain does not exist at all

The brief's §1 instructs: *"Do not duplicate employee, attendance, leave,
compensation, expense or loan master data inside Payroll."*

There is no expense, reimbursement, loan, advance, recovery, or bonus table
anywhere in the repository. The module list is: `assistant, ats, attendance,
audit, auth, ess, hr_core, leave, notifications, payroll, public, system,
tenant, work_calendar`. There is nothing to avoid duplicating.

**Recommendation — and this is a disagreement with the brief.** Do not build a
Finance module to satisfy an architectural rule. Reimbursements, loan EMIs and
advances should enter payroll as **payroll inputs with a typed source**, which
the ledger already supports (`source` ∈ `structure | work_facts | manual |
import | adjustment`; add `expense`, `loan`, `advance`). Build a Finance module
when a *second consumer* of that data appears — an expense approval workflow, a
loan balance that must amortise across months, an accounting export. Until
then, a full expense subsystem is exactly the over-building the brief's §49
forbids. The seam that matters is the `source` enum plus an external reference
id, and that is cheap to add now and cheap to repoint later.

**Loans are the exception.** A loan has a *balance* that survives across
periods, and a balance cannot live in a per-period ledger without being
recomputed wrongly. Loans need a real table before the first deduction is
taken. Reimbursements and one-off bonuses do not.

### 0.3 Bank details do not exist — which blocks two named requirements

There is no `bank_account`, `ifsc`, or `pan` column anywhere. Grep returns
nothing. `Employee` carries `uan` but not `pan`. There is also no
`designation`.

This blocks the brief's §7 ("2 missing bank details" as a readiness check) and
all of §29 (payments). Both require a schema change first.

### 0.4 The most dangerous defect in the system is not in the brief

**Payroll does not read attendance. At all.**

- `AttendanceEvent` is never imported by any payroll module.
- LOP is derived *only* from approved leave requests on leave types flagged
  unpaid (`service.unpaid_leave_days`).
- `WorkFact` rows are created only by `POST /workforce/facts` — nothing derives
  them from punches.

Therefore: **an employee who never punches in for an entire month, and files no
leave, is paid in full.** The attendance module is live (there is a working
`/me/attendance/punch` endpoint), so this is not a dormant feature — it is a
module that produces data payroll silently ignores.

This is also the real duplication in the system, and it is not the one the
brief warns about. There are two independent answers to "did this person work
today" — `attendance_events` (punches, white-collar) and `work_facts` (days and
hours, blue-collar) — with no bridge and no reconciliation. That is the single
highest-value architectural fix available, and it ranks above compliance.

### 0.5 Statutory: the 21 Nov 2025 assumption is coarser than reality

`rules.py` holds `LABOUR_CODE_START = date(2025, 11, 21)` as one global flag
that switches the wage definition for every statute simultaneously.

Verified against sources: the four labour codes took effect 21 November 2025,
but **only certain provisions of the Code on Wages and the Social Security Code
were brought into force**, with final Central Rules expected later and state
rules following separately. The Ministry issued clarifying FAQs in January and
March 2026.

**Consequence:** a single global date is wrong in principle, not just in
detail. Different statutes may adopt the new wage basis on different dates, and
state rules will vary by jurisdiction. This validates the brief's §14 — *each
statutory scheme must determine its applicable basis* — and it means
`wage_definition` must become a **per-statute, per-jurisdiction, effective-dated
resolution**, not one constant.

It also means the product must not state compliance certainty. See §27.

### 0.6 Smaller disagreements

- **§21 `OVERDUE` should not be a stored status.** It is a derived property of
  `(due_date, status, now)`. Storing it guarantees rows that are stale until a
  cron touches them. Derive it in the query; keep the stored status honest.
- **§39 mixes two lifecycles in one column.** `QUEUED`, `CALCULATING`,
  `CALCULATED` are *job* states; `DRAFT`, `APPROVED`, `FINALIZED` are *business*
  states. Putting them in one enum means a failed worker corrupts the business
  state. Use `status` (business) + `job_state` (execution) as separate columns.
  This is a real improvement on the brief.
- **§41 "test at 50,000".** 50,000 employees in a single tenant is not the SME
  target and would force a different persistence strategy (partitioning,
  COPY-based bulk load). Plan and test to **10,000**, publish the measured
  ceiling, and treat beyond that as a separate architecture exercise rather
  than a number to claim.
- **§38 nav (`PEOPLE / WORKFORCE / FINANCE / PAYROLL / COMPLIANCE / REPORTS`)**
  adds three top-level items to a product whose design system explicitly states
  *"every additional top-level item is a tax on the product's clarity"*
  (`DESIGN.md` §3). FINANCE has no content. REPORTS is a container, not a
  destination — reports belong next to the data they describe. Recommend
  **PAYROLL + COMPLIANCE only**, both permission-gated.

---

## 1. Current architecture assessment

A modular monolith: FastAPI + PostgreSQL + Redis + Celery, Next.js frontend.
Shared-schema multi-tenancy with `FORCE ROW LEVEL SECURITY` under a
non-superuser `app` role. 16 migrations, 373 backend tests passing.

**The core payroll model is sound and unusually well-founded for its age.** The
chain work facts → input ledger → statutory wage basis → versioned rule →
deterministic calculation is the right shape, is really implemented, and is
tested. Money is `Decimal`/`NUMERIC(12,2)` end-to-end and never becomes a float,
including across the API boundary (amounts are strings in JSON and in React).

**What is weak is everything around the calculation**: the run executes
synchronously inside the HTTP request, nothing can be exported, nothing can be
searched, compensation overwrites history, attendance is ignored, and
compliance does not exist as a concept.

The gap is best stated as: **this is a correct payroll calculator inside an
HRMS. It is not yet a payroll system.** The brief's framing of that distinction
(§2) is exactly right.

---

## 2. Existing functionality inventory

Verified by reading code, not by reputation. `payroll/` is 3,882 lines across
16 files; the payroll frontend is 8 components totalling ~1,750 lines.

### 2.1 Present and correct

| Capability | Where | Notes |
|---|---|---|
| Decimal money end-to-end | `MONEY` type, all models | No float anywhere in the money path |
| Tenant isolation + RLS | `core/tenant.py`, all `TenantBase` | FORCE RLS, non-superuser role; 2 isolation tests |
| Immutable finalized runs | `service.build_run` raises `RunFinalized` | Enforced in the service |
| Adjustments | `adjustments.py`, 20 tests | Corrections post into a later period |
| Work facts | `workforce.py:94`, 21 route tests | Hours/site/shift/night/premium, approval state |
| Payroll input ledger | `ledger.py`, 14 + 11 tests | `source` provenance, approval, rebuild preserves manual |
| Establishment config | `workforce.py`, 13 tests | PF/ESI codes, state, minimum wage |
| Statutory rule versioning | `rules.py` | Resolved **by period**, stamped into payslip |
| Readiness | `readiness.py`, 16 tests | 9 checks (§2.3 lists them) |
| Validation | `validation.py` | 6 finding codes, blocking vs warning vs info |
| Risk | `/payroll/risk` | Shares the validation engine |
| Movement | `movement.py`, 16 tests | Decomposes MoM change into named causes |
| Provenance on payslip | `service.compute_payslip` → `breakdown.basis` | statutory_wage, added_back, EPS reason, rule versions |
| Blue-collar work facts | `WorkFactSummary` | Renders only when facts exist — no `worker_type` branch |
| Contractor reconciliation | `contractors.py`, 19 tests | Record always / approve only at zero variance |
| Manager vs HR separation | `core/auth/permissions.py` | Manager has `workfact.approve`, **no** `payroll.read` |
| Payroll invariants | `test_payroll_invariants.py` | 21 property tests |
| ESS finalized-only payslips | `ess/routes.py:192` | Filter in service, not handler |
| Lamoon design system | `DESIGN.md`, `globals.css` | Monochrome; Lumo is the only chroma |
| AI grounding contract | `assistant/tools.py` | Facts from tools only; test asserts model cannot invent clickable results |

### 2.2 Present but wrong, incomplete, or dangerous

| Issue | Severity | Evidence |
|---|---|---|
| **Payroll ignores attendance entirely** | **Critical** | No import of `AttendanceEvent` in `payroll/`; LOP only from unpaid leave |
| **Compensation overwrites; no effective dating** | **Critical** | `SalaryComponent` docstring admits it; a raise destroys the prior amount |
| **Run executes synchronously in the request** | High | `routes.py:316` calls `build_run` inline |
| **N+1: ~14 queries/employee** | High | Measured: 25→0.41s, 100→1.18s, 300→3.35s |
| **No pagination on any list endpoint** | High | `hr_core/routes.py` has no `limit`/`offset` |
| Global `LABOUR_CODE_START` flag | High | One date for all statutes — see §0.5 |
| ESI coverage wage == contribution wage | Medium | Not separated; brief §16 is right that they differ |
| Audit stores no before/after values | Medium | `AuditEvent.payload` is free-form; callers pass names, not diffs |
| Audit has no query route | Medium | `audit.record()` exists; nothing reads it back |
| Run states are only `draft \| finalized` | Medium | No approval step, no job state |
| No `updated_by` on financial rows | Medium | Only `finalized_by` exists |
| Contractor has no delete route | Low | Found during dev-data cleanup; required raw SQL |
| Frontend renders all payslips, unvirtualized | Low at 25 seats, High at 2,000 | `pay/page.tsx` maps the full array |

### 2.3 Readiness checks that exist

`employees`, `pay_components`, `salary_coverage`, `work_calendar`,
`provident_fund`, `esi`, `professional_tax`, `jurisdiction`,
`statutory_identity`.

**MISSING from readiness:** bank details (column does not exist), attendance
completeness, unapproved work facts (currently only a *validation* warning),
compensation effective-date conflicts.

### 2.4 MISSING entirely

Compliance (all of it) · payments and bank files · any export or download ·
artifact model and lifecycle · search · audit query/UI · effective-dated
compensation · arrears · F&F · gratuity · statutory bonus · LWF · TDS
computation (deliberate — see §16.7) · ECR/24Q/ESI returns · challans ·
compliance calendar · notifications module (directory exists, is empty) ·
`finance` and `compliance` roles · designation · PAN · bank details.

---

## 3. KEEP / CHANGE / REMOVE / ADD / DEFER

### A. KEEP (do not touch)

The whole of §2.1. Specifically resist "improving":

- **EPF employer split by subtraction.** `employer_epf = employer_total − EPS`.
  The brief's §15 warns against rounding both halves independently; the code
  already avoids it. Any refactor must preserve this exactly.
- **PT with no fallback chain.** An establishment with no slabs means *no PT*,
  not "borrow the company's". Silently borrowing another state's slabs files a
  wrong return.
- **TDS as an explicit input** with the UI saying so. Matches brief §19.
- **Invoice recorded always / approved only at zero variance.**
- **`invoiced = None` ≠ zero.**
- **Work facts store hours, never amounts.**
- **Manager holds `workfact.approve` but not `payroll.read`.**
- **Rule resolution by period, never by `today()`.**

### B. CHANGE

1. Effective-dated compensation (replaces overwrite).
2. Bridge attendance → work facts; make absence produce LOP.
3. Async run execution on Celery; split `status` from `job_state`.
4. Eliminate the N+1 via a preloaded `PayrollContext`.
5. Per-statute, per-jurisdiction wage-basis resolution (retire the global flag).
6. Separate ESI coverage wage from contribution wage.
7. Audit: add before/after for financial entities; add a query API.
8. Pagination + virtualization on every list.
9. Readiness: add bank, attendance, and work-fact-approval checks.

### C. REMOVE

Almost nothing. Two items:

- The global `LABOUR_CODE_START` constant, once §B.5 lands (replaced, not deleted —
  the date becomes data in a rule row).
- The `pf_wage` boolean on `PayComponent`, already superseded by `wage_basis`
  and kept only for back-compat. Remove after one release with a migration that
  asserts the back-fill is complete.

### D. ADD

Compliance obligation engine · compliance calendar · artifact model +
async generation · payslip PDF · payroll register (CSV/XLSX) · bank details +
PAN · payment lifecycle · bank file export · search · audit query UI ·
`finance` and `compliance` roles · loans (balance-bearing) · arrears · run
approval step · contractor delete.

### E. DEFER (explicitly, with the trigger that un-defers each)

| Deferred | Un-defer when |
|---|---|
| Full TDS subsystem | A customer's CA will not accept manual entry — needs regime, declarations, proofs, Form 16 |
| Gratuity, F&F | First customer exit at 5+ years tenure |
| Statutory bonus | First customer with a qualifying employee at year end |
| Minimum-wage compliance *engine* (beyond the current warning) | First blue-collar customer with multi-state sites |
| Meilisearch/ES | Postgres FTS relevance measurably insufficient |
| Kafka, microservices, sharding | Never, on current evidence. Revisit only with measurements |
| 50,000-employee tenant | A signed customer at that size |

---

## 4. Domain model

Nine aggregates. Payroll owns four of them and *reads* the rest.

```
PERSON            employee, employment, establishment assignment, statutory
                  identity, bank details                     [hr_core owns]
COMPENSATION      salary version (effective-dated), component  [NEW owner:
                  compensation, currently inside payroll]
WORKFORCE FACT    work fact, attendance event, leave request, holiday, shift
                                                    [attendance/leave/payroll]
PAYROLL INPUT     one asserted figure for one person for one period, with a
                  source and an approval                       [payroll owns]
PAYROLL RUN       period, state, totals, payslips              [payroll owns]
PAYSLIP           frozen snapshot incl. basis and rule versions[payroll owns]
ADJUSTMENT        a correction posted into a later period      [payroll owns]
STATUTORY RULE    effective-dated, per statute, per jurisdiction[rules owns]
OBLIGATION        what a finalized run made us liable for, and its
                  filing/payment/evidence lifecycle          [NEW: compliance]
```

The invariant that ties them: **a payslip is a pure function of (payroll
inputs, statutory rules, calendar) as they stood at finalization.** Everything
else is provenance. If re-running a finalized period from its stored inputs and
rule versions does not reproduce it byte-for-byte, that is a bug, and §24 makes
it a test.

---

## 5. Database model

### 5.1 Existing (16 migrations, `0001`–`0016`)

Payroll-owned: `pay_components`, `salary_components`, `payroll_settings`,
`pt_slabs`, `payroll_runs`, `payslips`, `establishments`, `work_facts`,
`payroll_inputs`, `payroll_adjustments`, `contractors`, `contractor_invoices`.

Every table is tenant-scoped, soft-deleted, and uses partial unique indexes on
live rows (`WHERE deleted_at IS NULL`) — a pattern adopted after a shipped bug
where deleting and re-adding a holiday 500'd.

### 5.2 New tables

```sql
-- §10 of the brief. The single highest-value change in this document.
compensation_versions(
  id, company_id, employee_id,
  effective_from date NOT NULL,        -- resolution is by payroll period
  effective_to   date,                 -- NULL = open-ended; maintained on insert
  reason         varchar(30),          -- hire|revision|promotion|correction|f_and_f
  approved_by, approved_at,
  supersedes_id  uuid,                 -- retro corrections chain, never overwrite
  created_at, created_by, deleted_at
)
compensation_lines(id, company_id, version_id, component_id, amount NUMERIC(12,2))

employee_bank_accounts(
  id, company_id, employee_id, account_number_enc, ifsc, account_holder_name,
  is_primary bool, verified_at, verified_by, deleted_at
)   -- account number encrypted at rest; §14.4

loans(id, company_id, employee_id, principal, outstanding, emi, start_period,
      status, created_by)              -- balance-bearing, so a real table
loan_instalments(id, company_id, loan_id, period, amount, payslip_id)

compliance_obligations(
  id, company_id, establishment_id, statute, obligation_type, period,
  amount NUMERIC(14,2), due_date, status, rule_version,
  source_run_id, return_ref, challan_ref, payment_ref,
  submitted_at, paid_at, reconciled_at,
  created_by, updated_by, created_at, updated_at, deleted_at
)   -- NOTE: no `overdue` column. Derived: status NOT IN (paid, reconciled,
    --       not_applicable) AND due_date < current_date
compliance_evidence(id, company_id, obligation_id, artifact_id, kind, note)

artifacts(
  id, company_id, kind, period, scope_json, status, storage_key,
  content_type, size_bytes, checksum_sha256, generated_by, generated_at,
  expires_at, error, deleted_at
)

payment_batches(id, company_id, run_id, status, total, file_artifact_id, ...)
payment_items(id, company_id, batch_id, employee_id, amount, bank_account_id,
              status, failure_reason, reference)

financial_audit(   -- restricted; before/after for money-bearing entities
  id, company_id, entity, entity_id, action, actor_user_id,
  before JSONB, after JSONB, correlation_id, created_at
)
```

### 5.3 Column additions

`employees`: `pan`, `designation`, `primary_bank_account_id`.
`payroll_runs`: `job_state`, `job_id`, `progress`, `approved_by`, `approved_at`,
`calculation_error`.
`payroll_inputs.source`: add `expense`, `loan`, `advance`, `arrear` + a nullable
`source_ref` (uuid) and `source_note`.

### 5.4 Indexes (beyond what exists)

Existing hot columns are already indexed — verified in `pg_indexes`:
`payroll_inputs(employee_id, period)`, `work_facts(employee_id, day)`,
`salary_components(employee_id, component_id)`, all partial on live rows. The
measured cost is round-trip *count*, not scan time, so **do not add indexes
hoping to fix the N+1** — batch the queries instead.

New composites required:
```
compensation_versions(employee_id, effective_from DESC) WHERE deleted_at IS NULL
compliance_obligations(company_id, status, due_date)
compliance_obligations(establishment_id, statute, period)
artifacts(company_id, kind, period)
payment_items(batch_id, status)
attendance_events(employee_id, occurred_on)      -- for the attendance bridge
```

---

## 6. Data ownership map

One writer per fact. Payroll reads; it does not master.

| Fact | Master | Payroll's access |
|---|---|---|
| Identity, department, manager, establishment | `hr_core` | read |
| Statutory identity (DOB, UAN, PF first joined, IW flag) | `hr_core` | read |
| Bank details | `hr_core` (new) | read; payments reads |
| Salary | `compensation` (new, extracted from payroll) | read, resolved by period |
| Punches | `attendance` | read via the bridge (§17) |
| Leave | `leave` | read (unpaid → LOP) |
| Working days, holidays | `work_calendar` | read |
| Work facts (days, hours, site, OT) | `payroll` | own |
| Payroll inputs | `payroll` | own |
| Run, payslip, adjustment | `payroll` | own |
| Statutory rules | `rules` | read |
| Obligations, returns, challans, evidence | `compliance` (new) | reads finalized runs only |
| Artifacts | `core/artifacts` (new) | write via job |

**Rule:** compliance never reads a draft run. Its only input is a finalized
one. This keeps the obligation ledger from tracking numbers that are still
moving.

---

## 7. Payroll lifecycle

```
PREPARE     upstream data lands: compensation versions, attendance, leave,
            work facts, expenses. HR does not type any of it into payroll.
   ↓
REVIEW      readiness (can it run?) + validation (is it valid?)
   ↓        HR resolves blockers. Only blockers are shown.
CALCULATE   async job. Ledger rebuilt, statutory wage derived, rules resolved
            by period, payslips written in bulk.
   ↓
REVIEW      risk (anything unusual?) + movement (why did it change?)
   ↓
APPROVE     a named human accepts the numbers. New state; today this is absent.
   ↓
FINALIZE    immutable. Emits statutory liability → obligations.
   ↓
PAY         payment batch → bank file → submitted → paid → reconciled
   ↓
FILE        obligations → returns → challans → payment → acknowledgement
   ↓
RECONCILE   calculated vs filed vs paid, per statute per establishment
```

Corrections after FINALIZE are adjustments in a later period. There is no
reopen.

---

## 8. Compliance lifecycle

Compliance is not reports. It manages obligations with a due date and evidence.

```
run FINALIZED
   ↓ emit
liability per (establishment, statute, period)
   ↓
obligation created  → NOT_APPLICABLE where the statute does not apply
   ↓
validation          → VALIDATION_FAILED with per-employee findings
   ↓
return prepared     → GENERATED (artifact: ECR / ESI / PT return)
   ↓
submitted           → SUBMITTED | SUBMISSION_FAILED
   ↓
challan             → CHALLAN_GENERATED
   ↓
payment             → PAYMENT_PENDING → PAID | PAYMENT_FAILED
   ↓
acknowledgement     → evidence artifact stored
   ↓
reconciled          → RECONCILED (calculated == filed == paid)
```

`OVERDUE` is derived, never stored (§0.6).

**V1 scope:** obligations, due dates, calendar, statutory registers as
downloadable artifacts, manual submission with reference + acknowledgement
capture, and reconciliation. **Not** direct portal integration — EPFO/ESIC
submission is a human uploading a file. Modelling it as an artifact plus a
recorded reference is honest and useful; claiming automated filing is not.

---

## 9. State machines

### 9.1 Payroll run — two columns, not one

```
status     DRAFT → CALCULATED → PENDING_APPROVAL → APPROVED → FINALIZED
job_state  IDLE | QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
```

Legal transitions:

| From | To | Guard |
|---|---|---|
| DRAFT | CALCULATED | job SUCCEEDED, no blocking findings |
| DRAFT | DRAFT | recalculation; always allowed |
| CALCULATED | PENDING_APPROVAL | HR submits for approval |
| CALCULATED | DRAFT | any input changes → invalidate |
| PENDING_APPROVAL | APPROVED | `payroll.approve`, actor ≠ preparer if segregation on |
| PENDING_APPROVAL | DRAFT | rejected, with reason |
| APPROVED | FINALIZED | `payroll.finalize` |
| FINALIZED | — | terminal. No transition out. Ever. |

A failed worker sets `job_state = FAILED` and leaves `status` untouched. The run
stays recoverable — brief §45.

### 9.2 Obligation

12 states as listed in brief §21, minus `OVERDUE`. Terminal: `RECONCILED`,
`NOT_APPLICABLE`.

### 9.3 Payment

```
DRAFT → VALIDATED → FILE_GENERATED → SUBMITTED → PROCESSING
      → PAID | PARTIALLY_PAID | FAILED → RECONCILED
```

**A payment failure never mutates payroll.** The payslip is finalized and
correct; only the `payment_item` failed. This is the brief's §45 and it is
right.

---

## 10. API architecture

Existing payroll surface (verified): 16 routes under `/payroll`, 5 under
`/workforce`, 6 under `/payroll/contractors`.

### 10.1 Changes to existing

```
POST /payroll/runs          → 202 Accepted, returns {run_id, job_id}
                              (breaking; see §25.3 for the transition)
GET  /payroll/runs/{id}     → adds job_state, progress, error
GET  /payroll/runs/{id}/payslips → NEW, paginated; today payslips are
                              embedded in the run detail and unbounded
POST /payroll/runs/{id}/submit   → NEW  CALCULATED → PENDING_APPROVAL
POST /payroll/runs/{id}/approve  → NEW  requires payroll.approve
```

### 10.2 New surfaces

```
/compensation/employees/{id}/versions        GET POST
/compensation/employees/{id}/versions/{vid}  PATCH DELETE
/compensation/resolve?employee_id=&on=       GET   -- what applied on a date

/compliance/obligations         GET (filters: statute, establishment, status,
                                     period, owner, due_before)
/compliance/obligations/{id}    GET PATCH
/compliance/obligations/{id}/generate|submit|challan|pay|acknowledge|reconcile
/compliance/calendar?from=&to=  GET
/compliance/reconciliation?period=&statute=  GET

/payments/batches               GET POST
/payments/batches/{id}/validate|file|submit|reconcile
/payments/items/{id}/retry

/artifacts                      GET (filters), POST (request generation)
/artifacts/{id}                 GET (metadata + status)
/artifacts/{id}/download        GET (streams; authorization re-checked)

/search?q=&kind=&limit=         GET
/audit?actor=&entity=&period=&action=  GET  (requires audit.read)
```

### 10.3 Conventions to adopt

- Every list endpoint takes `limit` (default 50, max 200) and a cursor.
  Returns `{items, next_cursor, total?}`.
- Long operations return `202` with a job handle. No exceptions.
- Money stays a string in JSON. Non-negotiable.
- Idempotency: `POST /payroll/runs` is already idempotent per (company,
  period) via a unique constraint. Payment submission and artifact generation
  take an `Idempotency-Key` header.

---

## 11. Background job architecture

Celery + Redis already exist (`workers/celery_app.py`, three queues:
`high`/`normal`/`background`) but run only two ATS tasks. Payroll uses none.

```
POST /payroll/runs
  → upsert run (unique on company+period)
  → job_state = QUEUED, job_id = uuid
  → calculate_payroll.apply_async(args=[company_id, run_id, job_id],
                                  queue="high")
  → 202 {run_id, job_id}

worker:
  acquire advisory lock on (company_id, run_id)   -- no double calculation
  if run.job_id != job_id: abort (superseded)
  job_state = RUNNING
  load PayrollContext in bulk (§23)
  compute in memory
  bulk upsert payslips
  job_state = SUCCEEDED, status = CALCULATED
  on exception: job_state = FAILED, calculation_error = str, run untouched
```

**Idempotency:** the advisory lock plus the `job_id` check means retrying the
same job cannot double-write, and a superseded job exits without touching
anything. Recalculation is naturally idempotent because `build_run` already
updates in place.

**Queues:** `high` = payroll calculation (a human is waiting). `normal` =
artifact generation. `background` = compliance calendar sweeps, reconciliation.

**Progress:** integer 0–100 on the run row, updated every N employees. The UI
polls `GET /payroll/runs/{id}` every 2s while `job_state = RUNNING`. WebSockets
are not worth the infrastructure for a job that runs seconds to a minute.

**Cancellation:** allowed only in QUEUED and RUNNING, and only before the bulk
write begins. After the write starts it runs to completion — a half-written run
is worse than a slow one.

---

## 12. Search and index strategy

Today: `PgSearch.query()` raises `NotImplementedError`. Nothing is searchable.

**V1 = PostgreSQL FTS inside the RLS boundary.** No new infrastructure. The
seam (`core/search/base.py`) already anticipates swapping in Meilisearch, and
that stays a seam until relevance measurably fails.

Two mechanisms, because they answer different questions:

1. **Entity lookup (exact-ish):** employee by name / code / UAN / PAN,
   contractor, challan ref, return ref, payment ref. `pg_trgm` GIN index on the
   text columns. This is what the `⌘K` palette needs.
2. **Record search (filtered):** payslips, obligations, adjustments, artifacts,
   audit events — filtered by period, establishment, status, amount range.
   These are *queries with filters*, not full-text. Plain composite indexes.

Conflating the two is the usual mistake. A payslip search is a filtered query;
only the employee name inside it wants trigram matching.

**RLS interaction:** search runs as the tenant role, so results are
tenant-scoped by construction. Do not build a cross-tenant index.

---

## 13. Artifact and download architecture

Nothing exists. `LocalBlobStore` works; `DriveBlobStore` is a stub.

```
POST /artifacts {kind, period, scope}
  → row: status=QUEUED, deterministic key
  → generate_artifact.apply_async(queue="normal")
  → 202 {artifact_id}

worker: GENERATING → render → blob.put() → checksum → READY | FAILED
```

Lifecycle: `QUEUED → GENERATING → READY | FAILED`, plus `EXPIRED` by a sweep.

Every artifact stores: kind, period, scope, `generated_by`, `generated_at`,
`checksum_sha256`, `storage_key`, `size_bytes`, `expires_at`. **A generated
file must be findable months later** — that is what makes it evidence rather
than a download.

Deterministic key:
`{company_id}/{kind}/{period:%Y-%m}/{scope_hash}-{version}.{ext}`

**Synchronous exception:** a *single* payslip PDF may generate inline (~50ms).
Everything bulk is async. 10,000 payslips is never an HTTP request.

**Authorization is re-checked at download**, not just at request. An artifact id
is not a capability — someone who changes role between request and download must
lose access.

**Formats:** CSV for registers (universally openable, streams, no dependency),
XLSX where HR will pivot (`openpyxl` in write-only mode), PDF for payslips
(WeasyPrint or ReportLab — decide at implementation; PyMuPDF is a reader).

---

## 14. Security and RLS model

### 14.1 Keep as-is

Shared schema, `FORCE ROW LEVEL SECURITY`, non-superuser `app` role,
`SET LOCAL app.company_id` per request. `SET LOCAL` is lost on commit, and the
next query then casts `''` to uuid and errors loudly rather than leaking — a
good failure mode discovered in testing. New tables inherit `TenantBase` and
get the same policy; the migration template must not be bypassed.

### 14.2 New tables need policies

Every table in §5.2. A migration that adds a table without an RLS policy must
fail CI — add a test that enumerates `pg_tables` and asserts every tenant table
has `rowsecurity = true` and a `FORCE` policy. This is cheap and catches the
mistake permanently.

### 14.3 RLS defeats data migrations — a known trap

Migrations run as the non-superuser under FORCE RLS. A data `UPDATE` in a
migration matches **zero rows and reports success**. This already bit us once
(caught only because a subsequent `SET NOT NULL` failed). Every data migration
must suspend RLS on *every* table it touches, including the read side of a
join, and must assert affected row counts.

### 14.4 Sensitive fields

Bank account numbers and PAN are encrypted at rest (app-level AEAD, key from
config/KMS), masked in all list responses (`••••3421`), and full values require
a dedicated permission plus a financial-audit entry on read.

### 14.5 Object-level authorization

Route-level `require()` is not enough for artifacts and payslips. A payslip
fetch must verify the caller either holds `payroll.read` for that tenant or is
the subject employee. The ESS module already models this correctly by taking no
id at all — extend that pattern rather than adding id checks.

---

## 15. Permission model

Today: 4 roles (`admin`, `hr`, `manager`, `employee`), permissions derived from
role, no per-user grants. Manager correctly lacks `payroll.read`.

### 15.1 New permissions

```
payroll.read  payroll.write  payroll.approve  payroll.finalize
workfact.read workfact.write workfact.approve                  (exists)
compensation.read  compensation.write  compensation.approve
compliance.read  compliance.write  compliance.submit  compliance.pay
payment.read  payment.write  payment.submit
artifact.read  artifact.generate
audit.read  audit.read_financial
pii.read_bank
```

### 15.2 New roles

| Role | Holds | Deliberately excludes |
|---|---|---|
| `payroll_admin` | payroll.*, compensation.*, workfact.*, artifact.* | compliance.submit, payment.submit |
| `compliance_officer` | compliance.*, artifact.read, payroll.read | compensation.*, payment.pay |
| `finance` | payment.*, artifact.read, payroll.read (totals) | compensation.read, pii beyond bank |

**Finance is the important one.** The brief's §35 is right: finance needs to pay
people without acquiring unrestricted access to everyone's compensation
history. Finance sees the payment batch — name, bank account, net amount — not
the salary structure, not the movement report, not the adjustment reasons.

### 15.3 Segregation of duties

Preparer ≠ approver, and approver ≠ payment submitter, as a per-company toggle
(off for a 3-person SME where it would deadlock, on by default above ~50
employees). Enforced in the service, tested.

---

## 16. Indian statutory rule architecture

### 16.1 The core change: basis is per statute

Today `statutory_wage()` produces one number that every scheme uses, gated by
one global date. Replace with:

```python
def basis_for(statute: str, *, period: date, jurisdiction: str,
              lines: Sequence[Line]) -> WageBasis
```

resolving `(statute, jurisdiction, period)` → an effective-dated
`WageDefinition` row. EPF, ESI, gratuity, bonus and minimum wage each ask
separately. This is required by §0.5, not merely tidy.

### 16.2 Rule storage

Rules move from Python constants to effective-dated **rows** with a Python
fallback for the current version, so a rate change is a migration and not a
deploy. Each row: `statute, jurisdiction, effective_from, effective_to,
version, params JSONB, source_note` — where `source_note` cites the
notification. Every payslip already stamps the resolved version; keep that.

### 16.3 EPF — keep the maths, extend the inputs

12%/12%; EPS 8.33% capped on ₹15,000; **employer EPF by subtraction**; EDLI
0.5%; admin 0.5% with a ₹500/month per-establishment floor. EPS eligibility:
age ≥58, or first membership on/after 1 Sep 2014 above the ceiling. Add:
reduced-rate establishments (10%), international workers outside the ceiling
(the flag exists, the rule does not yet consume it), voluntary PF, and PF on
full wage where the employer opts in (`on_full_wage` already a parameter).

### 16.4 ESI — separate the two wages

0.75%/3.25%, `ROUND_CEILING` per reg. 40, Apr–Sep / Oct–Mar contribution
periods with mid-period crossing not stopping contribution (implemented and
tested). **Add:** coverage wage ≠ contribution wage. Coverage decides *whether*
someone is covered; contribution decides *on what*. OT is excluded from the
coverage test but included in the contribution wage — conflating them
mis-covers anyone near the limit whose OT pushes them over.

### 16.5 Professional tax — keep the strictness

Per-establishment jurisdiction, no fallback. Add explicit `NOT_APPLICABLE` (the
state does not levy) as distinct from `NOT_CONFIGURED` (it does and we have not
set it up). Today both look like "no PT", which hides a real misconfiguration.

### 16.6 LWF, minimum wage, bonus, gratuity

All jurisdiction-specific and effective-dated, all deferred (§3.E), but the
rule table above is designed so adding them is data plus a calculator function,
not a schema change.

### 16.7 TDS — no change

Remains an explicit input from an authorized user, with the UI saying so.
Matches brief §19. When built, it is the full subsystem or nothing.

### 16.8 Compliance claims

The product must never assert that a filing is correct. It asserts what it
calculated, under which rule version, from which inputs. Given §0.5 — partial
notification, Central Rules pending, state rules varying — any stronger claim
would be false. See §27.

---

## 17. White-collar workflow

Monthly salary resolved from the compensation version effective in the period.
Proration by **working days** (a company-calendar decision, already documented
and defensible). LOP from unpaid leave **and, newly, from unexcused absence**.

**The attendance bridge (new).** A nightly job — plus an on-demand trigger
during PREPARE — converts attendance into work facts for salaried staff:

```
for each working day in the period:
  punches exist            → fact(status=worked)
  approved leave           → fact(status=leave, paid|unpaid)
  holiday / weekly off     → no fact (calendar handles it)
  nothing at all           → fact(status=absent, approved_at=NULL)
```

The `absent` fact is **unapproved by design**. It surfaces in validation as
"3 unexplained absences — regularise or confirm as LOP", and it does not silently
cut anyone's pay. That closes §0.4 without making a missing punch instantly cost
someone money, which would be its own kind of wrong.

---

## 18. Blue-collar workflow

Already correct and should not be rebuilt. Days and hours from work facts,
converted to money by a *versioned rule*, never stored as an amount. The
payslip renders a work summary **only when work facts exist** — which
self-selects blue-collar without a single `worker_type` branch in the UI. This
is exactly what the brief's §12 asks for, and it is already how the code works.

`worker_type` exists on the employee but drives explanation, not calculation.
One engine, as required.

Add later: piece-rate, attendance-linked incentives, multi-rate shifts.

---

## 19. Contractor workflow

Implemented: contractor master, deployment via `employee.contractor_id`,
reconciliation per worker-day, record-always/approve-at-zero-variance,
`invoiced = None ≠ 0`.

**Add:**
- Authorized override with a mandatory reason, `compliance.write`-gated, written
  to financial audit. The brief's §13 asks for it and it is right — a real
  variance sometimes has a legitimate explanation, and forcing the attendance
  data to be falsified to clear it is worse than an audited override.
- Variance *reason* per line, not just per invoice.
- Evidence artifacts on the invoice (the contractor's own bill).
- Principal-employer compliance view: has the contractor filed their own PF/ESI
  for these workers? Under CLRA the principal employer carries residual
  liability. **Deferred**, but the obligation model must not assume every
  obligation is our own — add an `obligor` field now (`self` | `contractor`) so
  this is not a schema migration later.
- Contractor soft-delete route, guarded against contractors with live workers or
  approved invoices.

---

## 20. Payment workflow

Wholly new. `FINALIZED ≠ PAID` — the brief's §29 is right and the current model
does conflate them by having no payment concept at all.

```
run FINALIZED
  → payment batch created (net per employee)
  → bank validation: account present, IFSC well-formed, name match,
    amount > 0, no duplicate (employee, period)
  → file generated as an artifact (NEFT/RTGS CSV; bank-specific templates)
  → HR downloads, uploads to the bank portal, records the reference
  → status polled or entered: PAID | PARTIALLY_PAID | FAILED
  → per-item failure → retry into a new batch; payroll untouched
  → reconciliation: sum(paid items) vs run.net_total
```

Direct bank API integration is deferred. The file + recorded reference is what
Indian SMEs actually do today, and it is honest.

---

## 21. Reconciliation workflow

Three reconciliations, one shape: `expected`, `actual`, `variance`, `status`,
`reason`, `evidence`, `owner`, `resolved_at`.

| | Expected | Actual |
|---|---|---|
| Payment | run net total | sum of paid items |
| Compliance | calculated liability | challan / paid amount |
| Contractor | work facts × rate | invoice amount |

Contractor reconciliation is built and is the reference implementation for the
other two. Reuse its shape rather than inventing a second one.

---

## 22. Audit model

Today: `audit_events` with jsonb payload, actor, correlation id, source. Good
bones. Two gaps: **no before/after values**, and **no way to read it back**.

- Add `financial_audit` (§5.2) with `before`/`after` JSONB for compensation,
  payslip corrections, adjustments, obligations, payments. Separate table
  because it needs a stricter permission (`audit.read_financial`) — salary
  history is exactly the data that must not leak through a general audit view.
- Add `GET /audit` with filters: actor, entity, entity_id, action, period,
  correlation id. Searchable, per the brief's §34.
- Keep `correlation_id` — one payroll run touching 1,200 employees should be
  one traceable operation.

---

## 23. Scalability model

### 23.1 Measured baseline (not estimated)

| Employees | Wall time | Queries | Per employee |
|---:|---:|---:|---:|
| 25 | 0.41s | 361 | 14.4 q · 16.4 ms |
| 100 | 1.18s | 1,411 | 14.1 q · 11.8 ms |
| 300 | 3.35s | 4,211 | 14.0 q · 11.2 ms |

Linear, every hot column indexed. The cost is **round-trip count**, not scan
time. Against a local Postgres; over a network, per-query latency dominates
(1,000 employees ≈ 14,000 round trips ≈ +14s at 1ms RTT).

**Honest current ceiling: ~1,000 employees** before a synchronous request
becomes untenable.

### 23.2 Target architecture

```
bulk load  →  PayrollContext (in memory)  →  pure functions  →  bulk persist
```

`PayrollContext` is loaded once per run with a bounded number of queries
regardless of headcount:

```
settings, establishments, pt_slabs, pay_components, rule versions   ~6 queries
compensation versions effective in period, all employees            1
salary lines for those versions                                     1
work facts for period, all employees                                1
approved unpaid leave overlapping period                            1
existing payslips                                                   1
existing ledger inputs                                              1
calendar + holidays                                                 2
                                                             ≈ 15 total
```

Then compute in memory (already pure — `statutory.py` takes no session) and
persist with bulk upserts. **Target: ~15 queries + O(1) writes per run**, versus
14 × N today. At 1,000 employees that is 15 queries instead of 14,000.

### 23.3 Measurement plan

Test at 100 / 1,000 / 5,000 / 10,000. Record wall time, query count, peak RSS,
and worker throughput. Publish the ceiling in the docs. **Do not test 50,000**
or claim it (§0.6).

### 23.4 Explicitly not doing

Kafka, microservices, sharding, a separate payroll service, read replicas. The
modular monolith with Postgres + Redis + Celery is correct at this scale and
there is no measurement suggesting otherwise. Revisit only with data.

---

## 24. Testing strategy

373 tests today; ~233 are payroll. The valuable ones are the 21 invariants —
three real shipped bugs were caught by invariants or by new features rather than
by review (mid-month joiner LOP accumulation, a leaver who kept being paid, PT
slabs read across jurisdictions).

### 24.1 Invariants to add

```
resolve(compensation, period) is deterministic and total
re-running a FINALIZED period from stored inputs + rule versions
    reproduces it byte-for-byte
an absent day produces a finding, never a silent deduction
obligation amount == sum of payslip statutory lines for that establishment
sum(payment items) == run.net_total
a failed payment leaves the payslip untouched
artifact checksum is stable for identical input
retrying a calculation job does not double-write
a superseded job exits without mutating
```

### 24.2 Scenario tests

Mid-month joiner · mid-month exit · rehire · retro raise effective two months
back · mid-month revision · LOP spanning a month boundary · unapproved OT ·
absent with no leave · contractor variance with override · multi-state PT ·
ESI mid-period ceiling crossing · EPS ineligible by age and by 2014 rule.

### 24.3 Security tests

Cross-tenant read on every new table · manager cannot reach payroll ·
finance cannot read compensation · employee cannot see a draft payslip ·
artifact download re-checks authorization after a role change ·
every tenant table has FORCE RLS (enumerated from `pg_tables`).

### 24.4 Failure tests

Worker killed mid-calculation → run recoverable · artifact generation fails →
FAILED and retryable · payment file rejected → payroll unchanged · compliance
submission fails → SUBMISSION_FAILED, not silently complete.

Count is not the metric. Invariants and realistic scenarios are.

---

## 25. Migration plan

### 25.1 Ordering constraint

Compensation versioning must land **before** async calculation, because the
bulk-load context resolves salary by period and there is no point building it
against a model that is about to change.

### 25.2 Compensation back-fill (the risky one)

1. Create `compensation_versions` + `compensation_lines`.
2. Back-fill one open-ended version per employee from current
   `salary_components`, `effective_from = employee.joined_on` (or the earliest
   finalized payslip period, whichever is earlier), `reason = 'migration'`.
3. Dual-read behind a flag: resolve from versions, assert equality with the old
   path, log mismatches. Run for one full payroll cycle.
4. Cut over. Keep `salary_components` for one release.
5. Drop after a release with a migration asserting zero rows changed since
   cutover.

**RLS:** every step of this back-fill must suspend RLS on both tables (§14.3)
and assert row counts. A back-fill that matches zero rows and reports success is
the exact failure mode already seen once in this repository.

**Finalized payslips are never rewritten.** They already froze their own
breakdown; the back-fill reconstructs *forward-looking* salary only.

### 25.3 Async run cutover

`POST /payroll/runs` changing from 200-with-payslips to 202-with-job is
breaking. Transition: add `POST /payroll/runs?async=true` returning 202, move
the frontend, then flip the default and keep `?async=false` for one release
capped at 200 employees.

---

## 26. Rollout strategy

Phases ship independently and each is useful alone.

1. Feature-flag per company (`core/flags.py` exists).
2. Dogfood on `ccdemo` for a full cycle before any customer sees it.
3. Compensation versioning: dual-read for one cycle (§25.2).
4. Async calculation: shadow-run the new bulk path against the old one on the
   same period and assert identical payslips before switching.
5. Compliance: obligations are read-only for one cycle — generate and display,
   let HR compare against what they filed manually, then enable the workflow.
6. Payments: bank file generation before any status tracking. HR uses the file
   for a cycle first.

No phase requires a big-bang migration. Every one is reversible except the
`salary_components` drop, which is deliberately last.

---

## 27. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Statutory rules change mid-build** | High | Effective-dated rows, never rewrite history. §0.5 says this is already live |
| **Partial Labour Code notification misread** | High | Per-statute basis (§16.1); cite the notification in `source_note`; never claim certainty |
| **Compensation back-fill wrong** | High | Dual-read for a full cycle; assert equality; finalized payslips untouched |
| **RLS silently voiding a data migration** | High | Suspend RLS on every touched table; assert row counts; already burned once |
| **Async cutover changes a number** | High | Shadow-run and diff before switching |
| **Bank file format varies per bank** | Medium | Template per bank; start with one; the artifact model makes adding more data |
| **Scope creep into a compliance SaaS** | Medium | The E-list in §3 with explicit un-defer triggers |
| **PII exposure via new surfaces** | Medium | Encrypt at rest, mask in lists, `pii.read_bank`, financial audit on read |
| **Over-building Finance** | Medium | §0.2 — inputs with a typed source; only loans get a table |

**A note on compliance liability.** Applicability depends on state,
establishment, headcount, wage level, and current notifications. The product
must present *what it calculated and under which rule version*, never *this
filing is correct*. Every statutory screen carries that framing. Getting this
wrong is a legal risk, not a UX one.

---

## 28. Explicitly deferred

Full TDS subsystem · gratuity · F&F · statutory bonus · LWF · minimum-wage
compliance engine · direct EPFO/ESIC/PT portal integration · direct bank API
payments · contractor principal-employer PF/ESI verification · piece-rate ·
Meilisearch/ES · 50,000-employee tenants · Kafka/microservices/sharding ·
multi-currency · non-Indian statutory.

Each has an un-defer trigger in §3.E. Deferred means *not built and clearly
labelled as such in the UI* — never a half-built screen implying it works.

---

# IMPLEMENTATION PLAN

Ordered by dependency. Each task states why, blast radius, and rollback.
**Nothing here is started.** Phases 1–2 are the ones that change whether this is
a payroll system; 3+ are extensions.

---

## PHASE 1 — Foundations (blocks everything else)

### 1.1 Effective-dated compensation
**Why** Salary currently overwrites. Without history, arrears, retro
corrections, F&F, and honest movement are all impossible — and a raise
destroys the evidence of what was previously agreed.
**Files** new `modules/compensation/{models,service,routes,schemas}.py`;
`payroll/service.py` (resolve by period); `salary-editor.tsx`.
**DB** `compensation_versions`, `compensation_lines`; back-fill; retain
`salary_components` one release.
**API** `/compensation/**`; `PUT /payroll/employees/{id}/salary` becomes a thin
shim creating a version.
**Frontend** Salary editor gains an effective-from date and a history list.
**Tests** Resolution by period; retro correction; mid-month revision;
overlapping versions rejected; back-fill equality.
**Migration** §25.2 — dual-read a full cycle.
**Rollback** Flag back to `salary_components`; both are written during dual-read.
**Perf** One extra join, removed by the §1.3 bulk load.

### 1.2 Attendance → work facts bridge
**Why** §0.4. Today a month of no-shows is paid in full. Highest-severity
correctness defect in the system.
**Files** new `modules/attendance/bridge.py`; `payroll/readiness.py`,
`validation.py`; nightly Celery task.
**DB** Index `attendance_events(employee_id, occurred_on)`.
**API** `POST /workforce/facts/derive?period=`.
**Frontend** Readiness gains an attendance-completeness check; validation shows
unexplained absences.
**Tests** Absent day → unapproved fact → validation finding, never a silent
deduction; leave beats absence; holidays produce nothing.
**Migration** Additive.
**Rollback** Stop the job; derived facts are deletable.
**Perf** Bounded by (employees × days); bulk insert.

### 1.3 PayrollContext bulk load — kill the N+1
**Why** 14 q/employee measured. Blocks any headcount above ~1,000.
**Files** new `payroll/context.py`; rewrite `build_run`, `compute_payslip`,
`ledger.rebuild` to take a context instead of a session.
**DB** None — indexes already exist; this is round-trips, not scans.
**API** None.
**Tests** Shadow-run: old vs new path produce identical payslips for the same
period. Query-count assertion (`≤ 25` regardless of N).
**Rollback** Keep the old path behind a flag for one release.
**Perf** The point: 14N → ~15 total.

### 1.4 Async payroll calculation
**Why** Brief §40. Depends on 1.3 — making a slow thing async without making it
fast just moves the problem.
**Files** `workers/celery_app.py`; `payroll/routes.py`; `pay/page.tsx` polling.
**DB** `payroll_runs`: `job_state`, `job_id`, `progress`, `calculation_error`.
**API** `POST /payroll/runs` → 202 (§25.3).
**Tests** Retry does not double-write; superseded job exits clean; worker killed
mid-run leaves the run recoverable; advisory lock prevents concurrency.
**Rollback** `?async=false`.

### 1.5 Pagination + virtualization
**Why** No list endpoint is bounded. A 2,000-employee tenant ships a
2,000-row payload and lays out 2,000 DOM nodes.
**Files** `hr_core/routes.py`, `payroll/routes.py`, all list components.
**API** `limit`/cursor on every list; new `GET /payroll/runs/{id}/payslips`.
**Tests** Cursor stability under concurrent insert; default and max limits.
**Rollback** Defaults preserve current behaviour below the limit.

### 1.6 Artifact infrastructure + first exports
**Why** Nothing is downloadable. Prerequisite for compliance evidence and
payments.
**Files** new `core/artifacts/{models,service,jobs}.py`; a `render/` package.
**DB** `artifacts`.
**API** `/artifacts/**`.
**Frontend** A download affordance on the run; an artifacts list.
**Deliverables** Payroll register (CSV), payroll summary (XLSX), single payslip
PDF (sync), bulk payslips (async).
**Tests** Checksum stability; FAILED is retryable; authorization re-checked at
download; expiry sweep.

### 1.7 Bank details + PAN + designation
**Why** Blocks readiness checks and all of payments.
**DB** `employee_bank_accounts`; `employees.pan`, `.designation`.
**Security** Encrypted at rest, masked in lists, `pii.read_bank`, financial
audit on full read.
**Tests** Masking; permission gate; audit entry written.

---

## PHASE 2 — Compliance

### 2.1 Obligation engine
**Why** Brief §20–21. The gap between a calculator and a system.
**Files** new `modules/compliance/**`.
**DB** `compliance_obligations` (no `overdue` column), `compliance_evidence`.
**API** `/compliance/obligations/**`.
**Tests** Emission on finalize; applicability → NOT_APPLICABLE; state machine
legality; obligation total == sum of payslip statutory lines.
**Rollout** Read-only for one cycle (§26.5).

### 2.2 Compliance calendar
Due dates derived from `(jurisdiction, statute, obligation_type, period)`, never
hardcoded in a component. Filters: statute, establishment, month, status, owner.
`OVERDUE` derived in the query.

### 2.3 Statutory registers as artifacts
PF contribution register, ESI register, PT register per establishment. ECR-format
export where the layout is stable. Reconciliation report per statute.

### 2.4 Compliance Control Center
Compact operational rows per brief §28 — not KPI cards. Reuses the existing
severity-rail + word pattern, monochrome, money on the fixed axis.

### 2.5 Financial audit + audit query API
`financial_audit` with before/after; `GET /audit` with filters;
`audit.read_financial` gated separately.

---

## PHASE 3 — Payments

3.1 Bank validation · 3.2 Payment batch + file artifact · 3.3 Payment
lifecycle and per-item retry · 3.4 Payment reconciliation against run net.
Constraint throughout: **a payment failure never mutates payroll.**

---

## PHASE 4 — Search and reporting

4.1 Postgres FTS + trigram entity lookup, wired into `⌘K` ·
4.2 Filtered record search (payslips, obligations, adjustments, artifacts) ·
4.3 The operational report set from brief §47, all async above a threshold.

---

## PHASE 5 — Statutory breadth

LWF · minimum-wage compliance engine · statutory bonus · gratuity · F&F ·
arrears. Each is a rule row plus a calculator function on the §16.2
architecture — no schema change.

---

## PHASE 6 — TDS

Only as a complete subsystem: regime election, declarations, proofs, previous
employment, projection, monthly computation, 24Q, Form 16. Until then TDS stays
an explicit authorized input, labelled as such.

---

## PHASE 7 — Lumo payroll intelligence

Explain a payslip, summarise movement, surface anomalies, find records, explain
compliance status. **Never** calculates, approves, finalizes, or submits. Facts
from tools only — the existing grounding contract and its test extend
unchanged.

---

## Definition of done, honestly assessed

Against the brief's §51, today: 11 of 29 criteria are met. Phase 1 takes it to
~18. Phase 2 to ~24. The remainder are Phases 3–6.

The criterion that matters most is not on that list: **a payroll professional
should be able to answer "why is this number what it is?" without opening
Excel.** The provenance chain already makes that answerable in the data. What is
missing is the UI that exposes it (brief §9) and the export that lets them prove
it to someone else.

---

**Sources for §0.5:**
[Code on Wages, 2019 (as on 21 Nov 2025) — India Code](https://www.indiacode.nic.in/bitstream/123456789/15793/1/aA2019-29.pdf) ·
[Implementation of labour codes — KPMG India](https://kpmg.com/in/en/blogs/2025/12/implementation-of-labour-codes-what-changes-and-road-ahead.html) ·
[Key Highlights of Labour Codes — Lexology](https://www.lexology.com/library/detail.aspx?g=487a903d-0a7d-458c-8a7f-bb85c22d7b7b)
