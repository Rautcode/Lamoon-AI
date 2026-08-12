"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { Reconciliation } from "@/lib/types";
import { Action, SectionLabel } from "@/components/lamoon/primitives";
import { Input } from "@/components/ui/input";

/* Contractor reconciliation.

   What attendance says a contractor is owed, against what they billed. That
   gap is the whole section — billing for days nobody worked is the commonest
   leak in site payroll and is invisible until the two figures sit next to
   each other.

   Variance is the ONE column here carrying semantic colour, because it is the
   only one that requires a decision. Everything else is a fact.

   The section renders only when contractors exist, so a company with none
   sees nothing rather than an empty frame explaining a concept it doesn't
   use. */

function rupees(amount: string): string {
  const [whole, paise = "00"] = amount.split(".");
  const negative = whole.startsWith("-");
  const digits = whole.replace("-", "");
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${negative ? "−" : ""}₹${grouped}${paise === "00" ? "" : `.${paise}`}`;
}

function signed(amount: string): string {
  return amount.startsWith("-") ? rupees(amount) : `+${rupees(amount)}`;
}

function Row({
  row,
  editable,
  onChanged,
}: {
  row: Reconciliation;
  editable: boolean;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState(row.invoiced ?? "");
  const [reference, setReference] = useState(row.invoice_reference ?? "");
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) =>
    setError(e instanceof ApiError ? e.message : "Something failed");
  const done = () => {
    setError(null);
    onChanged();
  };

  const record = useMutation({
    mutationFn: () =>
      api.payroll.recordInvoice({
        contractor_id: row.contractor_id,
        period: row.period,
        amount,
        reference: reference || undefined,
      }),
    onSuccess: done,
    onError: fail,
  });
  const approve = useMutation({
    mutationFn: () => api.payroll.approveInvoice(row.invoice_id!),
    onSuccess: done,
    onError: fail,
  });
  const dispute = useMutation({
    mutationFn: () => api.payroll.disputeInvoice(row.invoice_id!),
    onSuccess: done,
    onError: fail,
  });

  const variance = row.variance === null ? null : Number(row.variance);
  const agreed = row.invoice_status === "approved";

  return (
    <>
      <tr className="border-b border-[var(--hairline)]">
        <td className="py-2.5 pr-3">
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="text-left text-[0.875rem] hover:underline"
          >
            {row.contractor_name}
          </button>
          {(row.workers_without_pay > 0 || row.days_awaiting_approval > 0) && (
            <span className="t-meta block">
              {row.workers_without_pay > 0 &&
                `${row.workers_without_pay} deployed but unpaid`}
              {row.workers_without_pay > 0 && row.days_awaiting_approval > 0 && " · "}
              {row.days_awaiting_approval > 0 &&
                `${row.days_awaiting_approval} days awaiting approval`}
            </span>
          )}
        </td>
        <td className="py-2.5 text-right tabular-nums">{row.workers}</td>
        <td className="py-2.5 text-right tabular-nums">{rupees(row.computed)}</td>
        <td className="py-2.5 text-right tabular-nums">
          {row.invoiced === null ? (
            <span className="t-meta">not billed</span>
          ) : (
            rupees(row.invoiced)
          )}
        </td>
        <td
          className={`py-2.5 text-right tabular-nums ${
            variance ? "text-[var(--critical)]" : ""
          }`}
        >
          {variance === null ? "—" : variance === 0 ? "matches" : signed(row.variance!)}
        </td>
        <td className="py-2.5 pl-3 text-right">
          <span className="t-meta">{agreed ? "approved" : row.invoice_status ?? ""}</span>
        </td>
      </tr>

      {open && (
        <tr>
          <td colSpan={6} className="pb-5">
            <div className="rounded-[12px] bg-[var(--surface-1)] p-4">
              {editable && !agreed && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    record.mutate();
                  }}
                  className="mb-4 flex flex-wrap items-end gap-3"
                >
                  <label className="space-y-1.5">
                    <span className="t-micro block">Invoice amount</span>
                    <Input
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      inputMode="decimal"
                      placeholder={row.computed}
                      className="w-36 tabular-nums"
                      required
                    />
                  </label>
                  <label className="space-y-1.5">
                    <span className="t-micro block">Reference</span>
                    <Input
                      value={reference}
                      onChange={(e) => setReference(e.target.value)}
                      placeholder="ABC/2026/09"
                      className="w-36"
                    />
                  </label>
                  <Action type="submit" variant="quiet" disabled={record.isPending}>
                    {row.invoice_id ? "Update" : "Record invoice"}
                  </Action>
                  {row.invoice_id && (
                    <>
                      <Action onClick={() => approve.mutate()} disabled={approve.isPending}>
                        Approve
                      </Action>
                      <Action
                        variant="ghost"
                        onClick={() => dispute.mutate()}
                        disabled={dispute.isPending}
                      >
                        Dispute
                      </Action>
                    </>
                  )}
                </form>
              )}

              {/* What somebody actually takes back to the contractor. */}
              <div className="overflow-x-auto">
                <table className="w-full min-w-[440px] text-[0.8125rem]">
                  <thead>
                    <tr className="border-b border-[var(--hairline)]">
                      <th className="py-1.5 text-left font-normal t-micro">Worker</th>
                      <th className="py-1.5 text-left font-normal t-micro">Site</th>
                      <th className="py-1.5 text-right font-normal t-micro">Days</th>
                      <th className="py-1.5 text-right font-normal t-micro">OT</th>
                      <th className="py-1.5 text-right font-normal t-micro">Computed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row.lines.map((l) => (
                      <tr key={l.employee_id} className="border-b border-[var(--hairline)] last:border-0">
                        <td className="py-1.5">
                          {l.name}
                          {!l.has_payslip && (
                            <span className="t-meta block text-[var(--caution)]">
                              deployed but not paid
                            </span>
                          )}
                        </td>
                        <td className="py-1.5">{l.site ?? "—"}</td>
                        <td className="py-1.5 text-right tabular-nums">
                          {l.days_approved}
                          {l.days_pending > 0 && (
                            <span className="text-[var(--caution)]"> +{l.days_pending}</span>
                          )}
                        </td>
                        <td className="py-1.5 text-right tabular-nums">
                          {Number(l.overtime_hours) ? `${Number(l.overtime_hours)}h` : "—"}
                        </td>
                        <td className="py-1.5 text-right tabular-nums">
                          {rupees(l.computed)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {error && (
                <p className="mt-3 text-[0.8125rem] text-[var(--critical)]">{error}</p>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function PayrollContractors({
  period,
  editable,
}: {
  period: string;
  editable: boolean;
}) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["payroll-reconciliation", period],
    queryFn: () => api.payroll.reconciliation(period),
  });

  // Nothing to say to a company that uses no contractors.
  if (!data?.length) return null;

  const disagreeing = data.filter(
    (r) => r.variance !== null && Number(r.variance) !== 0
  ).length;

  return (
    <div>
      <SectionLabel>Contractors · {data.length}</SectionLabel>
      {disagreeing > 0 && (
        <p className="t-meta mb-3">
          {disagreeing} {disagreeing === 1 ? "invoice disagrees" : "invoices disagree"} with
          attendance. An invoice can be recorded whatever it says, and approved only when
          it matches.
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-[0.8125rem]">
          <thead>
            <tr className="border-b border-[var(--rule,var(--hairline))]">
              <th className="py-2 text-left font-normal t-micro">Contractor</th>
              <th className="py-2 text-right font-normal t-micro">Workers</th>
              <th className="py-2 text-right font-normal t-micro">Attendance says</th>
              <th className="py-2 text-right font-normal t-micro">Invoiced</th>
              <th className="py-2 text-right font-normal t-micro">Variance</th>
              <th className="py-2 pl-3 text-right font-normal t-micro">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <Row
                key={r.contractor_id}
                row={r}
                editable={editable}
                onChanged={() =>
                  qc.invalidateQueries({ queryKey: ["payroll-reconciliation", period] })
                }
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
