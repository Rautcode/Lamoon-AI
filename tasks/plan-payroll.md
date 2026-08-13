# Implementation Plan: Indian Payroll & Compliance — full lifecycle

**Status:** Plan only. No code written for anything below.
**Written:** 13 August 2026, against `main` @ `066e279` (425 tests passing).
**Revised:** same day, after domain research, competitive research, and a
review of what HR teams still do by hand *after* buying an HRMS. Those three
threads added a **Phase 0** and changed the order of everything after it.

**Companions:** `PAYROLL-COMPLIANCE-ARCHITECTURE.md` (system architecture),
`PAYROLL-UX-SPEC.md` (information architecture and screen flow),
`market-position.md` (competitive evidence and sources).

---

## 0. Strategy — what this is, and what it is not

The governing insight in the domain brief is correct:

> **Payroll calculation is only one stage of payroll.** Most HRMS products make
> the calculation stage look like the whole product.

But the research added a second insight that outranks it in sequencing:

> **The engine being correct is worthless if nobody can reach it.**

Both now shape the plan. Phase 0 exists because of the second.

### 0.1 Who this is for

**Not "Indian SMEs".** That segment has a free competitor — greytHR is free to
25 employees, precisely the band ADR-0001 targets — and "nicer UX" does not
beat free-and-compliant.

**The segment:** *multi-establishment and contractor-heavy employers, roughly
50–500 people, where payroll errors have a measurable rupee cost.* They have
month-end exception loads that Excel handles badly, contractor invoices nobody
verifies against attendance, and multi-state statutory complexity. They already
pay ₹150–400 per employee/month for outsourcing because payroll is **risky**,
not because it is hard.

### 0.2 Non-goals, stated so they stop costing us

- **Not an HCM suite.** Competitors carry performance, onboarding, documents,
  expense, helpdesk, assets, engagement, LMS and marketplaces. We will not
  out-module a ten-year suite. Feature-comparison sheets are a losing surface.
- **Not auto-filing.** RazorpayX auto-pays and auto-files TDS/PF/ESI/PT because
  it is a payments company with money-movement rails. That is a licence
  advantage, not a software one. Any roadmap language implying "we file for
  you" is a promise this product cannot keep.
- **Not TDS parity.** Razorpay auto-files it at ₹30–100 per employee/month.
  Building a full TDS subsystem to reach parity is the worst
  return-on-effort item available. TDS stays an explicit labelled input until a
  customer's CA refuses it.

### 0.3 What we are betting on

Three things, in order of how defensible they are:

1. **Provenance.** Every figure knows its source, its approver and the rule
   version that produced it. Competitor engines compute from a live salary
   field and cannot reconstruct *why* after the fact. This is a **data-model**
   advantage, not a feature, which is what makes it hard to copy.
2. **Exceptions driven to closure**, not merely recorded.
3. **Contractor attendance-vs-invoice variance.** Built, and unmarketed by
   anyone else in this market.

**The bet is unproven.** It has never been tested on a customer. See §Open
questions.

---

## 1. Design assessment — where Lamoon wins and where it blocks us

### Wins, and worth defending

Exception-first rather than dashboard-first; severity as a rail **plus a word**
(survives greyscale and colour-blindness); money on one axis in tabular
numerals; frozen state reads square; provenance to four levels; nav derived
from permissions; Lumo constrained so facts come only from tools. Every
competitor screenshot in the research is a dense blue dashboard. Not looking
like them is an asset.

### Liabilities, and they are severe

| Liability | Consequence |
|---|---|
| **Desktop-only** | For frontline and low-signal units, offline sync and local language are required *or attendance simply fails*. Competitors ship three parallel punch modes — biometric, geo-fenced mobile, kiosk — "so no worker is locked out" |
| **English-only** | Vernacular payslips and helpdesk (Hindi, Tamil, Telugu, Marathi) are a stated requirement for blue-collar, not a nicety |
| **Minimalism reads as "not enterprise"** | Buyers equate visible module density with capability. We optimised for the operator on day 30, not the buyer on day 0 |
| **Employees never touch the product** | No adoption pull from below, which is how HRMS spreads inside a company |

