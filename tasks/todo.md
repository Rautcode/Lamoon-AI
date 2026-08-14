# Lamoon HR — HRMS task list

Companion to [`plan.md`](plan.md) (master) and [`plan-payroll.md`](plan-payroll.md)
(Track D detail, decomposed to acceptance criteria).

**We are building an HRMS.** Payroll is its deepest pillar, not the product.

Scope key: **XS** 1 file · **S** 1–2 · **M** 3–5 · **L** 5–8 · **XL** split it.
Markers: ⚠ risk or live bug · 💡 rationale · 📊 measured · ✔ verification

**Release gate:** no *substantial* Track E work before the R2 checkpoint passes.
Breadth outrunning the spine is the top risk in this plan.

**But R5 is an engineering sequence, not a commercial prohibition.** If a real
customer needs a Talent/Service/Insight capability sooner, build a deliberately
narrow vertical slice — all four guardrails, or it is not a slice:
① obeys the module contract · ② does not fork the spine (no second employee
master, reporting line or calendar) · ③ does not delay an R2 gate item ·
④ labelled V1 in the product.

**Module contract — binding.** Every module documents in its package docstring:
what it **owns**, **consumes**, **produces**, who **depends on** it, and its
**correction behaviour**. No module duplicates another's source of truth. A
module that cannot answer all five does not ship.

---

## R1 — Reachable · Track A (Platform)

- [ ] **A1 Migration suite — a customer's Excel folder in** · L → split · deps: none
  - [ ] Employee master, compensation history, opening balances, attendance
  - [ ] **Dry run** shows what would land before anything is written
  - [ ] Per-row rejection with a reason; **never a partial load**
  - [ ] Re-importing the same file is idempotent
  - [ ] ✔ A deliberately dirty sheet loads nothing and reports every row
  - [ ] ⚠ ~80% of Indian HRMS implementations reportedly fail on **dirty master
        data**, not software. Most SMEs migrate from spreadsheets, not an HRMS

- [ ] **A5 Notifications and task inbox** · L · deps: none
  - [ ] In-app inbox ordered by what blocks payroll soonest
  - [ ] Email digest via the existing `Notifier` seam
  - [ ] Data-driven reminder and escalation schedules
  - [ ] ✔ A manager's digest contains no salary figures
  - [ ] ⚠ **`modules/notifications/` is an empty `__init__.py`.** Exception
        routing, approvals and escalation across every track depend on this

- [ ] **A6 Settings section + operations/config split** · M · deps: none
  - [ ] `/settings` — payroll, calendar, leave types, roles, establishments
  - [ ] `/pay` carries operations only
  - [ ] Findings deep-link into the exact setting, jurisdiction pre-selected
  - [ ] ⚠ **There is no `/settings` route.** Nine workspace routes, none is settings

- [ ] **A3 Mobile ESS and punch (PWA)** · L · deps: none
  - [ ] Punch, payslip, leave, attendance on a phone; installable; offline-tolerant
  - [ ] ✔ Duplicate-punch suppression on re-sync; usable at 360px and throttled
  - [ ] ⚠ For frontline and low-signal units, offline sync and local language are
        required *or attendance simply fails*

- [ ] **A4 Vernacular payslip and ESS** · M · deps: A3
  - [ ] Hindi first, then Tamil / Telugu / Marathi
  - [ ] Per-employee preference, not a browser guess
  - [ ] ✔ Untranslated strings fall back to English visibly, never blank

- [ ] **A7 Roles beyond the four fixed ones** · M · deps: none
  - [ ] `roles` / `role_permissions` tables (ADR-0008 designed them)
  - [ ] Finance, compliance officer, recruiter + per-company custom roles
  - [ ] 💡 The ADR's documented trigger — "when tenants define custom roles" — fires here

- [ ] **A2 Parallel run and explained diff** · M · deps: D-1.3
  - [ ] Import the incumbent's register; diff per employee × component
  - [ ] Each difference attributed — salary version, LOP, wage basis, rule version
  - [ ] Exportable as an artifact for customer sign-off
  - [ ] 💡 **Strongest sales asset we have.** Parallel run is standard in every
        Indian implementation and we are the only product that can say *why*

