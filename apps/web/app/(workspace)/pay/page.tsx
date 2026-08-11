"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, RefreshCw, Settings2, ListChecks } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { Payslip } from "@/lib/types";
import { Action, Avatar, Empty, Pill, SectionLabel, Status } from "@/components/lamoon/primitives";
import { PayrollSetup } from "@/components/lamoon/payroll-setup";
import { PayrollExceptions } from "@/components/lamoon/payroll-exceptions";
import { PayrollReadiness } from "@/components/lamoon/payroll-readiness";
import { PayrollLedger } from "@/components/lamoon/payroll-ledger";
import { Input } from "@/components/ui/input";

/* Payroll.

   The page is built around the one thing that matters about a payroll run:
   it becomes irreversible. So the draft state is loud and editable, the
   finalized state is quiet and read-only, and the button between them says
   what it does rather than "Save".

   Amounts arrive as strings from NUMERIC columns and stay strings all the way
   to the DOM. Parsing them into JS numbers to format them would introduce
   exactly the float error the backend went to some trouble to avoid. */

/** ₹ with Indian digit grouping, from a decimal STRING. Never parseFloat. */
function rupees(amount: string): string {
  const [whole, paise = "00"] = amount.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = whole.replace("-", "");
  // Indian grouping: last three, then pairs. 1234567 -> 12,34,567
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${sign}₹${grouped}${paise === "00" ? "" : `.${paise}`}`;
}

function monthLabel(period: string): string {
  return new Date(period + "T00:00:00").toLocaleDateString([], {
    month: "long",
    year: "numeric",
  });
}

/** First of the current month, as the API wants it. */
function thisMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
}

/** `<input type="month">` speaks YYYY-MM; the API wants the first of it. */
function monthInputToPeriod(value: string): string {
  return `${value}-01`;
}

function periodToMonthInput(period: string): string {
  return period.slice(0, 7);
}

function LineTable({ lines }: { lines: { code: string; name: string; amount: string }[] }) {
  const shown = lines.filter((l) => l.amount !== "0" && l.amount !== "0.00");
  if (!shown.length) return <p className="t-meta">None</p>;
  return (
    <table className="w-full">
      <tbody>
        {shown.map((l) => (
          <tr key={l.code}>
            <td className="py-1 pr-4 text-[0.8125rem]">{l.name}</td>
            <td className="py-1 text-right text-[0.8125rem] tabular-nums">{rupees(l.amount)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PayslipDetail({
  slip,
  runId,
  editable,
}: {
  slip: Payslip;
  runId: string;
  editable: boolean;
}) {
  const qc = useQueryClient();
  const [tds, setTds] = useState(slip.tds);
  const [lop, setLop] = useState(String(slip.lop_days));
  const [tdsSource, setTdsSource] = useState(slip.tds_source ?? "");
  const [taxYear, setTaxYear] = useState(slip.tds_tax_year ?? "");
  const [error, setError] = useState<string | null>(null);

  const adjust = useMutation({
    mutationFn: () =>
      api.payroll.adjust(runId, slip.id, {
        tds,
        lop_days: Number(lop),
        tds_source: tdsSource || undefined,
        tds_tax_year: taxYear || undefined,
      }),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["payroll-run", runId] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save"),
  });

  return (
    <div className="mt-3 grid gap-6 rounded-2xl bg-[var(--surface-1)] p-5 sm:grid-cols-3">
      <div>
        <SectionLabel>Earnings</SectionLabel>
        <LineTable lines={slip.breakdown.earnings} />
        <p className="mt-2 border-t border-[var(--hairline)] pt-2 text-[0.8125rem] font-medium tabular-nums">
          Gross {rupees(slip.gross)}
        </p>
      </div>
      <div>
        <SectionLabel>Deductions</SectionLabel>
        <LineTable lines={slip.breakdown.deductions} />
        <p className="mt-2 border-t border-[var(--hairline)] pt-2 text-[0.8125rem] font-medium tabular-nums">
          Total {rupees(slip.deductions)}
        </p>
      </div>
      <div>
        <SectionLabel>Employer contribution</SectionLabel>
        <LineTable lines={slip.breakdown.employer_contributions} />
        <p className="t-meta mt-2 border-t border-[var(--hairline)] pt-2">
          Not deducted from pay. Cost to company {rupees(slip.employer_cost)}.
        </p>
      </div>

      <div className="sm:col-span-3 space-y-1">
        <p className="t-meta">
          {slip.breakdown.basis.proration} · PF wage {rupees(slip.breakdown.basis.pf_wage)} · ESI
          wage {rupees(slip.breakdown.basis.esi_wage)}
        </p>
        {/* The statutory wage is derived, not nominated. Showing the working is
            the difference between a number you can defend and one you can only
            reproduce. */}
        {slip.breakdown.basis.statutory_wage && (
          <p className="t-meta">
            Statutory wage {rupees(slip.breakdown.basis.statutory_wage)} — wages{" "}
            {rupees(slip.breakdown.basis.nominated_wages ?? "0")}
            {Number(slip.breakdown.basis.added_back ?? 0) > 0 && (
              <>
                {" "}+ {rupees(slip.breakdown.basis.added_back!)} added back, allowances
                above half of {rupees(slip.breakdown.basis.remuneration ?? "0")}
              </>
            )}
          </p>
        )}
        {slip.breakdown.basis.eps && (
          <p className="t-meta">Pension: {slip.breakdown.basis.eps}</p>
        )}
        {slip.breakdown.rule_versions && (
          <p className="t-meta">
            Computed under {slip.breakdown.rule_versions.wage_definition} ·{" "}
            {slip.breakdown.rule_versions.epf} · {slip.breakdown.rule_versions.esi}
          </p>
        )}
      </div>

      <div className="sm:col-span-3 border-t border-[var(--hairline)] pt-4">
        <PayrollLedger employeeId={slip.employee_id} period={slip.period} editable={editable} />
      </div>

      {editable && (
        <div className="sm:col-span-3 border-t border-[var(--hairline)] pt-4">
          <SectionLabel>Adjust</SectionLabel>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="t-micro">Income tax (TDS)</span>
              <Input
                value={tds}
                onChange={(e) => setTds(e.target.value)}
                inputMode="decimal"
                className="w-36 tabular-nums"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="t-micro">Advised by</span>
              <Input
                value={tdsSource}
                onChange={(e) => setTdsSource(e.target.value)}
                placeholder="Customer CA"
                className="w-40"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="t-micro">Tax year</span>
              <Input
                value={taxYear}
                onChange={(e) => setTaxYear(e.target.value)}
                placeholder="2026-27"
                className="w-24 tabular-nums"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="t-micro">Unpaid days</span>
              <Input
                value={lop}
                onChange={(e) => setLop(e.target.value)}
                inputMode="numeric"
                className="w-24 tabular-nums"
              />
            </label>
            <Action onClick={() => adjust.mutate()} disabled={adjust.isPending}>
              {adjust.isPending ? "Saving…" : "Apply"}
            </Action>
          </div>
          {/* The system computes unpaid days from approved unpaid leave. It
              cannot know about a mid-month exit, so an override sticks. */}
          {/* Provenance, not decoration: six months on somebody will ask why
              this figure was deducted, and the payslip has to answer. */}
          <p className="t-meta mt-2">
            TDS is not computed here — enter what your accountant advises, and record who
            advised it.
            {slip.tds_provided_at &&
              ` Last set ${new Date(slip.tds_provided_at).toLocaleDateString()}` +
                (slip.tds_source ? ` by ${slip.tds_source}` : "") + "."}
            {slip.lop_overridden && " Unpaid days were set by hand and survive a recompute."}
          </p>
          {error && <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">{error}</p>}
        </div>
      )}
    </div>
  );
}

export default function PayPage() {
  const qc = useQueryClient();
  const [openSlip, setOpenSlip] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [configuring, setConfiguring] = useState(false);
  // Payroll for a month is normally run after that month ends, so "the current
  // month" is the wrong default to be locked to — and once it's finalized the
  // button had no other period to offer.
  const [month, setMonth] = useState(periodToMonthInput(thisMonth()));
  const [rebuilt, setRebuilt] = useState<string | null>(null);
  const canWrite = hasPermission(useAuthStore((s) => s.permissions), "payroll.write");

  const runs = useQuery({ queryKey: ["payroll-runs"], queryFn: api.payroll.runs });
  const runId = selected ?? runs.data?.[0]?.id ?? null;
  const run = useQuery({
    queryKey: ["payroll-run", runId],
    queryFn: () => api.payroll.run(runId!),
    enabled: !!runId,
  });

  const compute = useMutation({
    mutationFn: (period: string) => api.payroll.compute(period),
    onSuccess: (detail) => {
      setError(null);
      setSelected(detail.id);
      setMonth(periodToMonthInput(detail.period));
      qc.invalidateQueries({ queryKey: ["payroll-runs"] });
      qc.invalidateQueries({ queryKey: ["payroll-run", detail.id] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not compute"),
  });

  const rebuild = useMutation({
    mutationFn: () => api.payroll.rebuild(monthInputToPeriod(month)),
    onSuccess: (r) => {
      setError(null);
      setRebuilt(
        `Ledger rebuilt for ${r.employees} people — ${r.derived} generated, ` +
          `${r.preserved} entered by hand kept` +
          (r.pending > 0 ? `, ${r.pending} awaiting approval.` : ".")
      );
      qc.invalidateQueries({ queryKey: ["payroll-validation"] });
      qc.invalidateQueries({ queryKey: ["payroll-inputs"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not rebuild"),
  });

  const finalize = useMutation({
    mutationFn: () => api.payroll.finalize(runId!),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["payroll-runs"] });
      qc.invalidateQueries({ queryKey: ["payroll-run", runId] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not finalize"),
  });

  const detail = run.data;
  const draft = detail?.status === "draft";

  return (
    <div className="fade mx-auto w-full max-w-5xl px-5 py-10 sm:px-8 sm:py-14">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="t-display">Pay</h1>
        {canWrite && (
          <div className="flex flex-wrap gap-2">
            <Action variant="quiet" onClick={() => setConfiguring((v) => !v)}>
              <Settings2 className="size-4" aria-hidden />
              Setup
            </Action>
            {/* Seeing what a period consists of and paying it are separate
                acts. This does the first without doing the second. */}
            <Action variant="quiet" onClick={() => rebuild.mutate()} disabled={rebuild.isPending}>
              <ListChecks className="size-4" aria-hidden />
              {rebuild.isPending ? "Rebuilding…" : "Rebuild inputs"}
            </Action>
            <label className="flex items-center gap-2">
              <span className="sr-only">Payroll month</span>
              <input
                type="month"
                value={month}
                onChange={(e) => setMonth(e.target.value)}
                className="rounded-[10px] bg-[var(--surface-2)] px-3 py-2 text-[0.8125rem]
                           tabular-nums outline-none focus-visible:ring-2
                           focus-visible:ring-[var(--lumo-base)]"
              />
            </label>
            <Action
              onClick={() => compute.mutate(monthInputToPeriod(month))}
              disabled={compute.isPending || !month}
            >
              <RefreshCw className="size-4" aria-hidden />
              {compute.isPending
                ? "Computing…"
                : `Run ${monthLabel(monthInputToPeriod(month))}`}
            </Action>
          </div>
        )}
      </header>

      {error && <p className="mt-4 text-[0.8125rem] text-[var(--critical)]">{error}</p>}
      {rebuilt && !error && <p className="t-meta mt-4">{rebuilt}</p>}

      {configuring && canWrite && (
        <div className="mt-6">
          <PayrollSetup />
        </div>
      )}

      {runs.data && runs.data.length > 1 && (
        <div className="mt-6 flex flex-wrap gap-2">
          {runs.data.map((r) => (
            <button
              key={r.id}
              onClick={() => {
                setSelected(r.id);
                setMonth(periodToMonthInput(r.period));
              }}
              aria-pressed={r.id === runId}
              className={`rounded-full px-3 py-1.5 text-[0.8125rem] transition-colors ${
                r.id === runId
                  ? "bg-[var(--ink-1)] text-[var(--surface-0)]"
                  : "bg-[var(--surface-2)] hover:bg-[var(--surface-3)]"
              }`}
            >
              {monthLabel(r.period)}
            </button>
          ))}
        </div>
      )}

      {!runs.isLoading && !runs.data?.length && (
        <div className="mt-10">
          <Empty>
            No payroll has been run yet. Set up salary structures, then run this month.
          </Empty>
        </div>
      )}

      {detail && (
        <>
          <section className="mt-8 flex flex-wrap items-end gap-x-10 gap-y-4">
            <div>
              <span className="t-micro block">Net pay</span>
              <span className="text-[1.75rem] font-medium tabular-nums">
                {rupees(detail.net_total)}
              </span>
            </div>
            <div>
              <span className="t-micro block">Gross</span>
              <span className="text-[1.125rem] tabular-nums">{rupees(detail.gross_total)}</span>
            </div>
            <div>
              <span className="t-micro block">Deductions</span>
              <span className="text-[1.125rem] tabular-nums">
                {rupees(detail.deductions_total)}
              </span>
            </div>
            <div>
              <span className="t-micro block">Cost to company</span>
              <span className="text-[1.125rem] tabular-nums">
                {rupees(detail.employer_cost_total)}
              </span>
              {Number(detail.admin_shortfall) > 0 && (
                <span className="t-meta block">
                  incl. {rupees(detail.admin_shortfall)} PF admin minimum
                </span>
              )}
            </div>
            <div className="ml-auto flex items-center gap-3">
              {draft ? (
                <>
                  <Status tone="caution">Draft</Status>
                  <Action onClick={() => finalize.mutate()} disabled={finalize.isPending}>
                    <Lock className="size-4" aria-hidden />
                    {finalize.isPending ? "Finalizing…" : "Finalize"}
                  </Action>
                </>
              ) : (
                <Pill>
                  <Lock className="size-3" aria-hidden /> Finalized
                </Pill>
              )}
            </div>
          </section>

          {draft && (
            /* Said plainly, because the button above cannot be undone. */
            <p className="t-meta mt-3">
              Finalizing freezes these numbers. Corrections after that have to be made in a
              later month, the same way payroll works on paper.
            </p>
          )}

          <div className="mt-12">
            <PayrollReadiness period={detail.period} />
          </div>

          <div className="mt-12">
            <PayrollExceptions period={detail.period} />
          </div>

          <div className="mt-12">
            <SectionLabel>
              {detail.payslips.length} payslip{detail.payslips.length === 1 ? "" : "s"} ·{" "}
              {monthLabel(detail.period)}
            </SectionLabel>
          </div>

          <ul className="mt-3 divide-y divide-[var(--hairline)]">
            {detail.payslips.map((slip) => (
              <li key={slip.id} className="py-3">
                <button
                  onClick={() => setOpenSlip(openSlip === slip.id ? null : slip.id)}
                  aria-expanded={openSlip === slip.id}
                  className="flex w-full items-center gap-3 text-left"
                >
                  <Avatar name={slip.employee_name} size={36} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[0.9375rem]">{slip.employee_name}</span>
                    <span className="t-meta">
                      {slip.paid_days}/{slip.working_days} days
                      {slip.lop_days > 0 && ` · ${slip.lop_days} unpaid`}
                    </span>
                  </span>
                  <span className="text-right">
                    <span className="block text-[0.9375rem] tabular-nums">
                      {rupees(slip.net)}
                    </span>
                    <span className="t-meta">net</span>
                  </span>
                </button>
                {openSlip === slip.id && (
                  <PayslipDetail slip={slip} runId={detail.id} editable={draft} />
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
