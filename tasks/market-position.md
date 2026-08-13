# What Indian HRMS/payroll products actually do, and where Lamoon can win

**Written:** 13 August 2026, before starting Phase 1 of `tasks/plan.md`.

**A caveat on sources, stated up front.** Almost every "greytHR vs Keka vs Zoho"
page on the open web is SEO content published by a *competing vendor* — the
Keka-vs-greytHR comparison is hosted by HROne, the Keka-alternatives page by
greytHR, the greytHR-alternatives page by PocketHRMS. Their feature claims are
marketing and their criticism of rivals is self-serving. What follows treats
**product documentation as fact**, **pricing as roughly right**, and
**complaints as a weak signal that is only credible where several
independently-motivated sources agree**. Nothing here is a substitute for
talking to five real payroll operators.

---

## 1. What these products actually solve

The established Indian players — greytHR, Keka, Zoho Payroll, RazorpayX
Payroll, HROne, Darwinbox — all solve the same core:

- Salary structures and monthly calculation
- PF, ESI, Professional Tax, TDS
- Payslips, registers, statutory reports
- Leave and attendance, to varying depths
- Form 16 and quarterly TDS statements
- Rule updates pushed by the vendor when EPFO circulars or Budget changes land

**Calculation is table stakes.** Every one of them computes PF/ESI/PT correctly.
Building a better calculator is not a business.

---

## 2. The one genuinely hard thing a competitor does

**RazorpayX Payroll actually pays and files.** Their documentation is explicit:
it automatically calculates *and pays* TDS, PF, ESI and PT, files quarterly
returns, handles PF UAN and ESI IP registration, and makes challans available
on the dashboard by the 28th.

That is not a software advantage. **It is a payments-licence advantage.**
Razorpay is a payment gateway with money-movement rails and the regulatory
posture to debit an employer and remit to EPFO, ESIC and the Income Tax
Department. Lamoon cannot replicate that without becoming a regulated payment
entity or partnering with one.

**Strategic conclusion: do not compete on auto-filing.** Any roadmap item that
implies "we file for you" is a promise this product cannot keep, and a customer
who wants that should buy RazorpayX.

**But this does not kill the compliance module.** Razorpay is the exception,
not the norm — greytHR, Keka and Zoho generate the artifacts and leave filing
to the employer or their CA. For the majority who file manually, *knowing what
is due, what has been filed, what has been paid, and being able to prove it*
is unsolved. That is Phase 4, and it stays.

---

## 3. Where the incumbents are weak

Signal is weaker here (see the caveat), but these recur across sources with
different axes to grind, which is the only reason to believe them:

| Complaint | Sources agreeing | Credibility |
|---|---|---|
| Support is slow exactly when payroll breaks mid-month | multiple, incl. rivals of different products | Medium-high — too specific to be pure marketing |
| Customisation needs a vendor ticket and a reply loop | multiple | Medium |
| Modules don't integrate with each other (HRIS vs Letter vs Exit vs Expense) | greytHR-specific, from a rival | Low-medium — but consistent with the fragmentation theme below |
| Mobile lags desktop | multiple | Medium |
| Keka is priced out of small teams (~₹6,999/mo for 25) | vendor pricing pages | High — this is published pricing |
| Zoho splits payroll across two products (People + Payroll) | Zoho's own docs | High |

---

## 4. The validated gap — and it is not calculation

The most useful finding, because it is a description of behaviour rather than a
feature claim:

> Attendance lives in one system. Leave approvals happen somewhere else.
> Payroll attempts to reconcile everything at month-end. **Complete integration
> between systems often remains incomplete, forcing HR teams to maintain Excel
> spreadsheets for reconciliation and exception handling.**

And: *"inconsistencies... only appear at the very end during reconciliations."*

So HR teams that already own a payroll product still keep Excel — **not to
calculate PF, but to reconcile fragmented data and to work through
exceptions.** That is the residual job the category has not finished.

This is exactly the thesis Lamoon is already built around, and it is worth
saying plainly what it implies:

- The product's job is **not** to compute payroll. It is to make the month's
  exceptions visible early, resolvable in place, and provably resolved.
- Reconciliation is not a Phase-5 report. It is the point.

---

## 5. Blue-collar and contractor — the sharpest edge

