"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Action, SectionLabel } from "@/components/lamoon/primitives";
import { Input } from "@/components/ui/input";

/* One person's compensation, its history, and their payslips.

   Salary used to be a field you overwrote. It is now a TIMELINE: each version
   carries the dates it is true for, and payroll resolves the one that applied
   to the period being run. That difference is the whole point — a raise dated
   next month must not change this month's pay, and a correction dated into a
   finalized month must not rewrite a payslip that has already been issued.

   So this screen shows what applies NOW, and every version behind it. Changing
   pay asks for a date, because "from when" is the question the old editor
   never asked and payroll cannot answer without.

   Amounts are strings from NUMERIC columns and stay strings. The gross shown
   is the SERVER's, so this component never becomes a second place that adds
   up money. */

function rupees(amount: string): string {
  const [whole, paise = "00"] = amount.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = whole.replace("-", "");
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${sign}₹${grouped}${paise === "00" ? "" : `.${paise}`}`;
}

function payMonth(period: string): string {
  return new Date(period + "T00:00:00").toLocaleDateString([], {
    month: "short",
    year: "numeric",
  });
}

/** The sentinel the back-fill uses when a salary predates any record of when
 *  it started. Showing "1 Jan 2000" reads as a bug; saying we don't know is
 *  both truer and less alarming. */
const NOT_RECORDED = "2000-01-01";

function longDate(day: string): string {
  return new Date(day + "T00:00:00").toLocaleDateString([], {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Words an operator uses, not the stored key. */
const REASON: Record<string, string> = {
  hire: "Joining",
  revision: "Salary revision",
  promotion: "Promotion",
  correction: "Correction",
  migration: "Existing salary",
  f_and_f: "Full and final",
};

export function SalaryEditor({
  employeeId,
  firstName,
  canWrite,
}: {
  employeeId: string;
  firstName: string;
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const { data: components } = useQuery({
    queryKey: ["pay-components"],
    queryFn: api.payroll.components,
  });
  const { data: versions } = useQuery({
    queryKey: ["compensation", employeeId],
    queryFn: () => api.compensation.versions(employeeId),
  });
  const { data: payslips } = useQuery({
    queryKey: ["employee-payslips", employeeId],
    queryFn: () => api.payroll.employeePayslips(employeeId),
  });

  const [open, setOpen] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [reason, setReason] = useState("revision");
  const [error, setError] = useState<string | null>(null);

  // Newest first from the server; the one in force is the first whose span
  // covers today, which for an ordered timeline is simply the open one.
  const current = versions?.find((v) => v.effective_to === null) ?? versions?.[0];

  const amountFor = (componentId: string) =>
    edits[componentId] ??
    current?.lines.find((l) => l.component_id === componentId)?.amount ??
    "";

  const save = useMutation({
    mutationFn: () =>
      api.compensation.addVersion(employeeId, {
        effective_from: effectiveFrom,
        reason,
        lines: (components ?? [])
          .map((c) => ({ component_id: c.id, amount: amountFor(c.id) }))
          .filter((c) => c.amount.trim() !== "" && Number(c.amount) !== 0),
      }),
    onSuccess: () => {
      setError(null);
      setEdits({});
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["compensation", employeeId] });
      qc.invalidateQueries({ queryKey: ["salary", employeeId] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save"),
  });

  if (!components?.length) {
    return (
      <>
        <SectionLabel>Compensation</SectionLabel>
        <p className="t-meta">
          No pay components are set up yet. Add them under Pay → Setup, then a salary can be
          built from them.
        </p>
      </>
    );
  }

  return (
    <>
      <SectionLabel>Compensation</SectionLabel>

      {current ? (
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <span className="text-[1.375rem] tabular-nums">{rupees(current.gross)}</span>
          <span className="t-meta">per month</span>
          <span className="t-meta">
            {current.effective_from === NOT_RECORDED
              ? "In force · start date not recorded"
              : `Effective ${longDate(current.effective_from)}`}
          </span>
        </div>
      ) : (
        <p className="t-meta">
          {firstName} has no compensation on record, so payroll will compute nothing for them.
        </p>
      )}

      {current && (
        <div className="mt-3 flex flex-wrap gap-x-8 gap-y-2">
          {current.lines.map((l) => (
            <span key={l.component_id} className="text-[0.8125rem]">
              <span className="t-meta block">{l.name}</span>
              <span className="tabular-nums">{rupees(l.amount)}</span>
            </span>
          ))}
        </div>
      )}

      {canWrite && !open && (
        <div className="mt-4">
          <Action variant="quiet" onClick={() => setOpen(true)}>
            {current ? "Change compensation" : "Set compensation"}
          </Action>
        </div>
      )}

      {canWrite && open && (
        <form
          className="mt-4 rounded-[12px] bg-[var(--surface-1)] p-4"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="space-y-1.5">
              <span className="t-micro block">Effective from</span>
              <Input
                type="date"
                value={effectiveFrom}
                onChange={(e) => setEffectiveFrom(e.target.value)}
                className="w-40"
                required
              />
            </label>
            <label className="space-y-1.5">
              <span className="t-micro block">Reason</span>
              <select
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="h-9 rounded-[8px] border border-[var(--hairline)] bg-transparent px-2 text-[0.875rem]"
              >
                <option value="revision">Salary revision</option>
                <option value="promotion">Promotion</option>
                <option value="correction">Correction</option>
                <option value="hire">Joining</option>
              </select>
            </label>
          </div>

          <div className="mt-4 flex flex-wrap items-end gap-3">
            {components.map((c) => (
              <div key={c.id} className="space-y-1.5">
                <span className="t-micro block">
                  {c.name}
                  {c.pf_wage && <span className="ml-1 text-[var(--ink-4)]">· PF</span>}
                  {c.kind === "deduction" && (
                    <span className="ml-1 text-[var(--ink-4)]">· ded</span>
                  )}
                </span>
                <Input
                  value={amountFor(c.id)}
                  onChange={(e) => setEdits({ ...edits, [c.id]: e.target.value })}
                  inputMode="decimal"
                  placeholder="0"
                  className="w-32 tabular-nums"
                />
              </div>
            ))}
          </div>

          <div className="mt-4 flex items-center gap-3">
            <Action type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save revision"}
            </Action>
            <Action variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Action>
          </div>

          <p className="t-meta mt-3">
            {/* The two things that surprise people, said before they happen
                rather than discovered afterwards. */}
            A date inside a month that has already been finalized does not change those
            payslips — they are frozen, and the difference is owed as arrears. A date
            mid-month splits that month between the old and new amounts.
          </p>
          {error && <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">{error}</p>}
        </form>
      )}

      {versions && versions.length > 1 && (
        <div className="mt-6">
          <SectionLabel>History</SectionLabel>
          <table className="w-full">
            <tbody>
              {versions.map((v) => (
                <tr key={v.id} className="border-b border-[var(--hairline)] last:border-0">
                  <td className="py-2 pr-3 text-[0.8125rem]">
                    {v.effective_from === NOT_RECORDED
                      ? "Not recorded"
                      : longDate(v.effective_from)}
                    <span className="t-meta">
                      {v.effective_to ? ` – ${longDate(v.effective_to)}` : " – current"}
                    </span>
                  </td>
                  <td className="py-2 pr-3 t-meta">{REASON[v.reason] ?? v.reason}</td>
                  <td className="py-2 text-right text-[0.8125rem] tabular-nums">
                    {rupees(v.gross)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {payslips && payslips.length > 0 && (
        <div className="mt-6">
          <SectionLabel>Payslips</SectionLabel>
          <div className="-mx-3">
            {payslips.map((p) => (
              <div
                key={p.id}
                className="flex items-center gap-4 rounded-[10px] px-3 py-2.5 hover:bg-[var(--surface-1)]"
              >
                <span className="min-w-0 flex-1 text-[0.875rem]">
                  {payMonth(p.period)}
                  <span className="text-[var(--ink-3)]">
                    {" · "}
                    {p.paid_days}/{p.working_days} days
                    {p.lop_days > 0 && ` · ${p.lop_days} unpaid`}
                  </span>
                </span>
                <span className="shrink-0 text-[0.875rem] tabular-nums">{rupees(p.net)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
