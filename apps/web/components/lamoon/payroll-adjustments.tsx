"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { Adjustment } from "@/lib/types";
import { Action, SectionLabel } from "@/components/lamoon/primitives";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/* Corrections carried in from an earlier, finalized month.

   This lives on the OPEN period rather than on the closed one, because that is
   where the money lands. April is finished; the correction to April is part of
   May's payroll, and reviewing May means seeing it.

   Raising one writes down a claim. APPROVING it is what creates the ledger row
   and changes somebody's pay — the two are separate so a mistake can be
   recorded by whoever spotted it without that person also being able to pay
   it. */

function rupees(amount: string): string {
  const [whole, paise = "00"] = amount.split(".");
  const digits = whole.replace("-", "");
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `₹${grouped}${paise === "00" ? "" : `.${paise}`}`;
}

function monthLabel(period: string): string {
  return new Date(period + "T00:00:00").toLocaleDateString([], {
    month: "long",
    year: "numeric",
  });
}

function toPeriod(monthValue: string): string {
  return `${monthValue}-01`;
}

function Row({
  adjustment,
  employeeName,
  editable,
  onApprove,
  onCancel,
  busy,
}: {
  adjustment: Adjustment;
  employeeName: string;
  editable: boolean;
  onApprove: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const applied = adjustment.applied_input_id !== null;
  return (
    <li className="flex flex-wrap items-start gap-x-4 gap-y-2 border-b border-[var(--hairline)] py-3 last:border-0">
      <span className="min-w-0 flex-1">
        <span className="block text-[0.875rem]">
          {employeeName}
          <span className="text-[var(--ink-3)]"> · {adjustment.name}</span>
        </span>
        <span className="t-meta">{adjustment.reason}</span>
        {!applied && (
          /* Said plainly: writing it down is not the same as paying it. */
          <span className="t-meta block text-[var(--caution)]">
            Not approved — this is not in the payroll yet
          </span>
        )}
      </span>
      <span className="shrink-0 text-right">
        <span className="block text-[0.875rem] tabular-nums">
          {adjustment.kind === "recovery" ? "−" : "+"}
          {rupees(adjustment.amount)}
        </span>
        <span className="t-meta">from {monthLabel(adjustment.source_period)}</span>
      </span>
      {editable && (
        <span className="flex shrink-0 gap-2">
          {!applied && (
            <Action size="sm" onClick={onApprove} disabled={busy}>
              Approve
            </Action>
          )}
          <Action variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
            {applied ? "Withdraw" : "Discard"}
          </Action>
        </span>
      )}
    </li>
  );
}

export function PayrollAdjustments({
  period,
  editable,
}: {
  period: string;
  editable: boolean;
}) {
  const qc = useQueryClient();
  const [raising, setRaising] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [employeeId, setEmployeeId] = useState("");
  const [sourceMonth, setSourceMonth] = useState(period.slice(0, 7));
  const [kind, setKind] = useState<"arrear" | "recovery">("arrear");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");

  const { data: adjustments } = useQuery({
    queryKey: ["payroll-adjustments", period],
    queryFn: () => api.payroll.adjustments(period),
  });
  const { data: employees } = useQuery({
    queryKey: ["employees"],
    queryFn: () => api.employees.list(),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["payroll-adjustments", period] });
    qc.invalidateQueries({ queryKey: ["payroll-run"] });
    qc.invalidateQueries({ queryKey: ["payroll-inputs"] });
    qc.invalidateQueries({ queryKey: ["payroll-movement", period] });
  };

  const raise = useMutation({
    mutationFn: () =>
      api.payroll.raiseAdjustment({
        employee_id: employeeId,
        source_period: toPeriod(sourceMonth),
        target_period: period,
        kind,
        amount,
        reason,
      }),
    onSuccess: () => {
      setError(null);
      setRaising(false);
      setAmount("");
      setReason("");
      refresh();
    },
    onError: (e) =>
      setError(e instanceof ApiError ? e.message : "Could not raise that correction"),
  });

  const approve = useMutation({
    mutationFn: (id: string) => api.payroll.approveAdjustment(id),
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not approve"),
  });

  const cancel = useMutation({
    mutationFn: (id: string) => api.payroll.cancelAdjustment(id),
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not withdraw"),
  });

  const name = (id: string) =>
    employees?.find((e) => e.id === id)?.full_name ?? "Unknown";
  const busy = approve.isPending || cancel.isPending;

  return (
    <div>
      <SectionLabel
        action={
          editable ? (
            <Action size="sm" variant="quiet" onClick={() => setRaising((v) => !v)}>
              {raising ? "Cancel" : "Raise correction"}
            </Action>
          ) : undefined
        }
      >
        Corrections carried in
        {adjustments?.length ? ` · ${adjustments.length}` : ""}
      </SectionLabel>

      {raising && editable && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            raise.mutate();
          }}
          className="surface-raised pop mb-5 flex flex-wrap items-end gap-3 p-4"
        >
          <label className="space-y-1.5">
            <span className="t-micro block">Employee</span>
            <select
              value={employeeId}
              onChange={(e) => setEmployeeId(e.target.value)}
              required
              className="rounded-[9px] bg-[var(--surface-2)] px-3 py-2 text-[0.8125rem] outline-none focus-visible:ring-2 focus-visible:ring-[var(--lumo-base)]"
            >
              <option value="">Choose…</option>
              {employees?.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.full_name}
                </option>
              ))}
            </select>
          </label>

          <div className="space-y-1.5">
            <Label htmlFor="adj-src" className="t-micro">
              Month being corrected
            </Label>
            <Input
              id="adj-src"
              type="month"
              value={sourceMonth}
              onChange={(e) => setSourceMonth(e.target.value)}
              className="tabular-nums"
              required
            />
          </div>

          <button
            type="button"
            onClick={() => setKind(kind === "arrear" ? "recovery" : "arrear")}
            aria-pressed={kind === "arrear"}
            className="rounded-[9px] bg-[var(--surface-2)] px-3 py-2 text-[0.8125rem]"
          >
            {kind === "arrear" ? "Owed to them" : "Recover from them"}
          </button>

          <div className="space-y-1.5">
            <Label htmlFor="adj-amt" className="t-micro">
              Amount
            </Label>
            <Input
              id="adj-amt"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              placeholder="2400.00"
              className="w-32 tabular-nums"
              required
            />
          </div>

          <div className="min-w-[220px] flex-1 space-y-1.5">
            <Label htmlFor="adj-why" className="t-micro">
              Why
            </Label>
            <Input
              id="adj-why"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="2 days unpaid deducted in error"
              required
            />
          </div>

          <Action type="submit" disabled={raise.isPending}>
            {raise.isPending ? "Recording…" : "Record"}
          </Action>

          <p className="t-meta w-full">
            The month being corrected must already be finalized — if it is still open,
            change it there instead. Recording this moves no money; approving it does.
          </p>
        </form>
      )}

      {adjustments?.length ? (
        <ul>
          {adjustments.map((a) => (
            <Row
              key={a.id}
              adjustment={a}
              employeeName={name(a.employee_id)}
              editable={editable}
              busy={busy}
              onApprove={() => approve.mutate(a.id)}
              onCancel={() => cancel.mutate(a.id)}
            />
          ))}
        </ul>
      ) : (
        <p className="t-meta">
          Nothing carried in from an earlier month. Corrections to a finalized period
          appear here, in the month they are settled.
        </p>
      )}

      {error && <p className="mt-3 text-[0.8125rem] text-[var(--critical)]">{error}</p>}
    </div>
  );
}
