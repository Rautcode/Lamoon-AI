"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { PayrollSettings, PTSlab } from "@/lib/types";
import { Action, Pill, SectionLabel } from "@/components/lamoon/primitives";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/* Payroll setup — the three things that must be right BEFORE a run, because
   every one of them changes what gets deducted from somebody's salary.

   Grouped on one panel rather than scattered into a settings area: they are
   read together ("is our payroll configured?") and they are wrong together.

   The panel is deliberately explicit about consequence. Toggling PF on is one
   click and changes every future payslip, so the copy says what it does
   instead of leaving the operator to find out on the first run. */

const DAYS_NOTE =
  "Registration is mandatory at 20+ employees for PF and 10+ for ESI. Leave a scheme off if you aren't registered — deducting for one you can't remit is worse than not deducting.";

function Toggle({
  on,
  onClick,
  children,
  disabled,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={on}
      className={`rounded-[9px] px-3 py-1.5 text-[0.8125rem] transition-colors ${
        on
          ? "bg-[var(--ink-1)] text-[var(--surface-0)]"
          : "bg-[var(--surface-2)] text-[var(--ink-3)]"
      }`}
    >
      {children}
    </button>
  );
}

function StatutorySchemes() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["payroll-settings"], queryFn: api.payroll.settings });
  // Local state holds ONLY unsaved keystrokes (the ceilings, which shouldn't
  // fire a request per character). Everything else reads from the server copy,
  // so there's no effect keeping two versions of the truth in step.
  const [edits, setEdits] = useState<Partial<PayrollSettings>>({});
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (body: PayrollSettings) => api.payroll.setSettings(body),
    onSuccess: () => {
      setError(null);
      setEdits({});
      qc.invalidateQueries({ queryKey: ["payroll-settings"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save"),
  });

  if (!data) return <p className="t-meta">Loading…</p>;

  const draft: PayrollSettings = { ...data, ...edits };
  const patch = (p: Partial<PayrollSettings>) => save.mutate({ ...draft, ...p });

  return (
    <div>
      <SectionLabel>Statutory schemes</SectionLabel>
      <div className="flex flex-wrap items-center gap-2">
        <Toggle on={draft.pf_enabled} onClick={() => patch({ pf_enabled: !draft.pf_enabled })}>
          Provident fund
        </Toggle>
        <Toggle on={draft.esi_enabled} onClick={() => patch({ esi_enabled: !draft.esi_enabled })}>
          ESI
        </Toggle>
        {draft.pf_enabled && (
          <Toggle
            on={draft.pf_on_full_wage}
            onClick={() => patch({ pf_on_full_wage: !draft.pf_on_full_wage })}
          >
            PF on full wage
          </Toggle>
        )}
      </div>
      <p className="t-meta mt-2">{DAYS_NOTE}</p>

      {(draft.pf_enabled || draft.esi_enabled) && (
        <div className="mt-4 flex flex-wrap items-end gap-3">
          {draft.pf_enabled && (
            <div className="space-y-1.5">
              <Label htmlFor="pfceil" className="t-micro">
                PF wage ceiling
              </Label>
              <Input
                id="pfceil"
                value={draft.pf_wage_ceiling}
                onChange={(e) => setEdits({ ...edits, pf_wage_ceiling: e.target.value })}
                onBlur={() => save.mutate(draft)}
                inputMode="decimal"
                className="w-32 tabular-nums"
              />
            </div>
          )}
          {draft.esi_enabled && (
            <div className="space-y-1.5">
              <Label htmlFor="esiceil" className="t-micro">
                ESI wage ceiling
              </Label>
              <Input
                id="esiceil"
                value={draft.esi_wage_ceiling}
                onChange={(e) => setEdits({ ...edits, esi_wage_ceiling: e.target.value })}
                onBlur={() => save.mutate(draft)}
                inputMode="decimal"
                className="w-32 tabular-nums"
              />
            </div>
          )}
        </div>
      )}
      <p className="t-meta mt-2">
        {draft.pf_on_full_wage
          ? "Contributing 12% on the whole PF wage. Pension stays capped on ₹15,000 either way."
          : "Contributions cap at the ceiling. Both are lawful — match what you committed to."}
      </p>
      {error && <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">{error}</p>}
    </div>
  );
}

function PayComponents() {
  const qc = useQueryClient();
  const { data: components } = useQuery({
    queryKey: ["pay-components"],
    queryFn: api.payroll.components,
  });
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [wageBasis, setWageBasis] = useState<"wages" | "excluded" | "outside">("excluded");
  const [kind, setKind] = useState<"earning" | "deduction">("earning");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.payroll.createComponent({
        code,
        name,
        kind,
        wage_basis: kind === "earning" ? wageBasis : "outside",
        pf_wage: wageBasis === "wages",
        esi_wage: kind === "earning",
        taxable: true,
        sequence: (components?.length ?? 0) * 10 + 10,
      }),
    onSuccess: () => {
      setCode("");
      setName("");
      setWageBasis("excluded");
      setKind("earning");
      setError(null);
      qc.invalidateQueries({ queryKey: ["pay-components"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not add"),
  });

  return (
    <div>
      <SectionLabel>Pay components</SectionLabel>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (code && name) create.mutate();
        }}
        className="flex flex-wrap items-end gap-3"
      >
        <div className="space-y-1.5">
          <Label htmlFor="ccode" className="t-micro">
            Code
          </Label>
          <Input
            id="ccode"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="BASIC"
            className="w-28"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="cname" className="t-micro">
            Name
          </Label>
          <Input
            id="cname"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Basic"
          />
        </div>
        <Toggle on={kind === "earning"} onClick={() => setKind(kind === "earning" ? "deduction" : "earning")}>
          {kind === "earning" ? "Earning" : "Deduction"}
        </Toggle>
        {kind === "earning" && (
          <div className="flex flex-wrap gap-1.5">
            {(
              [
                ["wages", "Wages"],
                ["excluded", "Allowance"],
                ["outside", "Reimbursement"],
              ] as const
            ).map(([value, label]) => (
              <Toggle
                key={value}
                on={wageBasis === value}
                onClick={() => setWageBasis(value)}
              >
                {label}
              </Toggle>
            ))}
          </div>
        )}
        <Action type="submit" disabled={create.isPending}>
          Add
        </Action>
      </form>

      {/* The one setting here that silently changes a statutory remittance. */}
      <p className="t-meta mt-2">
        This classification decides the statutory wage, so it decides real money.
        <strong className="font-medium"> Wages</strong> is basic and DA.
        <strong className="font-medium"> Allowance</strong> is excluded from wages but
        still counted when testing whether allowances exceed half of pay — from 21 Nov 2025
        anything above that half is added back into wages.
        <strong className="font-medium"> Reimbursement</strong> is not remuneration at all,
        so it sits outside the test. Where a given allowance falls is your auditor&apos;s
        judgement, not something this software can infer from a name.
      </p>

      {components && components.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {components.map((c) => (
            <Pill key={c.id}>
              {c.name}
              {c.kind === "earning" && c.wage_basis === "wages" && " · wages"}
              {c.kind === "earning" && c.wage_basis === "outside" && " · reimbursement"}
              {c.kind === "deduction" && " · deduction"}
            </Pill>
          ))}
        </div>
      ) : (
        <p className="t-meta mt-3">
          No components yet. Salaries are built from these, so add Basic first.
        </p>
      )}
      {error && <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">{error}</p>}
    </div>
  );
}

type SlabDraft = { up_to: string; amount: string };

function ProfessionalTax() {
  const qc = useQueryClient();
  const { data: slabs } = useQuery({ queryKey: ["pt-slabs"], queryFn: api.payroll.ptSlabs });
  const [rows, setRows] = useState<SlabDraft[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A slab schedule is a LIST — rows get added and removed, so there's nothing
  // to fall back to per-field the way the other two panels do. This is React's
  // documented "adjust state when a prop changes" pattern: reset during
  // render, not in an effect, so no extra paint and no lint suppression.
  //
  // `dirty` is what stops a background refetch landing mid-edit and silently
  // discarding a row someone just typed. A dropped slab is a wrong statutory
  // deduction, so losing one quietly is the worst outcome here.
  const [dirty, setDirty] = useState(false);
  const [syncedFrom, setSyncedFrom] = useState<PTSlab[] | undefined>(undefined);
  if (slabs && slabs !== syncedFrom && !dirty) {
    setSyncedFrom(slabs);
    setRows(slabs.map((s: PTSlab) => ({ up_to: s.up_to ?? "", amount: s.amount })));
  }

  const save = useMutation({
    mutationFn: (draft: SlabDraft[]) =>
      api.payroll.setPtSlabs(
        draft
          .filter((r) => r.amount !== "")
          .map((r) => ({ up_to: r.up_to === "" ? null : r.up_to, amount: r.amount }))
      ),
    onSuccess: () => {
      setError(null);
      setDirty(false); // server copy is authoritative again
      qc.invalidateQueries({ queryKey: ["pt-slabs"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save"),
  });

  const draft = rows ?? [];
  const edit = (next: SlabDraft[]) => {
    setDirty(true);
    setRows(next);
  };

  return (
    <div>
      <SectionLabel>Professional tax</SectionLabel>
      {/* Not shipped per-state on purpose: twenty states' schedules in code
          would be twenty chances to be quietly wrong about a deduction. */}
      <p className="t-meta">
        Levied by state, so enter your own state&apos;s schedule. Leave it empty where PT
        isn&apos;t levied — Delhi, Haryana, UP and others. Blank &quot;up to&quot; means the
        top, unbounded slab.
      </p>

      <div className="mt-3 space-y-2">
        {draft.map((row, i) => (
          <div key={i} className="flex flex-wrap items-end gap-2">
            <div className="space-y-1.5">
              {i === 0 && <Label className="t-micro">Monthly gross up to</Label>}
              <Input
                value={row.up_to}
                onChange={(e) =>
                  edit(draft.map((r, j) => (i === j ? { ...r, up_to: e.target.value } : r)))
                }
                placeholder="and above"
                inputMode="decimal"
                className="w-36 tabular-nums"
              />
            </div>
            <div className="space-y-1.5">
              {i === 0 && <Label className="t-micro">Tax</Label>}
              <Input
                value={row.amount}
                onChange={(e) =>
                  edit(draft.map((r, j) => (i === j ? { ...r, amount: e.target.value } : r)))
                }
                inputMode="decimal"
                className="w-28 tabular-nums"
              />
            </div>
            <Action
              variant="ghost"
              size="sm"
              onClick={() => edit(draft.filter((_, j) => j !== i))}
            >
              Remove
            </Action>
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <Action
          variant="quiet"
          size="sm"
          onClick={() => edit([...draft, { up_to: "", amount: "" }])}
        >
          Add slab
        </Action>
        <Action size="sm" onClick={() => save.mutate(draft)} disabled={save.isPending}>
          {save.isPending ? "Saving…" : dirty ? "Save schedule *" : "Save schedule"}
        </Action>
      </div>
      {error && <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">{error}</p>}
    </div>
  );
}

export function PayrollSetup() {
  return (
    <div className="surface-raised pop mb-8 space-y-8 p-5">
      <StatutorySchemes />
      <PayComponents />
      <ProfessionalTax />
    </div>
  );
}
