# Payroll & Compliance — Product Experience Specification

**Companion to** `PAYROLL-COMPLIANCE-ARCHITECTURE.md` (system architecture, DB,
API, background jobs, security). This document covers **information
architecture, screen flow, and the test plan** — steps 6, 8 and 9 of the brief.

**Constraint:** the Lamoon visual language stays. Monochrome, warm neutrals,
tabular money on a fixed axis, severity rail plus a word, frozen state reads
square, Lumo the only chroma. Nothing here changes the look. It changes what is
on which screen, and in what order.

---

## 1. Corrections to the UI critique

Three claims in the critique are wrong against the code, and one is right for a
smaller reason than stated. Getting these right changes the size of the work.

### 1.1 The holiday calendar problem is real — confirmed, and now fixed

The critique is right that attendance could not distinguish a weekend from a
no-show, and right that it is a payroll correctness problem rather than a
cosmetic one.

My first reading of this was wrong and worth recording, because the near-miss
is instructive. The `work_calendar` module does exist (migration `0008`), and
`attendance/service.py` does load it and set `working_day` and `holiday` on day
summaries — so at a glance the feature looked present and the UI copy looked
stale. It was not. The annotation loop ran `for d in sorted(by_day)`, where
`by_day` holds **only days that had punches**. The calendar was therefore
attached to exactly the days nobody needed it for, and omitted from every empty
day — which is the entire question. Both the presence list and the heatmap were
blind, and the UI copy was accurate.

Three defects, all fixed in increment 1:

- **`summaries_for` returned only punched days.** Now returns every day in the
  range, each annotated. This is the root cause and fixing it here fixes every
  caller — presence, the heatmap, ESS, and the payroll bridge to come.
- **`GET /attendance/today` discarded the calendar**, reducing the summary to
  `"in" if day.open else ("out" if day.first_in else "absent")`.
- **`today_for` returned a bare `DaySummary(day=today)`** for anyone with no
  punches, keeping the `working_day=True` default — so a Sunday no-show and a
  Tuesday no-show were identical before the route ever saw them.

**Lesson for the rest of this plan:** "the field exists" is not "the feature
works." Every remaining claim of the form *X already handles Y* in either
document was re-checked against the code path that populates it, not the
declaration.

### 1.2 Payroll does have a correctness problem — a different one

The real defect, from the architecture audit: **payroll never reads attendance
at all.** LOP comes only from approved unpaid leave. A month of no-shows is paid
in full. The critique's instinct is right; the mechanism is not the one named.

### 1.3 `/pay` mixing operations and configuration — confirmed

Verified: `pay/page.tsx` is 488 lines rendering setup, run controls, totals,
readiness, exceptions, movement, adjustments, contractors and payslips on one
route. The critique is correct and §3 acts on it.

### 1.4 Salary overwrite — confirmed

`SalaryComponent`'s own docstring concedes it. The compensation redesign in §5
is right and is already Phase 1.1 in the architecture plan.

---

## 2. The one product sentence

> Tell me what needs my attention, let me fix it, show me what payroll will do,
> let me approve it, and give me everything I need afterward.

Every screen below earns its place against that sentence. If a screen does not
help someone *notice*, *fix*, *decide*, or *prove*, it does not ship.

---

## 3. Information architecture

### 3.1 Navigation

```
HOME
PEOPLE          Hiring · People
WORKFORCE       Attendance · Time off
PAYROLL         Overview · Runs · Inputs · Payslips · Adjustments
COMPLIANCE      Overview · Obligations · Returns · Payments · Evidence
```

Two changes from the critique's proposal, both deliberate:

- **No top-level REPORTS.** A report is a download of a thing you are already
  looking at. Putting them in a separate destination means HR navigates *away*
  from context to fetch a file about that context. Downloads live on the screen
  that owns the data, and every generated file is discoverable afterwards from
  Compliance → Evidence and a global artifacts list. This also honours
  `DESIGN.md` §3: *"every additional top-level item is a tax on the product's
  clarity."*
- **No top-level FINANCE.** Nothing exists behind it (architecture doc §3). A
  nav item pointing at an empty module is a promise the product cannot keep.

Nav remains **permission-derived** from one source feeding both the rail and the
route guard, so PAYROLL and COMPLIANCE simply do not exist for a manager.

### 3.2 Where configuration goes

