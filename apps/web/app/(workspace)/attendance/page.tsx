"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { DaySummary } from "@/lib/types";
import { Avatar, Empty, Pill, SectionLabel, Status } from "@/components/lamoon/primitives";

/* Attendance — presence now, patterns over time.

   The heatmap is the point: a table of clock-in times tells you nothing at a
   glance, whereas two weeks of shaded cells shows you who's drifting late and
   who's quietly working twelve-hour days. Shade = hours worked, so an unusual
   ROW or an unusual COLUMN both jump out without reading a single number. */

const DAY_MS = 86_400_000;

function hhmm(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h}h${m ? ` ${m}m` : ""}` : `${m}m`;
}

function clockTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Ink opacity by hours worked. Deliberately monochrome — chroma belongs to
    Lumo alone (DESIGN.md §1), so intensity carries the signal instead. */
function shade(day: DaySummary | undefined, expected: number): string {
  if (!day || day.worked_minutes === 0) return "var(--surface-2)";
  const ratio = Math.min(1.35, day.worked_minutes / expected);
  const alpha = 0.15 + ratio * 0.6;
  return `color-mix(in oklch, var(--ink-1) ${Math.round(alpha * 100)}%, transparent)`;
}

export default function AttendancePage() {
  const { data: presence, isLoading } = useQuery({
    queryKey: ["attendance-today"],
    queryFn: api.attendance.today,
    refetchInterval: 60_000, // people arrive and leave while this is open
  });
  const { data: summary } = useQuery({
    queryKey: ["attendance-summary"],
    queryFn: () => api.attendance.summary(14),
  });
  const { data: policy } = useQuery({
    queryKey: ["attendance-policy"],
    queryFn: api.attendance.policy,
  });

  const expected = policy?.expected_minutes ?? 480;

  // Build the column axis from today backwards so every row lines up even
  // when someone has no punches on a given day.
  const today = new Date();
  const days: string[] = Array.from({ length: 14 }, (_, i) => {
    const d = new Date(today.getTime() - (13 - i) * DAY_MS);
    return d.toISOString().slice(0, 10);
  });

  const inNow = presence?.filter((p) => p.status === "in").length ?? 0;
  const lateToday = presence?.filter((p) => p.late).length ?? 0;

  return (
    <div className="fade">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="t-display">Attendance</h1>
          <p className="mt-2 t-meta">
            {inNow} in right now
            {lateToday ? ` · ${lateToday} late today` : ""}
            {policy ? ` · day starts ${policy.workday_start.slice(0, 5)} ${policy.timezone}` : ""}
          </p>
        </div>
      </header>

      {isLoading && <Empty>Loading…</Empty>}

      {/* --- Right now ------------------------------------------------------ */}
      {presence && presence.length > 0 && (
        <section className="mb-12">
          <SectionLabel>Right now</SectionLabel>
          <div className="stagger grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
            {presence.map((p, i) => (
              <div
                key={p.employee_id}
                style={{ "--i": i } as React.CSSProperties}
                className="surface flex items-center gap-3.5 p-4"
              >
                <Avatar name={p.full_name} size={38} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.9375rem] font-medium">{p.full_name}</p>
                  <p className="truncate text-[0.8125rem] text-[var(--ink-3)]">
                    {p.status === "absent"
                      ? "No punch today"
                      : `${clockTime(p.first_in)}${
                          p.status === "out" ? ` → ${clockTime(p.last_out)}` : ""
                        } · ${hhmm(p.worked_minutes)}`}
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Status
                    tone={
                      p.status === "in" ? "positive" : p.status === "out" ? "neutral" : "caution"
                    }
                  >
                    {p.status === "in" ? "In" : p.status === "out" ? "Done" : "Absent"}
                  </Status>
                  {p.late && <Pill>Late</Pill>}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {presence && presence.length === 0 && (
        <Empty>No active employees to track yet.</Empty>
      )}

      {/* --- Heatmap -------------------------------------------------------- */}
      {summary && summary.length > 0 && (
        <section>
          <SectionLabel>Last 14 days</SectionLabel>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-separate border-spacing-y-1">
              <thead>
                <tr>
                  <th className="t-micro w-40 pb-2 text-left font-semibold">Person</th>
                  {days.map((d) => (
                    <th key={d} className="pb-2 text-center">
                      <span className="text-[0.625rem] text-[var(--ink-4)]">
                        {new Date(d + "T00:00:00").getDate()}
                      </span>
                    </th>
                  ))}
                  <th className="t-micro pb-2 pl-3 text-right font-semibold">Avg</th>
                </tr>
              </thead>
              <tbody>
                {summary.map((row) => {
                  const byDay = new Map(row.days.map((d) => [d.day, d]));
                  const worked = row.days.filter((d) => d.worked_minutes > 0);
                  const avg = worked.length
                    ? Math.round(
                        worked.reduce((a, d) => a + d.worked_minutes, 0) / worked.length
                      )
                    : 0;
                  return (
                    <tr key={row.employee_id}>
                      <td className="pr-3 text-[0.875rem]">
                        <span className="flex items-center gap-2">
                          <Avatar name={row.full_name} size={22} />
                          <span className="truncate">{row.full_name}</span>
                        </span>
                      </td>
                      {days.map((d) => {
                        const day = byDay.get(d);
                        const label = day
                          ? `${d}: ${hhmm(day.worked_minutes)}${day.late ? " (late)" : ""}`
                          : `${d}: no punches`;
                        return (
                          <td key={d} className="px-0.5">
                            <span
                              title={label}
                              aria-label={label}
                              className="mx-auto block size-5 rounded-[5px]"
                              style={{
                                background: shade(day, expected),
                                // A late start is a ring, not a colour — keeps
                                // the monochrome rule while staying legible.
                                boxShadow: day?.late
                                  ? "inset 0 0 0 1.5px var(--caution)"
                                  : undefined,
                              }}
                            />
                          </td>
                        );
                      })}
                      <td className="pl-3 text-right text-[0.75rem] tabular-nums text-[var(--ink-3)]">
                        {avg ? hhmm(avg) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-4 t-meta">
            Darker means more hours worked. A ring marks a late start. Empty
            cells are days with no punches — this module has no holiday
            calendar yet, so a weekend and a no-show look the same.
          </p>
        </section>
      )}
    </div>
  );
}
