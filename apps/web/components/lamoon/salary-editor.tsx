"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Action, SectionLabel } from "@/components/lamoon/primitives";
import { Input } from "@/components/ui/input";

/* One person's salary structure, plus their payslip history.

   Lives on the person's page rather than in a payroll screen because that's
   where the question is asked — you look someone up and want to know what
   they're paid. It's gated on payroll.read, which HR and admin hold and
   managers deliberately do not.

   Amounts are strings from NUMERIC columns and stay strings. The gross shown
   here is the SERVER's, echoed back after a save, so this component never
   becomes a second place that adds up money. */

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
  const { data: structure } = useQuery({
    queryKey: ["salary", employeeId],
    queryFn: () => api.payroll.salary(employeeId),
  });
  const { data: payslips } = useQuery({
    queryKey: ["employee-payslips", employeeId],
    queryFn: () => api.payroll.employeePayslips(employeeId),
  });

  /** Local state holds ONLY what the operator has typed. Everything else
   *  reads straight from the server copy, so there is no effect syncing two
   *  sources of truth — and a refetch can't silently overwrite an edit in
   *  progress or vice versa. Blank means "not part of this salary": the
   *  structure is replaced wholesale on save, so clearing a field removes it. */
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const serverAmounts: Record<string, string> = Object.fromEntries(
    (structure?.components ?? []).map((c) => [c.component_id, c.amount])
  );
  const amountFor = (componentId: string) =>
    edits[componentId] ?? serverAmounts[componentId] ?? "";

  const save = useMutation({
    mutationFn: () =>
      api.payroll.setSalary(
        employeeId,
        (components ?? [])
          .map((c) => ({ component_id: c.id, amount: amountFor(c.id) }))
          .filter((c) => c.amount.trim() !== "" && Number(c.amount) !== 0)
      ),
    onSuccess: () => {
      setError(null);
      setSaved(true);
      setEdits({}); // server copy is now authoritative again
      qc.invalidateQueries({ queryKey: ["salary", employeeId] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save"),
  });

  if (!components?.length) {
    return (
      <>
        <SectionLabel>Salary</SectionLabel>
        <p className="t-meta">
          No pay components are set up yet. Add them under Pay → Setup, then a salary can be
          built from them.
        </p>
      </>
    );
  }

  return (
    <>
      <SectionLabel>Salary</SectionLabel>

      <div className="flex flex-wrap items-end gap-3">
        {components.map((c) => (
          <div key={c.id} className="space-y-1.5">
            <Label>
              {c.name}
              {c.pf_wage && <span className="ml-1 text-[var(--ink-4)]">· PF</span>}
              {c.kind === "deduction" && <span className="ml-1 text-[var(--ink-4)]">· ded</span>}
            </Label>
            <Input
              value={amountFor(c.id)}
              onChange={(e) => {
                setSaved(false);
                setEdits({ ...edits, [c.id]: e.target.value });
              }}
              disabled={!canWrite}
              inputMode="decimal"
              placeholder="0"
              className="w-32 tabular-nums"
            />
          </div>
        ))}
        {canWrite && (
          <Action onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save salary"}
          </Action>
        )}
      </div>

      <p className="t-meta mt-2">
        {structure && Number(structure.monthly_gross) > 0 ? (
          <>
            Monthly gross <span className="tabular-nums">{rupees(structure.monthly_gross)}</span>.
            Changes apply to future runs — payslips already finalized keep their own numbers.
          </>
        ) : (
          <>{firstName} has no salary structure yet, so payroll will compute nothing for them.</>
        )}
        {saved && " Saved."}
      </p>
      {error && <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">{error}</p>}

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

/** Local label so this component doesn't depend on the form-kit's `htmlFor`
 *  contract for what are really just captions. */
function Label({ children }: { children: React.ReactNode }) {
  return <span className="t-micro block">{children}</span>;
}