Payroll configuration leaves the monthly workflow entirely:

```
Settings → Payroll
             Salary components
             Statutory settings (schemes, PF ceiling)
             Professional tax slabs
             Establishments
             Pay calendar
```

Configuration is a *setup* activity done once and revisited rarely. Putting it
on the screen HR opens twelve times a year to run payroll is the single largest
IA error in the current build.

**The link back matters more than the separation.** When readiness reports "PT
slabs not configured for Maharashtra," that finding links directly to the exact
settings screen with the jurisdiction pre-selected. HR never hunts for settings;
settings come to HR when a finding needs them.

### 3.3 Screens, and what is a drawer instead

| Screen | Route | Why it is a screen |
|---|---|---|
| Payroll Overview | `/pay` | The workflow surface — status, attention, movement, next action |
| Runs | `/pay/runs` | History; audit and comparison across months |
| Inputs | `/pay/inputs` | Per-period figures with provenance, filterable |
| Payslips (register) | `/pay/register` | Scales to thousands; search and export |
| Adjustments | `/pay/adjustments` | Corrections to closed periods |
| Compliance Overview | `/compliance` | What is due, what is wrong |
| Obligations | `/compliance/obligations` | The operational list |
| Evidence | `/compliance/evidence` | Audit-ready artifacts |

**Drawers, not screens:** one payslip · one exception's detail · "Why?" at every
depth · one obligation's timeline · one employee's compensation history · one
contractor's worker-day breakdown · artifact generation status.

A drawer keeps the list behind it, which is what makes triage fast: fix one, the
list is still there, fix the next.

---

## 4. Payroll Overview — the workflow surface

Replaces the current `/pay`. One column, ordered by the question HR asks next.

```
Payroll · August 2026                              Ready for review

  Gross              Deductions            Net              Employer cost
  ₹48,20,140         ₹5,43,208             ₹42,76,932       ₹52,10,480
  1,284 employees

  ─────────────────────────────────────────────────────────────────

  NEEDS ATTENTION                                              8

  ▍ 1 employee has no salary structure              Blocking
      Ramesh Kumar — added 3 Aug, never assigned
      → Assign salary

  ▍ 3 employees have unresolved attendance          Blocking
      11 days with no punch and no leave
      → Resolve attendance

  ▎ 2 overtime records await approval               Review
      68 hours · ₹8,200 not included in this pay
      → Approve overtime

  ▎ 1 unusual salary increase                       Review
      Priya Shah +42% — revision effective 01 Aug
      → View compensation

  ─────────────────────────────────────────────────────────────────

  WHAT CHANGED                                    vs July  +₹2,40,180

  Joiners            12          +₹2,70,400
  Exits               5          −₹1,12,900
  Salary revisions   18          +₹1,40,200
  Overtime            —            +₹82,300
  Loss of pay         —            −₹39,820
                                                        → Investigate

  ─────────────────────────────────────────────────────────────────

  COMPLIANCE

  EPF     ₹5,42,180   Ready          ESI   ₹1,24,820  Ready
  PT        ₹48,200   Attention      TDS   Input required
                                                    → Open compliance

  ─────────────────────────────────────────────────────────────────

  [ Review payroll ]
```

**Design notes.**
Totals are a typographic row on the money axis — not cards, not tiles, no
colour. The severity rail (`▍` blocking, `▎` review) carries weight *and* a
word, never colour alone. One primary action at the bottom; the step name
changes with state (`Calculate` → `Review payroll` → `Approve` → `Finalize`).

**Progress replaces the button while a run is calculating** — `job_state =
RUNNING` shows a determinate count ("847 of 1,284"), because a payroll run that
looks frozen is the moment HR reaches for Excel.

---

## 5. Screen specifications

### 5.1 Exceptions — the heart of the product

Every finding carries: **severity · who · what · where it came from · what to do
· the action**. The last two are what distinguish this from a validation report.

```
▍ Blocking    Ramesh Kumar
              No salary structure
              Source    Employee created 3 Aug 2026, never assigned
              Effect    Excluded from this payroll
              → Assign salary
```

Clicking opens a drawer *with the fix in it*, not a link to a page where the fix
might be. On success the row collapses to `✓ Resolved` and the count decrements
in place. Resolving must never reload the list out from under the person
working it.