The market here is real but thin, and it is served by *specialists* rather than
mainstream HRMS: BlueTree for contract-labour workforce management, TeamLease
RegTech for labour-return/register automation, HROne advertising CLRA-leaning
registers.

What they compete on is **CLRA compliance paperwork** — registration, Form XIII
contractor register, licence checks, statutory registers.

**What nobody advertises is the money question.** Did the contractor bill for
days nobody worked? Lamoon already computes what attendance says a contractor
is owed, per worker-day, against what they invoiced, and refuses approval at
non-zero variance. In an afternoon's searching I found no competitor marketing
attendance-versus-invoice variance reconciliation.

The stakes justify it: absorbing contract workers as permanent employees after a
CLRA failure can raise payroll cost 30–50% overnight, and the principal-employer
threshold (20+ contract workers) catches ordinary mid-sized manufacturers.

**This is the most defensible differentiation in the product today, and the
plan under-weights it.**

---

## 6. The pricing reality — this changes strategy

| Product | Price |
|---|---|
| greytHR | **Free up to 25 employees** |
| Zoho Payroll | ~₹33 per employee/month |
| RazorpayX Payroll | ₹30–100 per employee/month |
| Keka | ₹60–150 PEPM, or ~₹6,999/month flat for 25 |
| Payroll *outsourcing* | ₹150–400 per employee/month |

Two consequences, and they are uncomfortable:

**A generic SME HRMS has a zero-price competitor.** greytHR is free to 25
employees, which is precisely the band `ADR-0001` names as the target
(20–500). "Better UX for small Indian SMEs" is not a business when the
incumbent is free and already compliant.

**The willingness to pay is where the leakage is.** Outsourcing costs 2–10× the
software, and firms pay it because payroll is *risky*, not because it is hard.
The segments that will pay are the ones with measurable money at stake:
contractor-heavy and multi-establishment employers, where a variance report
pays for the software in one month.

---

## 6b. What their users actually do by hand — and two corrections

Follow-up research, looking for the specific manual work rather than the theme.
**Two of my assumptions were wrong**, and the corrections matter because they
remove things from the differentiation list.

**Correction 1 — arrears and mid-month revision are NOT gaps.** Zoho Payroll
automatically calculates arrears on a salary revision and pays them in the
payout month. Keka flows a CTC revision straight into arrears and PF ceiling
checks. Keka's own help centre documents mid-cycle revisions splitting the
month at the effective date. Our effective-dated compensation work reaches
**parity** here; it is not an advantage.

**Correction 2 — F&F is not a market gap, it is a gap in OUR product.** Zoho
has termination, final settlement, bulk exit import and an F&F report. This is
us catching up, not us leading.

**What genuinely remains manual, with evidence:**

| Manual work | Evidence | Why software hasn't fixed it |
|---|---|---|
| **Attendance regularisation chasing** | ~**5–8% of daily attendance records** need regularisation; requests "vanish into endless email threads", managers forget, and up to **20 land on HR's desk the night before payroll** | It is a *workflow and chasing* problem, not a calculation one. Products record attendance; they do not drive the exception to closure |
| **Month-end reconciliation across systems** | Attendance in one system, leave in another; "inconsistencies only appear at the very end during reconciliations"; Excel persists for exactly this | Requires one authoritative input ledger. Bolting reconciliation onto separate modules doesn't produce it |
| **"Why is my salary different this month?"** | Named as one of the most common HR queries; CTC-vs-net confusion, TDS projection swings, LOP | The engine computes from a live salary field. It cannot reconstruct *why* after the fact, so a human does |
| **Contractor invoice checking** | Specialists sell CLRA registers and licences; nobody sells attendance-vs-invoice variance | The data (per-worker-day attendance) usually sits with the contractor, not the principal employer |
| **Getting help when payroll breaks mid-month** | Recurring complaint: slow payroll-module support, ticket-and-reply loops | If the product can't explain itself, every anomaly becomes a vendor ticket |

At 200 employees × 22 working days, 5–8% regularisation is **220–350 exceptions
a month**. That is the number to beat.

---

## 7. What to do differently

Seven changes to `tasks/plan.md`, in order of how much they change the product.