- [ ] **A8 Search foundation** · M · deps: none
  - [ ] Entity lookup — employee, candidate, department, establishment
  - [ ] ⚠ `Search.query()` is `raise NotImplementedError`. A company cannot
        operate a system it cannot search

- [ ] **A9 Audit foundation** · M · deps: A7
  - [ ] `GET /audit` filtered by actor, entity, action, period, correlation id
  - [ ] `before`/`after` on money-bearing changes in `financial_audit`,
        gated by `audit.read_financial`
  - [ ] 💡 Platform §10 already specifies all of this; the code has none of it

### ✅ Checkpoint R1 — operational, **not** feature-complete

*"We added settings, notifications, mobile and i18n" is not R1. R1 is: a real
company can operate the system without hitting a foundational dead end.* Each
line is demonstrated on a real tenant, not asserted from the code.

- [ ] **Tenant isolation** — a test enumerates `pg_tables` and asserts FORCE RLS
      on **every** tenant table; a cross-tenant read is attempted on each and fails
- [ ] **Permission model** — an employee cannot reach another employee's
      payroll, payslip, compensation or documents by **any** route, including a
      stale artifact link
      - ⚠ *A beautiful UI where this fails is a catastrophic product failure,
        not a bug*
- [ ] Authentication — password + OAuth, refresh, revocation, expiry
- [ ] Roles — a custom role created per company and enforced
- [ ] Settings — a company configures payroll, calendar, leave types and roles
      without an engineer
- [ ] Notifications — an exception reaches the closer, ages, escalates
- [ ] Search — a person found by name, code, UAN or PAN in one action
- [ ] Mobile — a worker on a phone with patchy signal punches and reads a payslip
- [ ] Language — payslip and ESS in Hindi with a visible English fallback
- [ ] Audit — "who changed this, when, from what to what" answerable in-product
- [ ] Migration — a real company's Excel folder loads, validates, parallel-runs

---

## R2 — The spine holds · Tracks B, C, D-1

- [ ] **C1 Calendar assignment and inheritance** · M · deps: none
  - [ ] ⚠ **LIVE PAYROLL BUG — P1, not UX.** Holidays are unique per
        `(company, day)`: one calendar for the whole company. Working days drive
        proration, so two states today produce **wrong pay**
  - [ ] The engine asks *"which calendar applies to **this employee** on **this
        date**?"* — never *"is 15 Aug a company holiday?"*
  - [ ] Assignable at company → establishment → location → employee group,
        with inheritance
  - [ ] **Assignment is effective-dated** — a site's calendar changes between years
  - [ ] Same date = holiday for one establishment, working day for another
  - [ ] Resolution is one bulk query per run, not per employee
  - [ ] ✔ Two establishments, same month, different holidays → different working
        days → different pay, asserted end to end
  - [ ] **Must land before D-1.4** (proration policy)

- [ ] **B1 Employee master depth** · M · deps: none *(absorbs D-2.1, D-2.2)*
  - [ ] Designation, grade, employment type, gender, exit date, location
  - [ ] PAN, bank details — encrypted, masked, `pii.read_bank`-gated
  - [ ] ✔ Financial-audit entry written **on read** of full bank details

- [ ] **B2 Employment history as an effective-dated timeline** · L · deps: none
  - [ ] Designation, department, manager, location, employment type versioned
  - [ ] 💡 Not cosmetic: establishment drives PT and ESI jurisdiction, so "which
        establishment was this person in during August?" is a payroll question
        the current model cannot answer

