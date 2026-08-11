"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Finding, FindingReport } from "@/lib/types";
import { SectionLabel } from "@/components/lamoon/primitives";

/* Needs attention, and risk — deliberately two things.

   Validation asks "are the inputs valid?". Risk asks "does anything look
   unusual?". A missing salary structure has a right answer and somebody must
   supply it; a 41% jump in someone's pay may be entirely correct. Folding them
   into one number produces a figure that means nothing, so they render as
   separate sections and never share a count.

   There is no readiness panel here. Readiness is a third question — "can
   payroll run at all?" — and the API doesn't answer it yet. A percentage
   assembled in the browser from whatever happened to be fetched would be a
   number nobody could defend, which is the opposite of the point. */

/** ₹ from a decimal string. Amounts never pass through a JS number. */
function rupees(amount: string): string {
  const [whole, paise = "00"] = amount.split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = whole.replace("-", "");
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${sign}₹${grouped}${paise === "00" ? "" : `.${paise}`}`;
}

/** Codes are stable keys; these are what an operator calls the problem. */
const LABELS: Record<string, string> = {
  no_salary_structure: "No salary structure",
  zero_gross: "Gross pay computes to zero",
  negative_net: "Deductions exceed gross",
  overtime_unapproved: "Overtime awaiting approval",
  below_minimum_wage: "Below the configured minimum wage",
  pay_variance: "Unusual change from last month",
};

const RANK: Record<string, number> = { blocking: 0, warning: 1, info: 2 };

const SEVERITY_RAIL: Record<string, string> = {
  blocking: "border-l-[var(--critical)]",
  warning: "border-l-[var(--caution)]",
  info: "border-l-[var(--ink-4)]",
};

/** Severity carried by a rail AND a word. The rail makes it scannable at list
 *  scale; the word makes it accessible. Neither alone is sufficient. */
function Group({
  group,
  findings,
  open,
  onToggle,
}: {
  group: FindingReport["groups"][number];
  findings: Finding[];
  open: boolean;
  onToggle: () => void;
}) {
  const impact = Number(group.impact) > 0 ? rupees(group.impact) : null;
  return (
    <div className={`border-l-2 ${SEVERITY_RAIL[group.severity] ?? SEVERITY_RAIL.info}`}>
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-4 px-4 py-3 text-left hover:bg-[var(--surface-1)]"
      >
        <span className="w-8 shrink-0 text-[1.0625rem] font-medium tabular-nums">
          {group.count}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[0.875rem]">{LABELS[group.code] ?? group.code}</span>
          <span className="t-meta">{group.severity}</span>
        </span>
        {impact && <span className="shrink-0 text-[0.8125rem] tabular-nums">{impact}</span>}
      </button>

      {open && (
        <ul className="pb-2">
          {findings.map((f, i) => (
            <li key={`${f.employee_id ?? "x"}-${i}`} className="px-4 py-2 pl-12">
              <span className="block text-[0.875rem]">
                {f.employee_name ?? "—"}
                {f.impact && Number(f.impact) > 0 && (
                  <span className="text-[var(--ink-3)]"> · {rupees(f.impact)}</span>
                )}
              </span>
              <span className="t-meta">{f.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Report({
  report,
  emptyMessage,
}: {
  report: FindingReport;
  emptyMessage: string;
}) {
  const [open, setOpen] = useState<string | null>(null);

  if (!report.groups.length) {
    return <p className="t-meta">{emptyMessage}</p>;
  }
  // Worst state leads. The API orders groups by count, which is the right
  // default for a summary but the wrong one for a queue: a single blocking
  // item outranks fourteen warnings, because only one of them stops the run.
  const ordered = [...report.groups].sort(
    (a, b) => (RANK[a.severity] ?? 3) - (RANK[b.severity] ?? 3) || b.count - a.count
  );
  return (
    <div className="divide-y divide-[var(--hairline)] rounded-[12px] border border-[var(--hairline)] overflow-hidden">
      {ordered.map((g) => (
        <Group
          key={g.code}
          group={g}
          findings={report.findings.filter((f) => f.code === g.code)}
          open={open === g.code}
          onToggle={() => setOpen(open === g.code ? null : g.code)}
        />
      ))}
    </div>
  );
}

export function PayrollExceptions({ period }: { period: string }) {
  const validation = useQuery({
    queryKey: ["payroll-validation", period],
    queryFn: () => api.payroll.validation(period),
  });
  const risk = useQuery({
    queryKey: ["payroll-risk", period],
    queryFn: () => api.payroll.risk(period),
  });

  if (validation.isLoading) return <p className="t-meta">Checking inputs…</p>;
  if (!validation.data) return null;

  const v = validation.data;
  const total = v.blocking + v.warnings + v.info;

  return (
    <div className="space-y-10">
      <section>
        <SectionLabel>
          {total === 0
            ? "Needs attention"
            : `Needs attention · ${total}`}
        </SectionLabel>

        {v.blocking > 0 && (
          /* Blocking is stated in words before the list, because it is the one
             thing that changes what the run will do: those people are excluded
             from it rather than paid a number nobody can defend. */
          <p className="t-meta mb-3">
            <strong className="font-medium text-[var(--critical)]">
              {v.blocking} blocking
            </strong>{" "}
            — these people are excluded from the run until resolved.
            {v.warnings > 0 &&
              ` ${v.warnings} ${v.warnings === 1 ? "warning does" : "warnings do"} not block it.`}
          </p>
        )}

        <Report
          report={v}
          emptyMessage="Nothing needs attention. Every employee calculated without warnings."
        />

        {Number(v.impact) > 0 && (
          <p className="t-meta mt-3">
            {rupees(v.impact)} of pay is affected by the items above.
          </p>
        )}
      </section>

      {risk.data && risk.data.findings.length > 0 && (
        <section>
          <SectionLabel>Worth a second look · {risk.data.findings.length}</SectionLabel>
          {/* Not errors. Every one may be correct — the value is that somebody
              looked, which is why they are never mixed into the count above. */}
          <p className="t-meta mb-3">
            Detected by comparing with last month. These do not block anything.
          </p>
          <Report report={risk.data} emptyMessage="Nothing unusual." />
        </section>
      )}
    </div>
  );
}
