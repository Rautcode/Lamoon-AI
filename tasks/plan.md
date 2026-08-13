# Lamoon HR — HRMS Implementation Plan

**We are building an HRMS.** Payroll is its deepest pillar, not the product.

**Status:** Plan only. No code written for anything below.
**Written:** 13 August 2026, against `main` @ `066e279` (425 tests passing).

**This file is the master.** The payroll and compliance detail — eight phases,
per-task acceptance criteria and verification — lives in
[`plan-payroll.md`](plan-payroll.md) and is referenced here as Track D rather
than repeated. Companions: `PAYROLL-COMPLIANCE-ARCHITECTURE.md` (system
architecture), `PAYROLL-UX-SPEC.md` (payroll screens),
`market-position.md` (competitive evidence).

---

## 0. What we are building

A full HRMS for the Indian market: employee lifecycle, time and attendance,
leave, payroll, statutory compliance, and the talent and service workflows
around them.

**Earlier drafts of this plan scoped HCM breadth out.** That was a
recommendation, not a decision, and the decision is to build the HRMS. This
plan is rewritten accordingly. The reasoning that produced the narrower
recommendation is preserved in `market-position.md` — it remains useful as a
description of where competition is hardest, not as a limit on scope.

**One thing carries forward unchanged from that research**, because it is a
fact about the market rather than an opinion about scope:

> Payroll calculation is table stakes. Every competitor computes PF/ESI/PT
> correctly. What HR teams still do in Excel — after buying an HRMS — is
> **reconciliation and exception handling** across data the tools never
> properly joined.

So breadth is the scope, and **the joins between modules are where the product
wins or loses.** An HRMS whose attendance, leave, compensation and payroll do
not truly meet is what every incumbent already ships.

### Principles that survive the scope change

1. **Depth at the joins, not just modules on a menu.** Attendance → leave →
   payroll → compliance must be one chain with provenance, not four products
   sharing a login.
2. **Exceptions are driven to closure**, never merely recorded.
3. **Effective-dated everything** that has a history — compensation, statutory
   rules, policies, org structure.
4. **Complexity in the engine, calm on the screen.** Monochrome, exception-first,
   progressive disclosure. `DESIGN.md` governs.
5. **Never claim statutory correctness we cannot demonstrate.**
6. **Mobile and vernacular are table stakes for the Indian workforce**, not a
   later polish (Track A).
7. **Do not optimise for feature count.** Breadth makes us comparable; the joins
   make us better.

### 0.1 The module contract — no isolated CRUD islands

**Binding rule. A module that cannot answer these five questions does not
ship.** The primary product value is in the joins between modules, and a module
that quietly duplicates another's source of truth destroys exactly that value.

Every module documents, in its package docstring:

| | |
|---|---|
| **Owns** | The facts it is the single authority for |
| **Consumes** | What it reads from other modules, read-only |
| **Produces** | The facts or events downstream modules depend on |
| **Depended on by** | Who breaks if its output changes shape |
| **Correction behaviour** | What happens downstream when one of its facts is corrected after the fact |

The last row is the one that is usually missing and always matters. A leave
approval reversed in September must have a defined consequence for an August
payroll that is already finalized — and that consequence is an adjustment in a
later period, never a rewrite.

**Current ownership, which nothing may duplicate:**

```
work_calendar   owns  working-day classification (which calendar applies to
                      WHOM, on WHICH date — not "is this a company holiday")
attendance      owns  attendance facts (punches, day states)
leave           owns  leave decisions and balances
hr_core         owns  employee, employment, org, statutory identity
compensation    owns  salary, effective-dated
payroll         owns  work facts, payroll inputs, runs, payslips, adjustments
compliance      owns  statutory obligations, filings, evidence
payments        owns  money movement and its failure states
```

```
calendar + attendance + leave  →  payroll inputs  →  payroll  →  compliance
                                                            └─→  payments
```

