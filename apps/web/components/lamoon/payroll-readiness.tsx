"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ReadinessCheck } from "@/lib/types";
import { SectionLabel } from "@/components/lamoon/primitives";

/* Readiness — can payroll run at all?

   A gate, not a score. The percentage is a summary and never appears without
   its worst state beside it: one blocking check means payroll cannot be run
   correctly however high the number reads. So the headline reads
   "92% · 1 blocking", never "92%".

   Status is carried by a glyph, a word and a position — never colour alone.
   `unknown` is its own state and deliberately not a tick: an empty
   professional-tax schedule is correct in Delhi and indistinguishable from
   having forgotten one, and claiming a pass there would be the exact kind of
   false assurance this panel exists to prevent. */

const GLYPH: Record<ReadinessCheck["status"], string> = {
  ok: "✓",
  warning: "!",
  blocking: "✕",
  unknown: "?",
};

const TONE: Record<ReadinessCheck["status"], string> = {
  ok: "text-[var(--positive)]",
  warning: "text-[var(--caution)]",
  blocking: "text-[var(--critical)]",
  unknown: "text-[var(--ink-4)]",
};

/** Worst first. An operator reads down and stops when the problems run out. */
const RANK: Record<ReadinessCheck["status"], number> = {
  blocking: 0,
  warning: 1,
  unknown: 2,
  ok: 3,
};

export function PayrollReadiness({ period }: { period: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["payroll-readiness", period],
    queryFn: () => api.payroll.readiness(period),
  });

  if (isLoading || !data) return null;

  const ordered = [...data.checks].sort((a, b) => RANK[a.status] - RANK[b.status]);
  const blocked = data.blocking > 0;
  const problems = data.blocking + data.warnings;

  return (
    <div>
      <SectionLabel>Readiness</SectionLabel>

      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-baseline gap-3 text-left"
      >
        <span className="text-[1.75rem] font-medium tabular-nums leading-none">
          {data.percent}%
        </span>
        {/* The percentage never stands alone. */}
        {blocked ? (
          <span className="text-[0.8125rem] font-medium text-[var(--critical)]">
            {data.blocking} blocking — payroll cannot run correctly
          </span>
        ) : data.warnings > 0 ? (
          <span className="t-meta">
            {data.warnings} {data.warnings === 1 ? "warning" : "warnings"}, none blocking
          </span>
        ) : (
          <span className="t-meta">Ready to run</span>
        )}
        <span className="t-meta ml-auto">{open ? "Hide" : "Show"} checks</span>
      </button>

      {/* Segments in proportion to real counts, not a decorative bar. */}
      <div
        className="mt-3 flex h-[6px] overflow-hidden rounded-full bg-[var(--surface-3)]"
        role="img"
        aria-label={`${data.percent}% ready: ${data.blocking} blocking, ${data.warnings} warnings, ${data.unknown} undetermined`}
      >
        {data.checks
          .slice()
          .sort((a, b) => RANK[b.status] - RANK[a.status])
          .map((c, i) => (
            <span
              key={`${c.code}-${i}`}
              style={{ width: `${100 / data.checks.length}%` }}
              className={
                c.status === "ok"
                  ? "bg-[var(--positive)]"
                  : c.status === "warning"
                    ? "bg-[var(--caution)]"
                    : c.status === "blocking"
                      ? "bg-[var(--critical)]"
                      : "bg-[var(--surface-3)]"
              }
            />
          ))}
      </div>

      {!open && problems > 0 && (
        <p className="t-meta mt-2">
          {ordered[0].label} — {ordered[0].detail}
        </p>
      )}

      {open && (
        <ul className="mt-4 divide-y divide-[var(--hairline)]">
          {ordered.map((c) => (
            <li key={c.code} className="flex items-start gap-3 py-2.5">
              <span
                className={`w-4 shrink-0 text-center font-mono text-[0.8125rem] ${TONE[c.status]}`}
                aria-hidden
              >
                {GLYPH[c.status]}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[0.875rem]">{c.label}</span>
                <span className="t-meta">{c.detail}</span>
              </span>
              <span className="t-meta shrink-0">
                {c.status === "unknown" ? "undetermined" : c.status}
              </span>
            </li>
          ))}
        </ul>
      )}

      {data.unknown > 0 && open && (
        <p className="t-meta mt-3">
          Undetermined checks are left out of the percentage rather than counted as
          passing — the system cannot tell whether they are configured or deliberately
          empty.
        </p>
      )}
    </div>
  );
}