**The contradiction to resolve:** we chose blue-collar as the differentiator and
built a product blue-collar workers cannot physically use. Phase 0 exists to
fix that before the engine work continues.

---

## 2. What already exists

| Capability | State |
|---|---|
| Statutory engine — EPF/EPS/EDLI/admin, ESI, PT | Built, tested, period-resolved rule versions |
| Code on Wages 50% wage basis | Built, one global effective date (wrong — see 1.1) |
| Effective-dated compensation | Built (`0017`) — **parity with Zoho/Keka, not advantage** |
| Work facts, input ledger with provenance | Built |
| Establishments, per-jurisdiction PT slabs | Built |
| Readiness / validation / risk / movement | Built, four separate surfaces |
| Immutable finalized runs + adjustments | Built |
| Contractor reconciliation | Built, and unique in this market |
| Day-state vocabulary (calendar-aware attendance) | Built |
| Artifacts + BlobStore (S3/local/Drive per purpose) | Built (`0018`), one consumer |
| RLS, tenant isolation, permission-derived nav | Built |

**Not built:** everything in Phases 0 and 3–8, plus attendance bridge,
PayrollContext, async runs, bank details/PAN, search, audit query, proration
policy.

---

## 3. Corrections carried forward

**Confirmed, and I would have got it wrong:** the quarterly salary TDS statement
**is** now **Form 138** (formerly 24Q) under the Income-tax Act 2025, effective
1 April 2026, due 31 Jul / 31 Oct / 31 Jan / 31 May.

**Confirmed:** Code on Wages s.17 — monthly wages before the 7th of the
succeeding month, dues on separation within two working days, **uniform across
establishment sizes**. The 7th/10th split by headcount was the *repealed*
Payment of Wages Act 1936 s.5.

**Corrected — arrears and mid-month revision are NOT differentiators.** Zoho
auto-calculates arrears and pays them in the payout month; Keka flows a CTC
revision into arrears and PF ceiling checks and splits the month at the
effective date. We reached parity, not advantage.

**Corrected — F&F is a gap in OUR product, not the market.** Zoho has
termination, final settlement, bulk exit import and an F&F report.

**Corrected — `artifacts.expires_at` is backwards for statutory evidence.**
Inspection has moved to risk-based digital scrutiny driven by payroll data and
filings, and manual registers are increasingly rejected. Evidence needs a
retention *floor*, not an expiry. Fixed in Task 4.6.

**One correction to the brief.** Gratuity's wage basis is listed as changing
"from 21 November 2025" alongside everything else. Only *some* provisions were
notified then, with Central Rules and separate state rules following. A single
global `LABOUR_CODE_START` is therefore wrong **in principle** — which is why
Task 1.1 makes the basis resolve per `(statute, jurisdiction, period)`.

**One disagreement on sequencing.** The brief puts minimum wage early. It sits
in Phase 5. A real engine needs state × zone × scheduled employment × skill ×
effective date — a large reference-data problem with no authoritative
machine-readable source, which rots between customers. The *check* against a
per-establishment configured floor ships in Phase 2 (Task 2.6): same protective
value, a fraction of the data.

**One thing the brief under-weights.** "Does every rupee have a destination?" is
presented as late-stage reconciliation. It is an **invariant that holds from
Phase 1** and is tested continuously. Every phase carries its reconciliation
invariant as an acceptance criterion.

---

## 4. Architecture decisions

1. **One `PayrollContext`, many rule implementations.** Each statutory scheme is
   a pure calculator over one authoritative context loaded once per run.
2. **Wage basis resolves per statute** — `basis_for(statute, jurisdiction,
   period, lines)`.
3. **Rules are effective-dated data**, with `source_note` citing the
   notification. A rate change is a migration, not a deploy.
4. **Proration policy is configuration** — working days / calendar / fixed-30 /
   attendance, per company, effective-dated.
5. **Obligation is one generic model**, with `obligor` (self | contractor) from
   the start.
6. **`OVERDUE` is derived, never stored.**
7. **Compliance never reads a draft run.**
8. **Artifacts are immutable, versioned, and retained** — never silently replaced.
9. **Complexity lives in the engine.** No screen shows `PayrollContext`, a rule
   version by default, or a uuid.