### 0.2 Positioning

**Not "an all-in-one HRMS"** — that is generic and invites a feature-comparison
sheet we lose. The product is **HR operations that actually connect**:

```
white collar   employee → leave → attendance → salary → payroll → TDS → payslip
blue collar    worker → site → shift → attendance → OT → contractor →
                        payroll → compliance
HR manager     what needs attention → fix it → run → approve → pay → comply
```

---

## 1. Capability map — honest current state

Percentages are rough, and deliberately unkind.

| # | Capability | Now | What "good" means here |
|---|---|---|---|
| **A** | **Platform** — auth, RLS, roles, notifications, search, audit, mobile, i18n | **35%** | Auth/RLS/roles solid. **Notifications is an empty file.** Search is a stub. No mobile, no vernacular, no settings section |
| **B** | **Core HR** — employee master, org, lifecycle, documents, letters | **30%** | 9 routes. No designation, grade, documents, letters, confirmation, transfers, employment history |
| **C** | **Time & attendance** — punches, shifts, rosters, devices, regularisation | **35%** | Punches, policy, calendar-aware day states. **No shifts, no roster, no device ingest, no regularisation workflow** |
| **C** | **Leave** — types, policies, accrual, balances | **30%** | Quota is *the same for every employee*. No accrual, carry-forward, half-day, comp-off, encashment |
| **C** | **Work calendar** — holidays, work week | **40%** | **One calendar per company.** Holidays unique per `(company, day)` — a Maharashtra and a Karnataka site cannot differ |
| **D** | **Payroll** — engine, wage bases, statutory | **70%** | Genuinely strong. Effective-dated compensation, provenance, immutability, rule versions, contractor reconciliation |
| **D** | **Compliance** — obligations, filings, evidence | **5%** | Statutory calculation only. No obligations, returns, challans, evidence |
| **D** | **Payments** | **0%** | No bank details, no bank file, no payment lifecycle |
| **E** | **Recruitment (ATS)** | **40%** | Jobs, apply, AI screening, scoring, advance/reject. No offers, no onboarding handoff |
| **E** | **Onboarding** | **0%** | Invite only |
| **E** | **Performance** | **0%** | — |
| **E** | **Learning** | **0%** | — |
| **E** | **Engagement** | **0%** | — |
| **F** | **Expenses & reimbursements** | **0%** | — |
| **F** | **Helpdesk** | **0%** | — |
| **G** | **Analytics & reporting** | **5%** | One CSV register |
| **G** | **ESS** | **40%** | Web only: payslips, leave, punch, attendance |

**Two entries above are also payroll correctness bugs**, not just thin modules:

- **One holiday calendar per company.** Working days drive proration; two states
  with different holidays produce wrong pay today. Fixed in **C1**.
- **Leave quota identical for every employee.** Blocks the whole leave model and
  any policy a real customer has. Fixed in **C4**.

---

## 2. Tracks

Seven tracks. They are not sequential phases — several run in parallel — but
the dependencies between them are real and named.

```
A  PLATFORM        auth · roles · notifications · search · audit
                   mobile · vernacular · settings · migration
                        │  (everything depends on A)
   ┌────────────────────┼────────────────────┐
   │                    │                    │
B  CORE HR         C  TIME & ATTENDANCE  E  TALENT
   employee master    shifts · roster        recruitment
   org · lifecycle    devices · leave        onboarding
   documents          calendar               performance
   letters                │                  learning
        └────────┬────────┘                  engagement
                 │
D  PAYROLL & COMPLIANCE  ←── see plan-payroll.md
   engine · statutory · payments · filings · evidence
                 │
   ┌─────────────┴─────────────┐
F  SERVICE                  G  INSIGHT
   expenses · helpdesk         search · analytics
   documents                   audit · reporting
```

**The spine is A → B → C → D.** Everything in D consumes B and C; if B and C
are thin, D produces confident wrong numbers. E, F and G are genuinely
separable and sequenced after the spine holds.