Findings are grouped by **cause, not by employee** — "3 employees have
unresolved attendance" is one row that expands, because the fix is one action
taken three times, not three separate investigations.

### 5.2 Payroll Register

Server-side everything. Search by name, employee ID, PAN, UAN. Filters:
department, establishment, status, has salary change, has LOP, has OT, variance
above a threshold.

```
Employee          ID       Days    Gross      Deductions   Net        Status
Ravi Kumar        E-0142   22/22   ₹41,000    ₹2,800       ₹38,200    ✓
Priya Shah        E-0088   22/22   ₹58,000    ₹6,420       ₹51,580    ✓  +42%
Lakshmi Devi      E-0311   17/22   ₹26,591    ₹2,000       ₹24,591    ⚠ LOP
```

Money right-aligned, `tabular-nums`, one axis. Variance and LOP annotate the row
rather than adding columns. Bulk selection → `Download selected payslips`, which
queues an artifact rather than blocking.

### 5.3 "Why?" — progressive disclosure in four levels

The differentiator, and the architecture already stores everything needed
(`payslip.breakdown.basis` + `rule_versions`).

```
L1  SUMMARY    Net ₹38,200                                    [Why?]
L2  WHY        Gross ₹41,000 − PF ₹1,080 − ESI ₹308 − PT ₹200
               − TDS ₹1,212  =  Net ₹38,200
L3  DETAIL     PF ₹1,080
               Statutory wage   ₹9,000
               Employee rate    12%
               ₹9,000 × 12%   = ₹1,080
               Why ₹9,000?     Basic ₹9,000 is the wages component;
                               excluded allowances are within the 50% limit
L4  AUDIT      Rule      EPF v2025.11 (effective 21 Nov 2025)
               Source    Compensation version #18, effective 01 Aug 2026
               Input     payroll_input #4471, approved by A. Sharma, 28 Aug
```

L1–L2 are for everyone. L3 answers the employee's question. L4 answers the
auditor's. **L4 never shows a database id as an id** — "Compensation version
#18" is a human-facing version number, not a uuid. Per the brief's Part 21, no
`PayrollContext`, no Celery, no RLS, no uuids on screen.

### 5.4 Compensation

Replaces the current overwrite editor.

```
COMPENSATION

Current                                  ₹41,000 / month
                                         Effective 01 Aug 2026
  Basic                ₹12,000
  HRA                   ₹8,000
  Special allowance    ₹21,000
                                                    [ Change compensation ]

HISTORY
  01 Aug 2026    ₹41,000    Salary revision      A. Sharma
  01 Jan 2026    ₹38,000    Joining              A. Sharma
```

The change form takes an **effective date, components, and a reason**, then
shows `Preview impact` before saving: what this does to next month's gross, PF,
ESI, and whether it triggers arrears for a retrospective date. A raise
backdated two months is a normal Indian payroll event and must not be a
surprise.

### 5.5 Attendance day states

One vocabulary, used by presence, the heatmap, and the payroll bridge:

```
present · absent · weekly_off · holiday · paid_leave · unpaid_leave
half_day · missing_punch · work_from_home · on_duty
```

**`absent` and `missing_punch` are different.** Missing punch means someone
worked and the record is incomplete; absent means they were not there. Only one
of those is a candidate for LOP, and conflating them is how payroll systems
quietly underpay people.

**No punch never becomes LOP automatically.** It becomes an exception that HR
resolves — regularise, mark leave, or confirm as LOP. That is the brief's Part 9
and it is the correct default: silently docking someone's pay because a
biometric device failed is a worse error than paying a day too many.

### 5.6 Compliance Overview and obligation detail

```
COMPLIANCE · August 2026            12 obligations · 9 done · 2 attention · 1 n/a

EPF    Mumbai      ₹5,42,180   ECR ready         Due 15 Sep      → Open
ESI    Mumbai      ₹1,24,820   Return ready      Due 21 Sep      → Open
PT     Maharashtra   ₹48,200   Not prepared      Due 30 Sep      → Resolve
TDS    —            External input required                      → Review
LWF    Maharashtra  Not applicable
```

Obligation detail is a **timeline, not a status dropdown** — a status field
tells you where you are; a timeline tells you what remains:

