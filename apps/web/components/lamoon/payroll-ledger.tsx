"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { PayrollInput } from "@/lib/types";
import { Action, SectionLabel } from "@/components/lamoon/primitives";
import { useState } from "react";

/* One person's payroll inputs for one period.

   The point of showing this at all: payroll does not pay somebody's salary, it
   pays the inputs approved for the period. When a figure is queried, the
   answer is here — what it was, where it came from, and whether a human signed
   it off. A payslip line with no provenance is not something anyone can
   defend six months later. */

function rupees(amount: string): string {
  const [whole, paise = "00"] = amount.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = whole.replace("-", "");
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${sign}₹${grouped}${paise === "00" ? "" : `.${paise}`}`;
}

/** Where a figure came from, in words an operator uses rather than the stored
 *  key. "structure" is not a thing anybody says out loud. */
const SOURCE: Record<PayrollInput["source"], string> = {
  structure: "from salary",
  work_facts: "from approved work",
  manual: "entered by hand",
  import: "imported",
  adjustment: "correction",
};

export function PayrollLedger({
  employeeId,
  period,
  editable,
}: {
  employeeId: string;
  period: string;
  editable: boolean;
}) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const { data: inputs, isLoading } = useQuery({
    queryKey: ["payroll-inputs", employeeId, period],
    queryFn: () => api.payroll.inputs(employeeId, period),
  });

  const approve = useMutation({
    mutationFn: (ids: string[]) => api.payroll.approveInputs(ids),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["payroll-inputs", employeeId, period] });
      qc.invalidateQueries({ queryKey: ["payroll-run"] });
      qc.invalidateQueries({ queryKey: ["payroll-validation", period] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not approve"),
  });

  if (isLoading) return <p className="t-meta">Loading inputs…</p>;
  if (!inputs?.length) {
    return (
      <p className="t-meta">
        No inputs for this period yet. Rebuilding the ledger generates them from the
        salary structure and any approved work.
      </p>
    );
  }

  // Structure rows need no separate sign-off — approving a salary IS the
  // approval. Anything asserted about this particular period does.
  const pending = inputs.filter((i) => i.source !== "structure" && !i.approved_at);

  return (
    <div>
      <SectionLabel>Inputs for this period</SectionLabel>
      <table className="w-full">
        <tbody>
          {inputs.map((i) => (
            <tr key={i.id} className="border-b border-[var(--hairline)] last:border-0">
              <td className="py-2 pr-3 text-[0.8125rem]">
                {i.name}
                {i.quantity && i.rate && (
                  <span className="t-meta block">
                    {i.quantity} × {rupees(i.rate)}
                  </span>
                )}
              </td>
              <td className="py-2 pr-3">
                <span className="t-meta">{SOURCE[i.source]}</span>
                {i.source !== "structure" && !i.approved_at && (
                  <span className="t-meta block text-[var(--caution)]">
                    awaiting approval — not paid
                  </span>
                )}
                {i.reason && <span className="t-meta block">{i.reason}</span>}
              </td>
              <td
                className={`py-2 text-right text-[0.8125rem] tabular-nums ${
                  i.source !== "structure" && !i.approved_at ? "text-[var(--ink-4)]" : ""
                }`}
              >
                {i.kind === "deduction" || i.kind === "tax" ? "−" : ""}
                {rupees(i.amount)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {pending.length > 0 && editable && (
        <div className="mt-3">
          <Action size="sm" onClick={() => approve.mutate(pending.map((p) => p.id))}
            disabled={approve.isPending}>
            {approve.isPending
              ? "Approving…"
              : `Approve ${pending.length} pending`}
          </Action>
          <p className="t-meta mt-2">
            Until approved these are claims, not costs — payroll will not pay them.
          </p>
        </div>
      )}
      {error && <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">{error}</p>}
    </div>
  );
}