---

## 5. Dependency graph

```
  [0.1] migration suite ──────────┐
  [0.3] mobile PWA ── [0.4] vernacular
                                  │
        [1.1] per-statute wage basis
                  │
        [1.2] rules as data
                  │
   ┌──────────────┼───────────────┬──────────────┐
   │              │               │              │
[1.3] PayrollContext      [1.4] proration   [2.x] statutory identity
   │                                              + contractor depth
   ├──── [0.2] parallel-run diff                  │
   │                                              │
[1.5] bulk persist ── [1.6] attendance bridge ── [2.9] device ingest
   │
[1.7] async runs ── [1.8] approval
   │
   ├── [3.x] payments ── [3.5] reconciliation surface
   └── [4.x] compliance ── [4.7] rejection ingestion
                  │
        [5.x] statutory breadth ── [6.x] F&F ── [7.x] TDS ── [8.x] search
```

---

## PHASE 0 — Adoption blockers

**Not features. The conditions under which anybody can use the product.** Every
one was found by research rather than architecture. **Phase 1 is worthless if
these are unresolved** — the engine would be correct and unreachable.

### Task 0.1: Migration suite — get a customer's Excel folder in

**Description:** Import employee master, salary history, opening balances and
attendance from spreadsheets, with validation on the way in. Reportedly ~80% of
Indian HRMS implementations fail, and the named causes are **dirty master data**,
state PT gaps and no internal owner — *not the software*. Most Indian SMEs are
not migrating from another HRMS; they are migrating from a folder of Excel files
maintained by whoever owned payroll that year.

**Acceptance criteria:**
- [ ] Employee master, compensation history, opening balances, attendance
- [ ] **Dry run** shows exactly what would land, before anything is written
- [ ] Per-row rejection with a reason; **never a partial load**
- [ ] Re-importing the same file is idempotent
- [ ] Validation reuses Task 2.7's rules, so bad data is caught at the door

**Verification:**
- [ ] New: a deliberately dirty sheet (duplicate PANs, malformed IFSC, missing
      joining dates, name/Aadhaar mismatch) produces a per-row report and loads nothing
- [ ] New: dry run and real run agree exactly
- [ ] Manual: load a 200-employee synthetic file end to end

**Dependencies:** none (validation rules shared with 2.7)
**Files:** `core/import/` (new), `hr_core/`, `compensation/`, `tests/test_migration.py`
**Scope:** L → split by entity

---

### Task 0.2: Parallel run and explained diff

**Description:** Run a period in Lamoon alongside the incumbent, import the
incumbent's register, diff per employee per component, and **explain every
difference** using the provenance chain we already store.

**This is the strongest sales asset in the product.** Parallel run is a standard
phase of every Indian HRMS implementation, and we are the only product that can
say *why* a number differs rather than only that it does — because competitor
engines compute from a live salary field and cannot reconstruct the reason. It
converts the architecture advantage into a switching argument, and de-risks the
phase where implementations fail.

**Acceptance criteria:**
- [ ] Import a register in a common shape; map columns to components
- [ ] Diff by employee × component, with tolerance for rounding
- [ ] Each difference is attributed — different salary version, different LOP,
      different wage basis, different rule version, missing input
- [ ] Differences are exportable as an artifact for the customer's sign-off

**Verification:**
- [ ] New: seed a known difference of each class and assert the attribution
- [ ] Manual: parallel-run `ccdemo` against its own prior register — zero diffs

**Dependencies:** 1.3
**Files:** `payroll/parallel.py` (new), `payroll/render.py`, `tests/test_parallel_run.py`
**Scope:** M

---

### Task 0.3: Mobile ESS and punch (PWA, not native)

**Description:** Punch, payslip, leave and attendance on a phone. Installable,
offline-tolerant, low-bandwidth.

**The blue-collar bet is unreachable without this.** For frontline and low-signal
units, offline sync and local language are required *or attendance simply fails*.
Competitors ship three parallel punch modes — biometric, geo-fenced mobile,
kiosk — explicitly so no worker is locked out; one rival's app alone has 1M+
installs. A PWA clears the bar; native apps are not required.

