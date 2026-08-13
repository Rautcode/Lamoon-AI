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
    Lumo alone (DESIGN.md §1), so intensity carries the signal instead.

    A non-working day is rendered as nothing at all rather than as an empty
    cell: a Sunday is not a gap in someone's attendance, and drawing it like
    one is what made a fortnight of weekends look like absenteeism. */
function shade(day: DaySummary | undefined, expected: number): string {
  if (day && !day.working_day) return "transparent";
  if (!day || day.worked_minutes === 0) return "var(--surface-2)";
  const ratio = Math.min(1.35, day.worked_minutes / expected);
  const alpha = 0.15 + ratio * 0.6;
  return `color-mix(in oklch, var(--ink-1) ${Math.round(alpha * 100)}%, transparent)`;
}

/* The day vocabulary, in the words an operator uses. `absent` is the only one
   of these that is a problem, and it is the only one that reads as caution. */
const STATE_LABEL: Record<string, string> = {
  present: "In",
  absent: "Absent",
  weekly_off: "Weekly off",
  holiday: "Holiday",
  paid_leave: "On leave",
  unpaid_leave: "Unpaid leave",
  half_day: "Half day",
  missing_punch: "Missing punch",
  work_from_home: "Working remotely",
  on_duty: "On duty",
};

const STATE_DETAIL: Record<string, string> = {
  absent: "No punch today",
  weekly_off: "Not a working day",
  holiday: "Company holiday",
  paid_leave: "Approved leave",
  unpaid_leave: "Approved unpaid leave",
  missing_punch: "Punched in, never out",
};

const STATE_TONE: Record<string, "positive" | "neutral" | "caution"> = {
  present: "positive",
  absent: "caution",
  missing_punch: "caution",
  weekly_off: "neutral",
  holiday: "neutral",
  paid_leave: "neutral",
  unpaid_leave: "neutral",
  half_day: "neutral",
  work_from_home: "positive",
  on_duty: "positive",
};

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
  // Only a genuinely unexplained absence is worth counting. Somebody on
  // approved leave, or a company-wide holiday, is not an exception.
  const unexplained = presence?.filter((p) => p.state === "absent").length ?? 0;
  const offToday = presence?.find(
    (p) => p.state === "holiday" || p.state === "weekly_off"
  );

  return (
    <div className="fade">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="t-display">Attendance</h1>
          <p className="mt-2 t-meta">
            {/* On a holiday or weekly off, "0 in right now" is the expected
                answer, not an alarm — so say which it is instead. */}
            {offToday
              ? offToday.state === "holiday"
                ? `Company holiday · ${offToday.holiday}`
                : "Weekly off"
              : `${inNow} in right now`}
            {lateToday ? ` · ${lateToday} late today` : ""}
            {unexplained ? ` · ${unexplained} unexplained` : ""}
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
                    {p.first_in
                      ? `${clockTime(p.first_in)}${
                          p.status === "out" ? ` → ${clockTime(p.last_out)}` : ""
                        } · ${hhmm(p.worked_minutes)}`
                      : STATE_DETAIL[p.state] ?? "No punch today"}
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Status tone={STATE_TONE[p.state] ?? "caution"}>
                    {p.state === "holiday" && p.holiday
                      ? p.holiday
                      : STATE_LABEL[p.state] ?? "Absent"}
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
                        const label = !day
                          ? `${d}: no record`
                          : day.holiday
                            ? `${d}: ${day.holiday}`
                            : !day.working_day
                              ? `${d}: weekly off`
                              : day.worked_minutes === 0
                                ? `${d}: no punches`
                                : `${d}: ${hhmm(day.worked_minutes)}${day.late ? " (late)" : ""}`;
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
            Darker means more hours worked. A ring marks a late start. Blank
            gaps are weekends and holidays from the company calendar; a filled
            but empty cell is a working day with no punches.
          </p>
        </section>
      )}
    </div>
  );
}