---

## 3. Release sequence

Five releases. Each leaves a coherent product, not a half-migration.

| Release | Contains | Why this order |
|---|---|---|
| **R1 — Reachable** | A: migration, notifications + inbox, settings, mobile PWA, vernacular, custom roles, **search foundation, audit foundation** | Nothing else matters if the product cannot be adopted, reached, searched or accounted for. `modules/notifications/` being empty blocks exception routing everywhere |
| **R2 — The spine holds** | B: employee master depth, documents, lifecycle · C: shifts, device ingest, regularisation, leave policies, per-establishment calendars · D-Phase 1: wage basis, rules-as-data, PayrollContext, attendance bridge | The joins. This is where the product becomes different from a menu of modules |
| **R3 — Money out** | D-Phase 2/3: statutory identity validation, contractor depth, bank details, payment lifecycle, reconciliation | Payroll finishes its job — people actually get paid and it reconciles |
| **R4 — Prove it** | D-Phase 4: obligations, calendar, EPF/ESI/PT chains, evidence, rejection ingestion · G: search, audit UI | Compliance operations and the evidence layer |
| **R5 — The rest of HR** | E: onboarding, recruitment depth, performance, learning, engagement · F: expenses, helpdesk · D-Phase 5–7: F&F, gratuity, bonus, LWF, TDS | Breadth, once the spine is trustworthy |

**R5 is the largest and least specified**, deliberately. Decomposing performance
management now, before R1 ships, would be fiction.

### 3.1 R5 is an engineering sequence, not a commercial prohibition

**R1 → R5 governs how we build. It does not govern what we can sell.**

If a real customer requires a Talent, Service or Insight capability earlier, we
build a **deliberately narrow vertical slice** of it — without compromising the
A → B → C → D spine. The alternative is being held hostage by our own roadmap,
which is a worse failure than a thin module.

**What a thin slice is allowed to be.** A Performance V1, for example:

```
cycle → employee → goals → self review → manager review → rating → finalize
```

That, and nothing more. Not calibration, not 360, not competency frameworks,
not analytics.

**Guardrails — all four, or it is not a slice:**

1. **It obeys the module contract (§0.1).** Owns, consumes, produces, depended
   on by, correction behaviour — documented before the first endpoint.
2. **It does not fork the spine.** No duplicated employee master, no second
   notion of who reports to whom, no private calendar.
3. **It does not delay an R2 gate item.** If it would, it waits or someone else
   builds it.
4. **It is labelled V1 in the product**, so nobody demos a depth we do not have.

A slice that cannot meet these is a request to change the roadmap, and should
be handled as one.

---

## TRACK A — Platform (Release 1)

The four tasks below were previously "Phase 0 adoption blockers". They are
unchanged and remain first.

### A1: Migration suite — get a customer's Excel folder in · **L → split**
Import employee master, salary history, opening balances, attendance. Dry run
first, per-row rejection with a reason, **never a partial load**, idempotent
re-import. Reportedly ~80% of Indian HRMS implementations fail, and the named
causes are **dirty master data**, state PT gaps and no internal owner — not the
software. Most SMEs migrate from a folder of spreadsheets, not another HRMS.
*Deps: none. Shares validation rules with D-2.7.*

### A2: Parallel run and explained diff · **M**
Run a period alongside the incumbent, import their register, diff per employee
per component, and **explain every difference** from the provenance chain.
Parallel run is a standard phase of every Indian implementation, and we are the
only product that can say *why* a number differs. *Deps: D-1.3.*

### A3: Mobile ESS and punch — PWA, not native · **L**
Punch, payslip, leave, attendance on a phone; installable; offline-tolerant.
For frontline and low-signal units, offline sync and local language are required
*or attendance simply fails*. Competitors ship three punch modes — biometric,
geo-fenced mobile, kiosk — so no worker is locked out. *Deps: none.*