**Acceptance criteria:**
- [ ] Punch in/out works on a phone, including offline with later sync
- [ ] Payslip and leave balance readable on a small screen
- [ ] Installable; usable on a slow connection
- [ ] Offline punches reconcile without creating duplicates

**Verification:**
- [ ] Manual: throttled network and offline mode in the browser pane
- [ ] New: duplicate-punch suppression on re-sync
- [ ] Responsive check at 360px

**Dependencies:** none
**Files:** `apps/web/app/(mobile)/`, service worker, `ess/routes.py`
**Scope:** L

---

### Task 0.4: Vernacular payslip and ESS

**Description:** Hindi first, then Tamil, Telugu, Marathi. Payslip and ESS
strings. A stated requirement for frontline workforces, not a nicety.

**Acceptance criteria:**
- [ ] Language is a per-employee preference, not a browser guess
- [ ] Payslip renders correctly in each script, including in the PDF
- [ ] Untranslated strings fall back to English visibly, never blank

**Verification:**
- [ ] New: a payslip renders in each language with correct numerals and money
- [ ] Manual: ESS in Hindi end to end

**Dependencies:** 0.3
**Files:** `apps/web/lib/i18n/`, `payroll/render.py`
**Scope:** M

---

### Task 0.5: Notifications and a task inbox

**Description:** `modules/notifications/` is an **empty `__init__.py`** — no code
at all. `core/notify/base.py` is a 58-line seam, and Celery beat runs two ATS
jobs. There is no way to tell a person that something needs them.

**This is a plan bug, not a wish.** Task 1.6 requires exceptions to be *routed
to the manager who can close them, to age visibly, and to escalate to HR before
the run*. None of that is possible today. Half of Phase 0's value and most of
1.6's disappear without it.

**Acceptance criteria:**
- [ ] An in-app inbox: what needs *me*, ordered by what blocks payroll soonest
- [ ] Email via the existing `Notifier` seam; digest rather than one mail per item
- [ ] Reminder and escalation schedules driven by data, not hardcoded
- [ ] Read/actioned state per person, so a resolved item leaves every inbox
- [ ] Nothing notifies about payroll amounts to someone without `payroll.read`

**Verification:**
- [ ] New: an unresolved attendance exception reaches the reporting manager,
      then HR on escalation, and disappears from both when resolved
- [ ] New: a manager's digest contains no salary figures

**Dependencies:** none · **Files:** `modules/notifications/`, `core/notify/`,
`workers/celery_app.py`, `apps/web/components/lamoon/inbox.tsx` · **Scope:** L

---

### Task 0.6: A Settings section, and the operations/configuration split

**Description:** `PAYROLL-UX-SPEC.md` §3.2 requires payroll configuration to
leave the monthly workflow — and **there is no Settings route in the product.**
Nine workspace routes exist and none of them is settings; payroll config lives
inside `/pay`, which is the single largest IA error in the current build.

**Acceptance criteria:**
- [ ] `/settings` with payroll (components, statutory, PT slabs, establishments,
      pay calendar), work calendar, leave types, roles
- [ ] `/pay` carries operations only
- [ ] **Findings deep-link into the exact setting** with jurisdiction
      pre-selected — HR never hunts for settings; settings come to HR
- [ ] Permission-derived, like the rest of the nav

**Verification:**
- [ ] Manual: a "PT slabs not configured for Maharashtra" finding opens the
      right screen with the state selected
- [ ] `/pay` no longer renders configuration

**Dependencies:** none · **Scope:** M

---

### ✅ Checkpoint: Phase 0
- [ ] A real company's Excel folder loads, validates and parallel-runs
- [ ] A worker with a phone and patchy signal can punch and read a payslip
- [ ] An exception reaches the person who can close it, and escalates if it doesn't
- [ ] Configuration has left the monthly workflow
- [ ] **Only then does engine depth start paying off**
- [ ] **Human review before Phase 1**

---

## PHASE 1 — Engine foundations