```
EPF — August 2026 — Mumbai                              ₹5,42,180 · 1,284 employees

  ✓ Payroll finalized            31 Aug   A. Sharma
  ✓ Contribution calculated      31 Aug
  ✓ Register generated           31 Aug   [Download]
  ✓ ECR generated                31 Aug   [Download]
  ○ Submitted                      —      [Record submission]
  ○ Challan                        —
  ○ Payment                        —
  ○ Reconciled                     —

  Due 15 Sep · 12 days remaining
```

Completed steps carry an actor and a date. `Overdue` is derived at render from
`due_date` and status — never a stored field that a cron forgot to update.

### 5.7 Evidence

Per obligation and per period: payroll register, ECR/return file, submission
acknowledgement, challan, payment receipt, reconciliation report. Each row shows
generated-at, by whom, size, and checksum. `Download all` bundles them.

The goal stated plainly: **audit-ready payroll in one click.** An auditor asks
for August EPF; HR produces one archive containing the calculation, the filing,
the payment and the reconciliation, each traceable to the run that produced it.

### 5.8 Blue-collar payslip

Not a different application — the same payslip, rendering work facts only when
work facts exist (already how the code works, no `worker_type` branch anywhere
in the UI):

```
WORK THIS PERIOD
  Worked days 25 · Regular 200h · Overtime 18h · Night shifts 4
  Holiday work 1 · Site Pune Project A · Shift 08:00–18:00

  3 days are awaiting approval and not included in this pay.
```

---

## 6. Test plan

Workflow tests, not screen tests. Each names the user-visible behaviour that
must hold.

**Day states and the bridge**
- Sunday with no punch renders `weekly_off`, never `absent`
- A gazetted holiday renders `holiday` with its name
- Approved paid leave renders `paid_leave` and produces no LOP
- Approved unpaid leave produces LOP
- Punch-in with no punch-out renders `missing_punch`, not `absent`
- A working day with nothing at all becomes an **unapproved** work fact and a
  validation finding — and **never** a silent deduction
- Re-running the bridge is idempotent

**Compensation**
- Payroll for August resolves the version effective 01 Aug, not the latest
- A retrospective revision produces arrears and does not mutate finalized runs
- Overlapping versions for one employee are rejected
- History remains queryable after five revisions

**Exceptions**
- Every blocking finding excludes its employee from calculation and names them
- Resolving a finding decrements the count without reloading the list
- A finding links to a context where the fix is possible

**Register and downloads**
- 5,000 payslips paginate without loading the browser
- Search by PAN and UAN returns the right employee
- A bulk export queues an artifact and never blocks the request
- A failed artifact is retryable

**Compliance**
- Finalizing emits one obligation per (establishment, statute)
- Obligation amount equals the sum of payslip statutory lines
- `Overdue` derives correctly at the due-date boundary
- A submission failure sets `SUBMISSION_FAILED`, never silent success
- Payment failure leaves the payslip untouched

**Permissions**
- A manager sees no Payroll or Compliance nav and is bounced by the guard
- Finance sees payment amounts but not salary structures
- An employee sees only their own finalized payslips
- Cross-tenant access is impossible on every new table

**Invariants** (extending the existing 21)
- gross − deductions = net
- employer EPF + EPS = employer total
- same inputs + same rule version = identical output
- rebuild is idempotent; finalized payroll is immutable
- adjustments never mutate history

---

## 7. Build order

Follows the architecture plan's phases. UI lands with the capability behind it —
**no screen ships before its data exists**, because a shell that looks finished
is worse than an absent feature.

| # | Increment | Depends on |
|---|---|---|
| 1 | **Attendance day states** — ✅ shipped. Every day returned and annotated; presence reports the full vocabulary | — |
| 2 | Attendance → work facts bridge; unexplained absence as an exception | 1 |
| 3 | Effective-dated compensation + history UI | — |
| 4 | Split `/pay`: operations vs Settings → Payroll | — |
| 5 | Exceptions as the primary surface | 2, 3 |
| 6 | PayrollContext, bulk calculation, async run + progress | 3 |
| 7 | Register: pagination, search, filters | 6 |
| 8 | Artifacts + downloads; "Why?" L1–L4 | 7 |
| 9 | Compliance overview, obligations, timeline, evidence | 8 |
| 10 | Payments | 9 |

Increment 1 is small, self-contained, fixes a live user-visible wrong, and is
the prerequisite for the bridge. It ships first.