- [ ] **C4 Leave policy engine** · L · deps: B1, C1, C2 — **core R2, not polish**
  - [ ] ⚠ Quota is currently *the same for every employee* — the model says so
        in a comment. Not production-ready for any real customer
  - [ ] `policy → employee group → leave type → entitlement → accrual →
        carry forward → usage → balance`
  - [ ] Policy per grade / location / employment type
  - [ ] Accrual: monthly, annual, **pro-rated on joining**; carry-forward caps + expiry
  - [ ] **Half-days** · comp-off earned against holiday and weekly-off work ·
        encashment · probation rules · **exit proration** · negative-balance policy
  - [ ] ✔ Half-day LOP prorates correctly through payroll
  - [ ] 💡 For blue-collar, half-day + comp-off + shift/holiday interaction are
        **not optional**. A worker working a weekly off earns comp-off — a leave
        fact created by an attendance fact against a calendar rule. Three
        modules in one transaction. **That join is the product**

- [ ] **C2 Shift master and rostering** · L · deps: C1
  - [ ] Shift timings, breaks, night flag, grace; rosters; rotation patterns
  - [ ] 💡 Blue-collar payroll needs the shift actually worked, not a
        company-wide `workday_start`

- [ ] **C3 Attendance regularisation workflow** · M · deps: A5
  - [ ] Employee raises, manager approves, HR sees what is outstanding pre-run
  - [ ] 📊 ~5–8% of daily records need regularisation; today there is no workflow

- [ ] **C5 Device and biometric ingest** · M · deps: C1
  - [ ] Per-vendor mapping profile; idempotent by (employee, timestamp, device)
  - [ ] Unmatched codes surface as findings, never dropped
  - [ ] ⚠ The blue-collar bet assumes attendance data exists and we cannot collect it

- [ ] **C6 Overtime policy** · M · deps: C2, D-1.2
  - [ ] Eligibility, multipliers, caps as effective-dated config, not two constants

- [ ] **B3 Confirmation, probation, transfer, exit workflows** · M · deps: B2
- [ ] **B5 Multi-entity and establishment depth** · M · deps: B2

- [ ] **D-Phase 1** — see [`plan-payroll.md`](plan-payroll.md)
  - [x] 1.1 wage basis per statute and jurisdiction
  - [~] 1.2 rules-as-data — **withdrawn**, premise was wrong (a migration IS
        a deploy here); `source_note` citations shipped instead
  - [ ] 1.3 PayrollContext
  - [ ] 1.4 proration policy *(now deps: C1)* · 1.5 bulk persist
  - [ ] 1.6 attendance bridge with **owned** exceptions · 1.7 async · 1.8 approval

### ✅ Checkpoint R2 — the chain, proven end to end

**The real quality gate for calling Lamoon an HRMS.** Not "the modules exist" —
this flow works on a real tenant with money coming out correct:

```
employee joins → establishment → holiday calendar → shift → leave policy
              → attendance → leave → work facts → payroll → payslip
```

Each scenario is an automated test **and** a manual walk-through:

- [ ] **Joiner, 15 Aug** — attendance from the 15th · salary prorated · leave
      entitlement pro-rated on joining · PF/ESI correct for a part month
- [ ] **Leaver, exit 18 Aug** — attendance stops · salary stops · LOP correct ·
      leave encashed or lapsed per policy · **F&F available** · no Sept payslip
- [ ] **Holiday** — no absence, no LOP, and the working-day **denominator** changes
- [ ] **Weekly off worked** — premium or comp-off earned per policy, reaching the payslip
- [ ] **Half-day** — attendance → leave/work fact → payroll, prorated correctly
- [ ] **LOP** — from unpaid leave *and* unexplained absence, never from a
      missing punch alone
- [ ] **Approved OT** — hours × rule → amount → payslip → statutory basis
- [ ] **Salary revision mid-month** — both segments prorated, split explained
- [ ] **Two establishments** — different calendars → different working days →
      different pay, same run

Plus the engineering bar:
- [ ] Query count per payroll run bounded and asserted
- [ ] `ccdemo` shadow-run byte-identical

- [ ] 🚧 **Gate: all nine scenarios pass before substantial Track E work.**
      If they do not, we do not build performance management

---

## R3 — Money out · Tracks D-2, D-3