### Task 1.1: Wage basis resolves per statute and jurisdiction
Replace `rules.statutory_wage()` and the global `LABOUR_CODE_START` with
`basis_for(statute, *, jurisdiction, period, lines)`.
**AC:** EPF and ESI can return different bases for the same employee/period ·
jurisdiction fallback recorded, not silent · existing payslips recompute identically.
**Verify:** 27 wage-definition tests pass unchanged · `ccdemo` shadow-run byte-identical.
**Deps:** none · **Files:** `payroll/rules.py`, `statutory.py`, `service.py` · **M**

### Task 1.2: Statutory rules become effective-dated data
`(statute, jurisdiction, effective_from, effective_to, version, params JSONB,
source_note)`, Python values as seeded fallback.
**AC:** rate change is a migration not a deploy · payslip stamps the version ·
missing rule raises a named error, never a silent zero.
**Verify:** future-dated fictional rate applies only to its period · upgrade/downgrade clean.
**Deps:** 1.1 · **M**

### Task 1.3: PayrollContext — bulk load, pure compute
One context per run; calculation becomes pure functions over it.
**AC:** ~15 queries per run regardless of headcount · `compute_payslip` takes a
context not a `Session` · payslips byte-identical.
**Verify:** query-count assertion ≤ 25 at N = 1/100/1000 · shadow-run diff ·
re-benchmark against measured baseline (300 → 3.35s / 4,211 queries).
**Deps:** 1.1, 1.2 · **L — split if it exceeds one session**

### Task 1.4: Proration policy becomes configuration
`working_days` (default) | `calendar_days` | `fixed_30` | `attendance_days`.
**AC:** default reproduces today exactly · each basis pure and separately tested ·
basis named on the payslip.
**Verify:** ₹30,000 / 2 LOP days → four documented figures.
**Deps:** none · **S**

### Task 1.5: Bulk persistence
**AC:** one run = one transaction · `Σ payslips = run totals`.
**Verify:** benchmarks at 100/1,000/5,000/10,000 · killed mid-write leaves no partial run.
**Deps:** 1.3 · **M**

### Task 1.6: Attendance → work facts bridge, with **owned** exceptions
Present / paid leave / unpaid leave / holiday / weekly-off mapped; **nothing at
all → an unapproved `absent` fact and an exception, never an automatic deduction.**
**AC:** no path from missing punch to LOP without a human · idempotent · derived
facts distinguishable by `source` · **each finding routed to the manager who can
close it**, ageing visibly, escalating to HR *before* the run rather than at it.
**Why the routing matters:** ~5–8% of daily attendance records need
regularisation, and the reason twenty land the night before payroll is that
nothing drives them to closure. Recording the exception is not the feature.
**Verify:** a month of no punches → N findings, ₹0 deducted.
**Deps:** 1.3 · **M**

### Task 1.7: Async payroll runs
202 + job handle; `status` (business) and `job_state` (job) separate. Adopts the
existing Platform §8 contract — 3× backoff, idempotency keys, `failed_tasks`.
**AC:** advisory lock prevents concurrent calculation · superseded job exits
cleanly · worker killed mid-run leaves the run recoverable.
**Deps:** 1.3, 1.5 · **M**

### Task 1.8: Run approval step
`DRAFT → CALCULATED → PENDING_APPROVAL → APPROVED → FINALIZED`, optional
segregation of duties.
**AC:** illegal transitions refused with a named reason · input change
invalidates approval · `FINALIZED` terminal.
**Deps:** 1.7 · **S**

### ✅ Checkpoint: Phase 1
- [ ] Full suite green; ruff, mypy, tsc, eslint, next build clean
- [ ] Query count bounded and asserted
- [ ] Benchmarks published at 100 / 1,000 / 5,000 / 10,000
- [ ] `ccdemo` shadow-run byte-identical
- [ ] **Human review before Phase 2**

---

## PHASE 2 — Employment master, identity and contractor depth

### Task 2.1: Bank details and PAN · M · deps: none
Encrypted at rest, masked in lists, `pii.read_bank` gate, financial-audit
written **on read**. Blocks all of Phase 3.

### Task 2.2: Employment attributes · S · deps: none
Designation, employment type, gender, exit date, location.