### A4: Vernacular payslip and ESS · **M**
Hindi first, then Tamil, Telugu, Marathi. Per-employee preference, not a browser
guess. Visible English fallback, never blank. *Deps: A3.*

### A5: Notifications and a task inbox · **L**
`modules/notifications/` is an **empty `__init__.py`**. There is no way to tell
anyone something needs them — yet exception routing, approvals, reminders and
escalation across every track depend on it.
**AC:** in-app inbox ordered by what blocks payroll soonest · email digest via
the existing `Notifier` seam · data-driven reminder and escalation schedules ·
**a manager's digest contains no salary figures**. *Deps: none.*

### A6: Settings section and the operations/configuration split · **M**
There is **no `/settings` route**. Nine workspace routes and none is settings;
payroll config lives inside `/pay`. Findings must deep-link into the exact
setting with jurisdiction pre-selected. *Deps: none.*

### A7: Roles beyond the four fixed ones · **M**
`ROLE_PERMISSIONS` is a hardcoded dict of admin/hr/manager/employee — correct
while roles were fixed (ADR-0008), wrong for an HRMS. Finance, compliance
officer, recruiter, and per-company custom roles need the `roles` /
`role_permissions` tables the ADR already designed. **This is the documented
trigger firing.** *Deps: none.*

### A8: Search foundation · **M**
Entity lookup — employee, candidate, department, establishment — behind the
existing `Search` interface (ADR-0007), which today is
`raise NotImplementedError`. **A company cannot operate a system it cannot
search.** Record search across payroll and compliance entities extends this in
R4 (G1). *Deps: none.*

### A9: Audit foundation · **M**
`GET /audit` with filters (actor, entity, action, period, correlation id), and
`before`/`after` values on money-bearing changes in a restricted
`financial_audit` table gated by `audit.read_financial`. Platform §10 already
specifies all of this and the code has none of it. *Deps: A7.*

---

### ✅ Checkpoint R1 — **operational, not feature-complete**

R1 does not mean "we added settings, notifications, mobile and i18n." It means
**a real company can operate the system without hitting a foundational dead
end.** Each line below is a demonstration, not a checkbox.

| Foundation | Proven by |
|---|---|
| **Tenant isolation** | An automated test enumerating `pg_tables` asserts FORCE RLS on **every** tenant table. A cross-tenant read is attempted on each and fails |
| **Permission model** | An employee cannot reach another employee's payroll, payslip, compensation or documents — by any route, including a stale artifact link. **A beautiful UI where this fails is a catastrophic product failure, not a bug** |
| Authentication | Password + OAuth, refresh, revocation, session expiry |
| Roles | At least one custom role created per company and enforced (A7) |
| Settings | A company configures payroll, calendar, leave types and roles without an engineer |
| Notifications | An exception reaches the person who can close it, ages, and escalates |
| Search | An operator finds a person by name, code, UAN or PAN in one action |
| Mobile | A worker on a phone with patchy signal punches and reads a payslip |
| Language | Payslip and ESS render in Hindi with a visible English fallback |
| Audit | "Who changed this, when, from what to what" is answerable in the product |
| Migration | A real company's Excel folder loads, validates, and parallel-runs |

**Gate:** every row demonstrated on a real tenant, not asserted from the code.

---

## TRACK B — Core HR (Release 2)

### B1: Employee master depth · **M**
Designation, grade, employment type, gender, exit date, location, PAN, bank
details (encrypted, masked, `pii.read_bank`-gated, financial-audit on read).
*Overlaps D-2.1/2.2 — do it once, here.*

### B2: Employment history as an effective-dated timeline · **L**
Designation, department, manager, location and employment type all change over
time and today are single mutable fields. Same pattern as compensation
versioning: a change is a new row, nothing is overwritten.
**Why it is not cosmetic:** establishment drives PT and ESI jurisdiction, so
"which establishment was this person in during August?" is a payroll question
the current model cannot answer. *Deps: none.*

