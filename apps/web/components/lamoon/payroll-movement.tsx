"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { BridgeLine } from "@/lib/types";
import { SectionLabel } from "@/components/lamoon/primitives";

/* Why the number changed.

   The first question anyone asks about payroll is not "what is it" but "why is
   it different". A line chart cannot answer that; a bridge can. So this is a
   table and a set of bars, not a graph — the figures are the point, and the
   bars exist only to make their relative size legible at a glance.

   Bar widths are proportional to the LARGEST cause, not to the net change. A
   month where a big joiner cohort and a big exit cohort nearly cancel has a
   tiny net movement and two enormous causes, and scaling to the net would make
   both look like nothing. */

function rupees(amount: string): string {
  const [whole, paise = "00"] = amount.split(".");
  const negative = whole.startsWith("-");
  const digits = whole.replace("-", "");
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${last3}` : last3;
  return `${negative ? "−" : ""}₹${grouped}${paise === "00" ? "" : `.${paise}`}`;
}

/** Signed, so a change reads as a change rather than a value. */
function signed(amount: string): string {
  return amount.startsWith("-") ? rupees(amount) : `+${rupees(amount)}`;
}

function percent(previous: string, current: string): string | null {
  const p = Number(previous);
  if (!p) return null;
  const pct = ((Number(current) - p) / p) * 100;
  if (Math.abs(pct) < 0.05) return "—";
  return `${pct > 0 ? "+" : "−"}${Math.abs(pct).toFixed(1)}%`;
}

function monthLabel(period: string): string {
  return new Date(period + "T00:00:00").toLocaleDateString([], {
    month: "long",
    year: "numeric",
  });
}

function Bar({ line, scale }: { line: BridgeLine; scale: number }) {
  const value = Number(line.amount);
  const width = scale > 0 ? (Math.abs(value) / scale) * 100 : 0;
  const negative = value < 0;
  return (
    <div className="grid grid-cols-[1fr_120px_auto] items-center gap-4 text-[0.8125rem]">
      <span>
        {line.label}
        {line.count ? <span className="text-[var(--ink-3)]"> ({line.count})</span> : null}
      </span>
      <span className="relative h-2 overflow-hidden rounded-[2px] bg-[var(--surface-2)]">
        <span
          className={
            negative
              ? "absolute inset-y-0 left-0 rounded-[2px] border border-[var(--ink-4)]"
              : "absolute inset-y-0 left-0 rounded-[2px] bg-[var(--ink-2)]"
          }
          style={{ width: `${Math.max(width, value === 0 ? 0 : 2)}%` }}
        />
      </span>
      <span className="tabular-nums whitespace-nowrap">{signed(line.amount)}</span>
    </div>
  );
}

export function PayrollMovement({ period }: { period: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["payroll-movement", period],
    queryFn: () => api.payroll.movement(period),
  });

  if (isLoading || !data) return null;

  if (!data.comparable) {
    return (
      <div>
        <SectionLabel>Movement</SectionLabel>
        <p className="t-meta">
          Nothing to compare with — no payroll was run for{" "}
          {monthLabel(data.previous_period)}.
        </p>
      </div>
    );
  }

  const gross = data.lines.find((l) => l.code === "gross");
  const grossChange = gross ? Number(gross.change) : 0;
  const scale = Math.max(...data.bridge.map((b) => Math.abs(Number(b.amount))), 0);
  const headcount = data.current.employees - data.previous.employees;

  return (
    <div>
      <SectionLabel>Movement</SectionLabel>

      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 text-left"
      >
        <span className="text-[1.375rem] font-medium tabular-nums leading-none">
          {gross ? signed(gross.change) : "—"}
        </span>
        <span className="t-meta">
          gross against {monthLabel(data.previous_period)}
          {gross && percent(gross.previous, gross.current) !== "—"
            ? ` · ${percent(gross.previous, gross.current)}`
            : ""}
          {headcount !== 0 &&
            ` · ${headcount > 0 ? "+" : "−"}${Math.abs(headcount)} ${
              Math.abs(headcount) === 1 ? "person" : "people"
            }`}
        </span>
        <span className="t-meta ml-auto">{open ? "Hide" : "Show"} detail</span>
      </button>

      {!open && data.bridge.length > 0 && grossChange !== 0 && (
        <p className="t-meta mt-2">
          Mostly{" "}
          {
            [...data.bridge].sort(
              (a, b) => Math.abs(Number(b.amount)) - Math.abs(Number(a.amount))
            )[0].label
          }
          .
        </p>
      )}

      {open && (
        <>
          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[440px] text-[0.8125rem]">
              <thead>
                <tr className="border-b border-[var(--hairline)]">
                  <th className="py-2 text-left font-normal t-micro">Component</th>
                  <th className="py-2 text-right font-normal t-micro">
                    {monthLabel(data.previous_period).split(" ")[0]}
                  </th>
                  <th className="py-2 text-right font-normal t-micro">
                    {monthLabel(data.period).split(" ")[0]}
                  </th>
                  <th className="py-2 text-right font-normal t-micro">Change</th>
                </tr>
              </thead>
              <tbody>
                {data.lines.map((l) => (
                  <tr key={l.code} className="border-b border-[var(--hairline)] last:border-0">
                    <td className="py-2">{l.label}</td>
                    <td className="py-2 text-right tabular-nums">{rupees(l.previous)}</td>
                    <td className="py-2 text-right tabular-nums">{rupees(l.current)}</td>
                    <td className="py-2 text-right tabular-nums">
                      {Number(l.change) === 0 ? "—" : signed(l.change)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {data.bridge.length > 0 && (
            <div className="mt-7">
              <div className="t-micro mb-3">
                What moved gross by {signed(gross?.change ?? "0")}
              </div>
              <div className="grid gap-2">
                {[...data.bridge]
                  .sort((a, b) => Math.abs(Number(b.amount)) - Math.abs(Number(a.amount)))
                  .map((b) => (
                    <Bar key={b.code} line={b} scale={scale} />
                  ))}
              </div>
            </div>
          )}

          {/* Always zero. Shown only if the decomposition ever stops closing,
              so a wrong bridge is visible rather than quietly plausible. */}
          {Number(data.unexplained) !== 0 && (
            <p className="mt-4 text-[0.8125rem] text-[var(--critical)]">
              {signed(data.unexplained)} of the change is unaccounted for. The causes
              above do not sum to the movement — treat this comparison as unreliable.
            </p>
          )}
        </>
      )}
    </div>
  );
}