### Task 2.3: ESI IP number and per-scheme eligibility flags · S · deps: 2.2
Applicability decided by data, never inferred.

### Task 2.4: Readiness gains the new checks · S · deps: 2.1, 2.3, 1.6
Each finding links to where it is actually fixed.

### Task 2.5: Loans and advances ledger · M · deps: none
Balance-bearing table; recovery posts as `source="loan"` with a `source_ref`,
so "why was ₹5,000 deducted?" is answerable from the payslip.

### Task 2.6: Minimum-wage floor check · S · deps: 1.1
Per-establishment configured floor vs qualifying wages, as a validation finding.

### Task 2.7: Statutory identity **validation** — catch a rejection before filing · M · deps: 2.1, 2.3
Readiness checks identity is *present*. Present is not *valid*, and the gap is
one of the most reliable time sinks in Indian payroll: EPFO rejects on a name
differing from Aadhaar by a single letter, an initial versus an expansion, a
middle name on one side only, or KYC not seeded against the UAN. The ECR fails,
HR finds out days later at the portal, and the fix loops employee → employer →
portal.
**AC:** UAN/PAN/IFSC formats checked on save · likely Aadhaar-name mismatches
surfaced as **findings, not hard blocks** (we do not hold Aadhaar and must not
pretend to adjudicate) · `kyc_seeded` and `esi_ip_number` tracked with a date ·
pre-filing check names everyone an ECR would reject · **a KYC problem never
blocks somebody being paid.**
**Note:** this is also a *migration* feature — dirty master data is the
number-one documented cause of implementation failure, so 0.1 shares its rules.

### Task 2.8: Contractor reconciliation depth · M · deps: none
*Promoted from Phase 5.* Per-line variance reasons; authorised override with
mandatory reason written to financial audit; the contractor's own bill attached
as evidence; CLRA principal-employer register; guarded soft-delete.
**Why:** the one thing no competitor markets, and the clearest ROI story we have.

### Task 2.9: Attendance ingest — biometric / device / bulk import · M · deps: 1.6
CSV/Excel import of device logs with a per-vendor mapping profile; idempotent by
(employee, timestamp, device); unmatched employee codes surface as findings,
never silently dropped.
**Blocking risk, not a nice-to-have.** The blue-collar bet assumes attendance
data exists. We have a punch API and nothing that ingests from the biometric
readers a factory or site actually runs; greytHR is explicitly described as
deeper on biometric hardware, which is years of device integrations. Without at
least a bulk import path the attendance bridge has no data to bridge.

### ✅ Checkpoint: Phase 2
- [ ] Incomplete statutory identity cannot be paid without an audited override
- [ ] PII masking verified by test, including after a role change
- [ ] A filing rejection is predicted before filing, not discovered after

---

## PHASE 3 — Payment and reconciliation

### Task 3.1: Bank validation · S · deps: 2.1, 1.8
Account present, IFSC well-formed, name match, amount > 0, no duplicate
(employee, period).

### Task 3.2: Payment batch and bank file · M · deps: 3.1
File as an artifact; one bank template to start. Surfaces the Code on Wages s.17
deadline — **7th of the succeeding month, uniform** — and two working days on
separation. *Blocked on open question 4.*

### Task 3.3: Payment lifecycle and per-item retry · M · deps: 3.2
`READY → VALIDATED → FILE_GENERATED → SUBMITTED → PROCESSING → PAID |
PARTIALLY_PAID | FAILED → RECONCILED`. **A payment failure never mutates payroll.**

### Task 3.4: Payment reconciliation · S · deps: 3.3
`Σ paid items = run.net_total`; variance named and owned.

### Task 3.5: Reconciliation as a user-visible surface · M · deps: 3.4
*Moved forward from Phase 5.* "Does every rupee have a destination?" answered on
screen — payroll vs bank vs statutory — because this is the validated
differentiator, not a late report.

### ✅ Checkpoint: Phase 3
- [ ] Invariant: a failed payment leaves the payslip byte-identical
- [ ] `FINALIZED ≠ PAID` visible in the UI, not only in the model

---

## PHASE 4 — Compliance operations