### B3: Confirmation, probation, transfer and exit workflows · **M**
Probation end dates and confirmation, internal transfer, resignation → notice →
last working day → exit. Feeds F&F (D-6.1) and the movement bridge. *Deps: B2.*

### B4: Documents and letters · **L**
Employee document vault (offer, contract, ID proofs, certificates) with expiry
tracking, and generated letters (offer, confirmation, transfer, experience,
relieving) from templates. Reuses `core/artifacts` — storage, checksums,
versioning and retention already exist. *Deps: A1, D-4.6 retention.*

### B5: Multi-entity and establishment depth · **M**
A group with several legal entities under one login: entity-scoped
establishments, PF/ESI codes, and reporting that rolls up. Directly serves the
multi-establishment segment. *Deps: B2.*

---

## TRACK C — Workforce rules engine (Release 2)

**Not "attendance".** This track owns the rules that decide what a day *was* and
what an employee was *entitled to* — and payroll consumes its output. Calling it
attendance understates it and invites the modules to be built as CRUD islands.

### C1: Calendar assignment and inheritance · **M — correctness blocker**

Holidays are unique per `(company, day)`: **one calendar for the whole company.**
A Mumbai and a Bengaluru establishment cannot differ, and one site working
through a day the rest of the company takes off cannot be expressed at all.
Working days drive proration, so this produces **wrong pay**. P1, not UX.

**The model is the fix, not another column.** The engine must ask:

> *Which calendar applies to **this employee** on **this date**?*

not *"is 15 August a company holiday?"* So:

```
Holiday calendar
    ├── company        (default, inherited)
    ├── establishment  (overrides company)
    ├── location       (overrides establishment)
    └── employee group (overrides location)
```

**AC:** calendars are assignable at each level with inheritance · assignment is
**effective-dated**, because a site's calendar changes between years · the same
date can be a holiday for one establishment and a working day for another ·
resolution is one bulk query for a whole run, not per employee.
**Verify:** two establishments, same month, different holidays → different
working days → different pay, asserted end to end.
*Deps: none. **Must land before D-1.4** (proration policy).*

### C2: Shift master and rostering · **L**
Shift definitions (timings, breaks, night flag, grace), rosters per employee per
day, and rotation patterns. Blue-collar payroll needs the shift a person
actually worked, not a company-wide `workday_start`. *Deps: C1.*

### C3: Attendance regularisation workflow · **M**
Employee raises a correction, manager approves, HR sees what is outstanding
before the run. ~5–8% of daily records need regularisation; today there is no
workflow at all. *Deps: A5.*

### C4: Leave policy engine · **L — core R2 requirement, not polish**

Quota today is *the same for every employee* — the model says so in a comment.
That is not production-ready for any real customer.

```
Leave policy → employee group → leave type → entitlement → accrual
            → carry forward → usage → balance
```

**Required:** policy per grade / location / employment type · accrual (monthly,
annual, **pro-rated on joining**) · carry-forward with caps and expiry ·
**half-days** · comp-off earned against holiday and weekly-off work ·
encashment · probation rules · **exit proration** · negative-balance policy.

**For the blue-collar segment, half-day + comp-off + the shift/holiday
interaction are not optional.** A worker who works a weekly off earns comp-off,
which is a leave fact created by an attendance fact against a calendar rule —
three modules meeting in one transaction. That join is the product.

*Deps: B1, C1, C2.*

### C5: Device and biometric ingest · **M**
CSV/Excel import of device logs with a per-vendor mapping profile; idempotent by
(employee, timestamp, device); unmatched codes surface as findings, never
dropped. The blue-collar bet assumes attendance data exists and we have no way
to collect it at scale. *Deps: C1.*

### C6: Overtime policy · **M**
Eligibility, multipliers and caps as effective-dated configuration rather than
the two constants in `ledger.py`. *Deps: C2, D-1.2.*