**7.1 — Promote contractor reconciliation from Phase 5 to Phase 2.**
It is built, it is unique, and it is the clearest ROI story we have. Deepen it:
per-line variance reasons, authorised override with mandatory reason, evidence
attachment, and the CLRA principal-employer register. Currently sitting in the
epics list at 6.x-ish priority; it should be near-first.

**7.2 — Reposition compliance as *evidence and control*, never as filing.**
Keep Phase 4 exactly as scoped — obligations, calendar, registers, challan
recording, reconciliation, evidence packages. Delete any language implying we
submit. Compete with "prove it in one click", not "we file it".

**7.3 — Move reconciliation forward from Phase 5.**
"Does every rupee have a destination?" is the validated differentiator, not a
late feature. The plan already carries reconciliation invariants per phase;
make the *user-visible* reconciliation surface part of Phase 3 rather than 5.

**7.4 — Make exceptions the product surface, not a panel.**
This is what actually kills the Excel sheet. It raises the priority of Task 1.6
(the attendance bridge) because unexplained absence is the single commonest
month-end exception, and of the Exceptions screen in `PAYROLL-UX-SPEC.md` §5.1.

**7.5 — Let TDS stay an input longer than planned.**
Phase 7 was already all-or-nothing. Given RazorpayX auto-files TDS at ₹30–100
PEPM, building a full TDS subsystem to reach parity is the worst
return-on-effort item on the roadmap. Defer until a customer's CA refuses
manual entry, which is the stated trigger.

**7.6 — Treat "Why?" as a headline feature, not polish.**
Nobody in this market sells explainability. We already store the whole chain —
inputs, wage basis, rule version, source. Surfacing L1→L4 is cheap for us and
structurally hard for anyone whose engine computes from a live salary field.
It also directly attacks the "support is slow when payroll breaks mid-month"
complaint: if HR can answer *why* without a ticket, the complaint never starts.

**7.7 — Segment deliberately, and write it down.**
Not "Indian SMEs". Something like: *multi-establishment and contractor-heavy
employers, 50–500 people, where payroll errors have a measurable rupee cost.*
That segment is under-served by mainstream HRMS, badly served by Excel, and can
justify a price above ₹0.

---

## 8. What NOT to change

- The engine architecture. Per-statute wage basis, effective-dated rules and
  `PayrollContext` are right regardless of positioning.
- Immutable finalized payroll and adjustments into later periods.
- Refusing to half-build TDS.
- The monochrome, exception-first UI. Every competitor screenshot in this
  research is a dense blue dashboard; not looking like them is an asset.

---

## 9. Honest risks in this analysis

- **Source quality is poor.** Vendor-published comparisons dominate the open
  web. The complaint table is the weakest section here.
- **No primary research.** Nobody has talked to a payroll operator. Every claim
  about what HR "actually does" is inference from secondary writing.
- **Pricing may be stale or tier-dependent** — published SaaS pricing in India
  is frequently a starting point for a sales conversation.
- **The contractor-variance gap may be a gap for a reason.** It is possible
  specialists solve it inside services engagements rather than software, and
  that buyers do not shop for it. **Testing this with three real
  contractor-heavy employers is worth more than any further building.**

---

**Sources:** [Keka vs greytHR (HROne)](https://hrone.cloud/blog/keka-vs-greythr-india/) ·
[greytHR alternatives (PocketHRMS)](https://www.pockethrms.com/greythr-alternatives/) ·
[RazorpayX statutory compliance](https://razorpay.com/payroll/payroll-compliance/) ·
[RazorpayX TDS docs](https://razorpay.com/docs/payroll/tds/) ·
[Payroll software pricing India 2026](https://www.itforsme.in/pricing/payroll-software-india/) ·
[HROne pricing guide](https://hrone.cloud/blog/payroll-software-price-in-india/) ·
[Payroll reconciliation guide (Asanify)](https://asanify.com/blog/human-resources/payroll-reconciliation-complete-guide-accurate-compliant-payroll-2026/) ·
[CLRA principal employer guide](https://tmservices.co.in/contract-labour-act-clra-compliance-employers-india/) ·
[BlueTree contract labour](https://www.getbluetree.com/manage-contract-labour) ·
[TeamLease RegTech labour automation](https://www.teamleaseregtech.com/product-services/labour_automation/)