### Task 4.1: Obligation engine · L → split · deps: 1.8
Generic model with `obligor`; emitted on finalization; one per (establishment,
statute, period). **Invariant: obligation amount = Σ payslip statutory lines.**

### Task 4.2: Compliance calendar · M · deps: 4.1
Due dates derived from `(jurisdiction, statute, obligation_type, period)` — EPF
15th, ESI per rules, PT state-specific, TDS deposit 7th (March exception),
**Form 138** quarterly 31 Jul / 31 Oct / 31 Jan / 31 May. Never hardcoded in a
component. `OVERDUE` derived.

### Task 4.3: EPF chain · L → split · deps: 4.1
ECR artifact → submission → challan/TRRN → payment → reconciliation. PF
*calculated*, *filed* and *paid* are three distinct states.

### Task 4.4: ESI chain · M · deps: 4.1
### Task 4.5: PT chain, jurisdiction-aware · M · deps: 4.1

### Task 4.6: Evidence, **retention**, and download-all · M · deps: 4.3–4.5
Register, return, acknowledgement, challan, receipt, reconciliation; audit-ready
in one click.
**Fixes something already built:** `artifacts.expires_at` plus the `expired`
sweep is backwards for statutory evidence. Inspection has moved to risk-based
digital scrutiny driven by payroll data and filings, and manual registers are
increasingly rejected. Add `retain_until`, refuse deletion before it, and let
`expires_at` govern only convenience artifacts such as an ad-hoc bulk download.

### Task 4.7: Return rejection ingestion · M · deps: 4.3, 2.7
EPFO and ESIC reject uploads and hand back an error list, which today becomes a
spreadsheet somebody works through by hand. Parse it, map each row back to the
employee, raise resolvable exceptions, and hold the obligation at
`SUBMISSION_FAILED` **with the reasons attached** rather than a bare status.

### ✅ Checkpoint: Phase 4
- [ ] Read-only for one full cycle on `ccdemo` before enabling the workflow
- [ ] Submission failure sets `SUBMISSION_FAILED`, never silent success
- [ ] The product asserts *what it calculated and under which rule version* —
      never *this filing is correct*

---

## PHASES 5–8 — epics, decomposed when reached

| # | Epic | Deps | Scope | Note |
|---|---|---|---|---|
| 5.1 | Arrears generation from retro compensation | 1.1 | M | **Parity** — Zoho and Keka both do this |
| 5.2 | Reimbursements | 2.5 | M | Typed ledger inputs, not a Finance module |
| 5.3 | LWF, jurisdiction-specific | 1.2 | M | |
| 5.4 | Statutory bonus | 1.1, 1.2 | M | Current Code rules, not the historical 8.33/20 description |
| 5.5 | Gratuity | 1.1, 1.2 | M | Revised wage basis; **not** CTC × 4.81% |
| 5.6 | Full minimum-wage engine | 2.6 | L | Reference-data heavy |
| 6.1 | F&F settlement workflow | 5.1, 5.5 | L | **Catching up** — Zoho has termination, final settlement, bulk exit import, F&F report |
| 7.1 | TDS subsystem | 1.3 | XL → many | All or nothing. **The proof workflow is the bulk, not the tax maths** — collect → verify → reject → re-collect → re-verify → recompute, in a Jan–Mar burst where over 65% of employees reportedly suffer higher TDS from late or wrong proofs. Size it as a workflow product with a calculator inside |
| 7.2 | Form 138 + Form 16 | 7.1, 4.1 | L | FY aggregation; payroll must survive the monthly run |
| 8.1 | Search — Postgres FTS + trigram | 1.3 | M | ADR-0007, no new infra |
| 8.2 | Audit query API + financial before/after | — | M | Platform §10 implementation lag |
| 8.3 | Operational report set | 8.1, 1.6 | M | Async above a threshold |

---

## HRMS module debt — tracked, deliberately not planned

**Answering "is the whole HRMS improvement in this plan?" honestly: no.** This
is a payroll and compliance plan. Below is what that leaves untouched, recorded
so it is *visible* rather than *forgotten* — the difference between a decision
and an oversight.

### Half-built modules that already exist