---

### ✅ Checkpoint R2 — **the chain, proven end to end**

**This is the real quality gate for calling Lamoon an HRMS.** Not "the modules
exist" — this flow works, for a real tenant, with money coming out correct:

```
employee joins → establishment → holiday calendar → shift → leave policy
              → attendance → leave → work facts → payroll → payslip
```

Every scenario below is an automated test **and** a manual walk-through:

| Scenario | Must produce |
|---|---|
| **Joiner, 15 Aug** | Attendance from the 15th only · correct salary proration · leave entitlement pro-rated on joining · correct PF/ESI for a part month |
| **Leaver, exit 18 Aug** | Attendance stops · salary stops · LOP correct · leave encashed or lapsed per policy · **F&F becomes available** · no payslip in September |
| **Holiday** | No absence, no LOP, and the working-day **denominator** changes |
| **Weekly off worked** | Premium or comp-off earned, per policy, and it reaches the payslip |
| **Half-day** | Attendance → leave/work fact → payroll, prorated correctly |
| **LOP** | Derived from unpaid leave *and* unexplained absence, never from a missing punch alone |
| **Approved OT** | Hours × rule → amount → payslip → statutory basis where applicable |
| **Salary revision mid-month** | Both segments prorated, split explained on the payslip |
| **Two establishments** | Different calendars → different working days → different pay, same run |

**Gate:** all nine pass before any substantial Track E work begins. If they do
not, we do not build performance management.

---

## TRACK D — Payroll and compliance (Releases 2–5)

**Detailed plan: [`plan-payroll.md`](plan-payroll.md).** Eight phases, per-task
acceptance criteria, verification, migration and rollback notes. Summary:

| Phase | Contents | Release |
|---|---|---|
| D-1 | Per-statute wage basis · rules-as-data · PayrollContext · proration policy · bulk persist · attendance bridge · async runs · approval | R2 |
| D-2 | Statutory identity validation · contractor depth · loans · minimum-wage floor | R3 |
| D-3 | Bank validation · payment batch and file · payment lifecycle · reconciliation surface | R3 |
| D-4 | Obligation engine · compliance calendar · EPF/ESI/PT chains · evidence and retention · rejection ingestion | R4 |
| D-5 | Arrears · reimbursements · LWF · bonus · gratuity · full minimum-wage engine | R5 |
| D-6 | F&F settlement | R5 |
| D-7 | TDS subsystem · Form 138 · Form 16 | R5 |
| D-8 | Search · audit query · operational reports | R4/R5 |

**Two items in Track D move because of the scope change:**
- D-2.1/2.2 (bank details, employment attributes) are **absorbed into B1** — one
  employee master, not a payroll-shaped one.
- D-1.4 (proration policy) now depends on **C1**, because per-establishment
  calendars change the working-day denominator.

---

## TRACK E — Talent (Release 5)

### E1: Recruitment depth · **L**
Offers with approval and acceptance, drag-to-move on the pipeline board (needs a
general "set stage" API — today only `screen`/`advance`/`reject` exist), talent
pool, referrals, careers page. *Deps: A5.*

### E2: Onboarding · **L**
Pre-joining document collection, induction checklist with owners, asset and
access provisioning, buddy assignment, and a **hire → employee** handoff that
does not retype anything. The join that makes an ATS worth having inside an
HRMS. *Deps: E1, B1, B4.*

### E3: Performance · **XL → split**
Goals/OKRs, review cycles, self and manager assessment, 360 feedback, 1:1s,
calibration, and a rating that can feed compensation revisions. Competitors are
strong here; this is the largest single item in the plan. *Deps: B2, A5.*

### E4: Learning · **L**
Course catalogue, assignment, completion tracking, certifications with expiry,
and statutory/safety training records — which matter for the industrial segment.
*Deps: B1.*

