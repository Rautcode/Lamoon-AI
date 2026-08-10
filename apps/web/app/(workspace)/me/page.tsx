"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Action, Avatar, Empty, Pill, SectionLabel, Status } from "@/components/lamoon/primitives";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/* Employee Self-Service.

   Everything here comes from /me/**, which takes no employee id — the server
   resolves the person from the JWT. That's why this page can't accidentally
   show someone else's data: there's no id for it to get wrong.

   Leave types are fetched via the ESS balances call (which returns the type
   name and id per row) rather than /leave/types, because an employee has no
   leave.read permission — the balance IS their view of the type list. */

const UNSET = "__unset__";
const TONE = { active: "positive", probation: "caution", exited: "neutral" } as const;

function hoursLabel(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h}h${m ? ` ${m}m` : ""}` : `${m}m`;
}

function clockTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
}

function BalanceRing({ used, allocated, label }: { used: number; allocated: number; label: string }) {
  const pct = allocated > 0 ? Math.min(1, used / allocated) : 0;
  const r = 26;
  const c = 2 * Math.PI * r;
  return (
    <div className="flex flex-col items-center gap-2.5">
      <span className="relative grid size-[68px] place-items-center">
        <svg viewBox="0 0 64 64" className="absolute size-[68px] -rotate-90">
          <circle cx="32" cy="32" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="5" />
          <circle
            cx="32" cy="32" r={r} fill="none" stroke="var(--ink-2)" strokeWidth="5"
            strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct)}
            style={{ transition: "stroke-dashoffset 600ms var(--ease-out-expo)" }}
          />
        </svg>
        <span className="relative text-center leading-none">
          <span className="block text-[1.0625rem] font-medium tabular-nums">
            {allocated - used}
          </span>
          <span className="block text-[0.625rem] text-[var(--ink-4)]">left</span>
        </span>
      </span>
      <span className="text-center text-[0.75rem] text-[var(--ink-3)]">
        {label}
        <span className="block text-[0.6875rem] text-[var(--ink-4)]">
          {used}/{allocated} used
        </span>
      </span>
    </div>
  );
}

export default function MePage() {
  const queryClient = useQueryClient();
  const [composing, setComposing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: me, isLoading, isError, error: profileError } = useQuery({
    queryKey: ["self-profile"],
    queryFn: api.self.profile,
    retry: false,
  });
  const { data: balances } = useQuery({ queryKey: ["self-balances"], queryFn: api.self.balances });
  const { data: requests } = useQuery({ queryKey: ["self-requests"], queryFn: api.self.requests });
  const { data: attendance } = useQuery({
    queryKey: ["self-attendance"],
    queryFn: () => api.self.attendance(14),
  });
  // "Today" comes from the server, which knows the company timezone. Deriving
  // it from the browser's UTC date shows "not checked in" to someone who IS
  // checked in, every evening in IST.
  const { data: today } = useQuery({
    queryKey: ["self-today"],
    queryFn: api.self.attendanceToday,
  });

  const [punchError, setPunchError] = useState<string | null>(null);
  const punch = useMutation({
    mutationFn: (kind: "in" | "out") => api.self.punch(kind),
    onSuccess: () => {
      setPunchError(null);
      queryClient.invalidateQueries({ queryKey: ["self-attendance"] });
      queryClient.invalidateQueries({ queryKey: ["self-today"] });
    },
    onError: (e: unknown) =>
      setPunchError(
        e instanceof ApiError && e.status === 409
          ? e.message
          : "Could not record that punch."
      ),
  });

  const [type, setType] = useState(UNSET);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [reason, setReason] = useState("");

  const file = useMutation({
    mutationFn: () =>
      api.self.fileLeave({
        leave_type_id: type,
        start_date: start,
        end_date: end,
        reason: reason || undefined,
      }),
    onSuccess: () => {
      setType(UNSET);
      setStart("");
      setEnd("");
      setReason("");
      setComposing(false);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["self-requests"] });
      queryClient.invalidateQueries({ queryKey: ["self-balances"] });
    },
    onError: (e: unknown) =>
      setError(
        e instanceof ApiError && e.status === 422
          ? "Check the dates — end can't be before start."
          : "Could not submit that request."
      ),
  });

  const typeName = (id: string) =>
    balances?.find((b) => b.leave_type_id === id)?.leave_type_name ?? "Leave";

  if (isLoading) return <Empty>Loading…</Empty>;

  // The honest case: HR/admin logins have no employee record behind them.
  if (isError) {
    const notLinked = profileError instanceof ApiError && profileError.status === 404;
    return (
      <Empty>
        {notLinked
          ? "This login isn't linked to an employee record, so there's no self-service profile to show."
          : "Couldn't load your profile."}
      </Empty>
    );
  }
  if (!me) return <Empty>Couldn&apos;t load your profile.</Empty>;

  return (
    <div className="stagger">
      <header style={{ "--i": 0 } as React.CSSProperties} className="flex flex-wrap items-center gap-5">
        <Avatar name={me.full_name} size={76} />
        <div className="min-w-0">
          <h1 className="t-display">{me.full_name}</h1>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
            <Status tone={TONE[me.status] ?? "neutral"}>{me.status}</Status>
            {me.joined_on && <Pill>Joined {me.joined_on}</Pill>}
          </div>
        </div>
      </header>

      {/* --- Today's clock --------------------------------------------------- */}
      <section style={{ "--i": 1 } as React.CSSProperties} className="mt-12">
        <SectionLabel>Today</SectionLabel>
        <div className="surface flex flex-wrap items-center gap-5 p-5">
          <div className="min-w-0 flex-1">
            <p className="text-[1.75rem] leading-none font-medium tracking-[-0.03em] tabular-nums">
              {today ? hoursLabel(today.worked_minutes) : "0m"}
            </p>
            <p className="mt-2 t-meta">
              {!today || !today.first_in
                ? "Not checked in yet"
                : today.open
                  ? `Checked in at ${clockTime(today.first_in)} — still on the clock`
                  : `${clockTime(today.first_in)} → ${clockTime(today.last_out)}`}
              {today?.late ? " · late start" : ""}
            </p>
          </div>
          <Action
            onClick={() => punch.mutate(today?.open ? "out" : "in")}
            disabled={punch.isPending}
          >
            {punch.isPending
              ? "…"
              : today?.open
                ? "Check out"
                : "Check in"}
          </Action>
        </div>
        {punchError && <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">{punchError}</p>}

        {attendance && attendance.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {attendance.map((d) => (
              <span
                key={d.day}
                title={`${d.day}: ${hoursLabel(d.worked_minutes)}${d.late ? " (late)" : ""}`}
                className="size-6 rounded-[6px]"
                style={{
                  background:
                    d.worked_minutes === 0
                      ? "var(--surface-2)"
                      : `color-mix(in oklch, var(--ink-1) ${Math.round(
                          (0.15 + Math.min(1.35, d.worked_minutes / 480) * 0.6) * 100
                        )}%, transparent)`,
                  boxShadow: d.late ? "inset 0 0 0 1.5px var(--caution)" : undefined,
                }}
              />
            ))}
          </div>
        )}
      </section>

      <section style={{ "--i": 2 } as React.CSSProperties} className="mt-12">
        <SectionLabel
          action={
            <Action size="sm" onClick={() => setComposing((v) => !v)}>
              <Plus size={15} />
              Request time off
            </Action>
          }
        >
          Your balance
        </SectionLabel>

        {balances && balances.length > 0 ? (
          <div className="flex flex-wrap gap-7">
            {balances.map((b) => (
              <BalanceRing
                key={b.leave_type_id}
                used={b.used}
                allocated={b.allocated}
                label={b.leave_type_name}
              />
            ))}
          </div>
        ) : (
          <p className="t-meta">No leave types configured yet — ask HR.</p>
        )}
      </section>

      {composing && (
        <form
          style={{ "--i": 2 } as React.CSSProperties}
          onSubmit={(e) => {
            e.preventDefault();
            if (type === UNSET || !start || !end) {
              setError("Choose a leave type and both dates.");
              return;
            }
            file.mutate();
          }}
          className="surface-raised pop mt-6 flex flex-wrap items-end gap-3 p-5"
        >
          <div className="space-y-1.5">
            <Label className="t-micro">Type</Label>
            <Select value={type} onValueChange={(v) => setType(v ?? UNSET)}>
              <SelectTrigger className="w-40">
                <SelectValue>{(v: string) => (v === UNSET ? "Choose" : typeName(v))}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {balances?.map((b) => (
                  <SelectItem key={b.leave_type_id} value={b.leave_type_id}>
                    {b.leave_type_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="start" className="t-micro">
              Start
            </Label>
            <Input id="start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="end" className="t-micro">
              End
            </Label>
            <Input id="end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reason" className="t-micro">
              Reason
            </Label>
            <Input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <Action type="submit" disabled={file.isPending}>
            {file.isPending ? "Submitting…" : "Submit"}
          </Action>
          {error && <p className="w-full text-[0.8125rem] text-[var(--critical)]">{error}</p>}
        </form>
      )}

      <section style={{ "--i": 3 } as React.CSSProperties} className="mt-12">
        <SectionLabel>Your requests</SectionLabel>
        {!requests || requests.length === 0 ? (
          <p className="t-meta">You haven&apos;t requested any time off yet.</p>
        ) : (
          <div className="-mx-3">
            {requests.map((r) => (
              <div
                key={r.id}
                className="flex items-center gap-4 rounded-[10px] px-3 py-3 hover:bg-[var(--surface-1)]"
              >
                <span
                  className={`size-1.5 shrink-0 rounded-full ${
                    r.status === "approved"
                      ? "bg-[var(--positive)]"
                      : r.status === "pending"
                        ? "bg-[var(--caution)]"
                        : "bg-[var(--ink-4)]"
                  }`}
                />
                <span className="min-w-0 flex-1 text-[0.875rem]">
                  {typeName(r.leave_type_id)}
                  <span className="text-[var(--ink-3)]">
                    {" · "}
                    {r.days} {r.days === 1 ? "day" : "days"} · {r.start_date} → {r.end_date}
                  </span>
                </span>
                <span className="shrink-0 text-[0.75rem] text-[var(--ink-3)]">{r.status}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