| Module | State | Why it is not planned |
|---|---|---|
| **ATS** | Basic pipeline, no drag-to-move, no offers, no letters | Not on the payroll path. Keka is strong here; we will not win it |
| **Leave** | No half-days, comp-off, encashment, or leave-year rollover | Encashment arrives with F&F (6.1). Half-days are a real gap for the blue-collar segment — **promote if a customer needs it** |
| **Attendance** | No shift master, no OT policy, **no regularisation workflow** | Regularisation is effectively delivered by 1.6 + 0.5; the shift master is deferred |
| **Employee profile** | No documents, letters, or asset tracking | Suite features. Excluded per §0.2 |
| **Org** | Basic tree | Sufficient |
| **Audit** | Records events, no UI, no before/after | Planned as 8.2 |
| **Search** | `raise NotImplementedError` | Planned as 8.1, payroll-scoped only |

### Design work outside payroll

`PAYROLL-UX-SPEC.md` covers **payroll screens only**. The other eight routes —
`/home`, `/people`, `/people/[id]`, `/hiring`, `/time`, `/attendance`, `/org`,
`/me` — have no design plan beyond what is already built. The Lamoon design
system itself (`DESIGN.md`) is documented and implemented; what is missing is a
per-screen treatment for the non-payroll surfaces.

**Recommendation: leave it.** Redesigning `/hiring` does not move the segment
in §0.1, and design attention is better spent on the exceptions surface, the
mobile shell (0.3) and the settings split (0.6) — all of which are on the
payroll path and all of which are planned.

### The three modules that would change this answer

If the segment bet in §0.1 is wrong and we end up selling to general SMEs, the
missing pieces become **onboarding workflows, documents/letters, and
performance** — in that order. That is a different product and a different
plan. Do not drift into it one feature at a time.

---

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **The chosen segment cannot reach the product** (no mobile, vernacular, offline, device ingest) | **Critical** | Phase 0 before Phase 1 |
| **No path from a customer's Excel folder in** — ~80% of implementations fail on dirty data | **Critical** | Task 0.1 + 2.7 sharing validation rules |
| **The bet is untested on a customer** | **Critical** | Three contractor-heavy employers before Phase 2 |
| Partial Labour Code notification misread | High | Per-statute basis (1.1); `source_note` cites notifications; claim no certainty |
| Rules change mid-build | High | Effective-dated rows (1.2); never rewrite history |
| PayrollContext refactor changes a number | High | Shadow-run and diff; byte-identical acceptance criterion |
| RLS silently voiding a data migration | High | Suspend RLS on every touched table; assert row counts. Burned twice |
| Support latency is the top switching reason, and we are small | High | Provenance UI so anomalies are self-explaining; a ticket avoided is a ticket answered |
| TDS half-built | High | 7.1 all-or-nothing; explicit labelled input until then |
| Competing on HCM breadth | High | §0.2 non-goals; do not enter feature-comparison sheets |
| Minimum-wage reference data rots | Medium | Configured floor first (2.6) |
| Compliance liability overclaimed | High | Never assert a filing is correct — only what was calculated, under which rule |
| S3 unverified against a live endpoint | Medium | MinIO in CI before production depends on S3 |

---

## Open questions (need a human)

1. **Customer validation before Phase 2** — will three contractor-heavy
   employers pay for attendance-vs-invoice variance, or does their contractor
   already absorb that problem inside a services contract? *This answer is worth
   more than three months of building.*
2. **Phase 0 scope** — is a PWA acceptable, or does the segment expect a
   Play Store app?
3. **MinIO in CI** to verify `S3BlobStore` — now, or on first real S3 use?
4. **`btree_gist`** superuser `CREATE EXTENSION` for a true no-overlap
   constraint on compensation versions — run it, or stay with service-level
   enforcement plus the invariant test?
5. **Segregation-of-duties default** (1.8) — off for small SMEs where it
   deadlocks, on above ~50 employees, or always off until asked?
6. **First bank template** (3.2) — which bank do the earliest customers use?
7. **Phase 4 before Phase 3?** Compliance is the bigger differentiator; payments
   are the more immediate pain. The graph allows either after Phase 1.