### E5: Engagement · **M**
Pulse surveys, eNPS, recognition. *Deps: A5.*

---

## TRACK F — Service (Release 5)

### F1: Expenses and reimbursements · **L**
Claim → policy check → manager approval → finance validation → payment, with tax
treatment per category. Feeds payroll as typed ledger inputs (`source="expense"`
with a `source_ref`) or a separate payment run. *Deps: A5, D-3.*

### F2: HR helpdesk · **M**
Ticket categories, SLAs, assignment, knowledge base. **Vernacular matters most
here** — policy queries in Hindi/Tamil/Telugu/Marathi are exactly what a
frontline workforce asks. *Deps: A4, A5.*

### F3: Asset management · **M**
Issue, return, recovery on exit — which lands in F&F. *Deps: B3.*

---

## TRACK G — Insight (Releases 4–5)

*Foundations moved into R1: entity search is **A8**, audit query is **A9** —
a company cannot operate a system it cannot search or account for.*

### G1: Record search across payroll and compliance · **M**
Extends A8 to filtered record search — payslips, obligations, adjustments,
artifacts, tickets, assets — by period, establishment, status, amount range.
Two mechanisms, deliberately: fuzzy trigram lookup for `⌘K`, plain composite
indexes for filtered lists. *Deps: A8, D-1.3.*

### G3: Analytics and reporting · **L**
Headcount, attrition, cost per department and establishment, overtime, LOP,
contractor cost, compliance status. Operational, not decorative; async above a
threshold; every report an artifact. *Deps: G1.*

---

## 4. What this scope change costs — stated plainly

The narrower plan was one product line. This is **seven**, and three of them
(performance, learning, recruitment depth) compete directly with vendors who
have a ten-year head start and dedicated teams.

That is a legitimate decision. Three things follow from it:

1. **R1 and R2 must not slip.** The spine — platform, core HR, time, payroll —
   is what makes breadth worth having. Building performance before leave
   policies work produces a demo, not a product.
2. **E3 (performance) is the single largest item** and should be re-planned in
   full when reached, not treated as one task.
3. **The differentiators do not change.** Provenance, exceptions driven to
   closure, and contractor variance are still the reasons to choose this over
   an incumbent. Breadth makes us comparable; the joins make us better.

---

## 5. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Breadth outruns the spine** | **Critical** | Release gates: no Track E work before R2 checkpoint passes |
| **One holiday calendar produces wrong pay** | **High — live bug** | C1, before D-1.4 |
| **Leave quota identical for everyone** blocks any real customer | High | C4 in R2 |
| Segment bet untested on a customer | High | Three contractor-heavy employers before R3 |
| No path from a customer's Excel in | High | A1 + D-2.7 sharing validation |
| Support latency is the top switching reason | High | Provenance UI so anomalies self-explain; a ticket avoided is a ticket answered |
| Partial Labour Code notification misread | High | Per-statute basis (D-1.1); cite notifications; claim no certainty |
| RLS silently voiding a data migration | High | Suspend RLS on every touched table; assert row counts. Burned twice |
| Performance/LMS built to parity, not to win | Medium | Ship them plainly and lean on the joins, not the feature count |
| TDS half-built | High | D-7 all-or-nothing; explicit labelled input until then |

---

## 6. Open questions

1. **Does R5 sequencing match your commercial reality?** Performance and
   learning are late here. If a deal needs them sooner, the spine slips — say so
   now rather than mid-R2.
2. **Customer validation** — will contractor-heavy employers pay for
   attendance-vs-invoice variance? Cheaper to learn in three conversations than
   three months.
3. **PWA or a Play Store app** (A3)?
4. **Custom roles now or fixed roles longer** (A7)?
5. MinIO in CI to verify `S3BlobStore` — now, or on first real S3 use?
6. `btree_gist` superuser `CREATE EXTENSION` for a true no-overlap constraint?
7. First bank template (D-3.2) — which bank?