- [ ] **D-2** statutory identity validation (2.7) · contractor depth (2.8) ·
      device ingest (2.9 → now C5) · loans (2.5) · minimum-wage floor (2.6)
- [ ] **D-3** bank validation · payment batch and file · payment lifecycle ·
      reconciliation surface
- [ ] **B4 Documents and letters** · L · deps: A1, D-4.6
  - [ ] Document vault with expiry tracking; generated offer/confirmation/
        transfer/experience/relieving letters
  - [ ] 💡 Reuses `core/artifacts` — storage, checksums, versioning already exist

### ✅ Checkpoint R3
- [ ] ✔ A failed payment leaves the payslip byte-identical
- [ ] `FINALIZED ≠ PAID` visible in the UI
- [ ] **Customer validation done** — three contractor-heavy employers

---

## R4 — Prove it · Tracks D-4, G

- [ ] **D-4** obligation engine · compliance calendar · EPF/ESI/PT chains ·
      evidence + **retention fix** · rejection ingestion
- [ ] **G1 Record search across payroll and compliance** · M · deps: A8, D-1.3
  - [ ] Payslips, obligations, adjustments, artifacts by period, establishment,
        status, amount range
  - [ ] 💡 Two mechanisms on purpose — trigram lookup for `⌘K` (A8), plain
        composite indexes for filtered lists

*(Search and audit **foundations** moved to R1 as A8/A9 — a company cannot
operate a system it cannot search or account for.)*

### ✅ Checkpoint R4
- [ ] Read-only for one full cycle before enabling the compliance workflow
- [ ] The product asserts *what it calculated and under which rule version* —
      never *this filing is correct*

---

## R5 — The rest of HR · Tracks E, F, D-5–7

- [ ] **E1** Recruitment depth — offers, drag-to-move, talent pool, careers · L
- [ ] **E2** Onboarding — pre-joining, checklist, provisioning, **hire → employee
      handoff with no retyping** · L
- [ ] **E3** Performance — goals/OKRs, cycles, 360, 1:1s, calibration · **XL → re-plan in full when reached**
- [ ] **E4** Learning — catalogue, assignment, certifications with expiry,
      statutory/safety training records · L
- [ ] **E5** Engagement — pulse, eNPS, recognition · M
- [ ] **F1** Expenses and reimbursements · L
- [ ] **F2** HR helpdesk — **vernacular matters most here** · M
- [ ] **F3** Asset management — issue, return, recovery on exit · M
- [ ] **G3** Analytics and reporting · L
- [ ] **D-5** arrears *(parity)* · reimbursements · LWF · bonus · gratuity · minimum-wage engine
- [ ] **D-6** F&F settlement *(catching up — Zoho has all of it)*
- [ ] **D-7** TDS · Form 138 · Form 16 — all-or-nothing; **the proof workflow is
      the bulk, not the tax maths**

---

## Standing bar — every task clears these

- [ ] **Module contract documented** — owns / consumes / produces / depended on
      by / correction behaviour. No duplicated source of truth
- [ ] Full backend suite green (baseline: 425)
- [ ] ruff · mypy · tsc · eslint · next build clean
- [ ] Verified against live services, not mocks
- [ ] Reconciliation invariant for the touched area still holds
- [ ] Migration suspends RLS on **every** touched table and asserts row counts
- [ ] Nothing claims statutory correctness it cannot demonstrate
- [ ] Committed, pushed, CI green

---

## Blocked on a human

1. **Does R5 sequencing match commercial reality?** Performance and learning are
   late. If a deal needs them sooner the spine slips — say so now, not mid-R2.
2. **Customer validation** — will contractor-heavy employers pay for
   attendance-vs-invoice variance?
3. PWA or a Play Store app (A3)?
4. Custom roles now, or fixed roles longer (A7)?
5. MinIO in CI to verify `S3BlobStore`?
6. `btree_gist` superuser `CREATE EXTENSION`?
7. First bank template (D-3.2) — which bank?
