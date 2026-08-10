"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { Action, Avatar, Empty, Pill, SectionLabel } from "@/components/lamoon/primitives";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/* Time off.

   Ordered by what needs a human: decisions first, then everything else. The
   old version led with a config form for leave types, which is the thing you
   touch once a year. */

const UNSET = "__unset__";

export default function TimePage() {
  const queryClient = useQueryClient();
  const permissions = useAuthStore((s) => s.permissions);
  const canWrite = hasPermission(permissions, "leave.write");
  const canApprove = hasPermission(permissions, "leave.approve");

  const [composing, setComposing] = useState(false);
  const [configuring, setConfiguring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [holidayDay, setHolidayDay] = useState("");
  const [holidayName, setHolidayName] = useState("");

  const { data: employees } = useQuery({ queryKey: ["employees"], queryFn: () => api.employees.list() });
  const { data: types } = useQuery({ queryKey: ["leave-types"], queryFn: api.leave.types.list });
  const { data: requests, isLoading } = useQuery({
    queryKey: ["leave-requests"],
    queryFn: api.leave.requests.list,
  });
  const { data: holidays } = useQuery({ queryKey: ["holidays"], queryFn: api.calendar.holidays });
  const { data: workWeek } = useQuery({ queryKey: ["work-week"], queryFn: api.calendar.workWeek });

  const addHoliday = useMutation({
    mutationFn: () => api.calendar.addHoliday(holidayDay, holidayName),
    onSuccess: () => {
      setHolidayDay("");
      setHolidayName("");
      queryClient.invalidateQueries({ queryKey: ["holidays"] });
    },
    onError: () => setError("Could not add that holiday."),
  });
  const removeHoliday = useMutation({
    mutationFn: (id: string) => api.calendar.removeHoliday(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["holidays"] }),
  });
  const setWorkWeek = useMutation({
    mutationFn: (pattern: string) => api.calendar.setWorkWeek(pattern),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["work-week"] }),
  });

  function toggleWorkDay(index: number) {
    const current = workWeek?.working_days ?? "1111100";
    const next = current.split("");
    next[index] = next[index] === "1" ? "0" : "1";
    if (!next.includes("1")) return; // a company must work at least one day
    setWorkWeek.mutate(next.join(""));
  }

  const nameOf = (id: string) => employees?.find((e) => e.id === id)?.full_name ?? "Someone";
  const typeOf = (id: string) => types?.find((t) => t.id === id)?.name ?? "Leave";

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["leave-requests"] });
    queryClient.invalidateQueries({ queryKey: ["leave-balances"] });
  };

  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      action === "approve" ? api.leave.requests.approve(id) : api.leave.requests.reject(id),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "Could not update that request."),
  });

  // --- new request ---
  const [emp, setEmp] = useState(UNSET);
  const [type, setType] = useState(UNSET);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const createRequest = useMutation({
    mutationFn: () =>
      api.leave.requests.create({
        employee_id: emp,
        leave_type_id: type,
        start_date: start,
        end_date: end,
      }),
    onSuccess: () => {
      setEmp(UNSET);
      setType(UNSET);
      setStart("");
      setEnd("");
      setComposing(false);
      setError(null);
      invalidate();
    },
    onError: (e: unknown) =>
      setError(
        e instanceof ApiError && e.status === 422
          ? "Check the dates — end can't be before start."
          : "Could not file that request."
      ),
  });

  // --- new type ---
  const [typeName, setTypeName] = useState("");
  const [typeQuota, setTypeQuota] = useState("");
  const createType = useMutation({
    mutationFn: () => api.leave.types.create({ name: typeName, annual_quota: Number(typeQuota) }),
    onSuccess: () => {
      setTypeName("");
      setTypeQuota("");
      setConfiguring(false);
      queryClient.invalidateQueries({ queryKey: ["leave-types"] });
    },
  });

  const pending = requests?.filter((r) => r.status === "pending") ?? [];
  const decided = requests?.filter((r) => r.status !== "pending") ?? [];

  return (
    <div className="fade">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="t-display">Time</h1>
          <p className="mt-2 t-meta">
            {pending.length
              ? `${pending.length} awaiting a decision`
              : "Everything's decided"}
          </p>
        </div>
        {canWrite && (
          <div className="flex gap-2">
            <Action variant="quiet" onClick={() => setConfiguring((v) => !v)}>
              Leave types
            </Action>
            <Action onClick={() => setComposing((v) => !v)}>
              <Plus size={16} />
              File leave
            </Action>
          </div>
        )}
      </header>

      {error && <p className="mb-4 text-[0.8125rem] text-[var(--critical)]">{error}</p>}

      {configuring && canWrite && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createType.mutate();
          }}
          className="surface-raised pop mb-6 flex flex-wrap items-end gap-3 p-5"
        >
          <div className="space-y-1.5">
            <Label htmlFor="tname" className="t-micro">
              Type
            </Label>
            <Input
              id="tname"
              value={typeName}
              onChange={(e) => setTypeName(e.target.value)}
              placeholder="Annual"
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="tquota" className="t-micro">
              Days per year
            </Label>
            <Input
              id="tquota"
              type="number"
              min={0}
              value={typeQuota}
              onChange={(e) => setTypeQuota(e.target.value)}
              required
            />
          </div>
          <Action type="submit" disabled={createType.isPending}>
            Add type
          </Action>
          {types && types.length > 0 && (
            <div className="flex w-full flex-wrap gap-2 pt-1">
              {types.map((t) => (
                <Pill key={t.id}>
                  {t.name} · {t.annual_quota}d
                </Pill>
              ))}
            </div>
          )}
        </form>
      )}

      {/* Leave is billed in WORKING days, so this panel decides what every
          future request actually costs. */}
      {configuring && canWrite && (
        <div className="surface-raised pop mb-8 space-y-7 p-5">
          <div>
            <SectionLabel>Working week</SectionLabel>
            <div className="flex flex-wrap gap-1.5">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label, i) => {
                const on = (workWeek?.working_days ?? "1111100")[i] === "1";
                return (
                  <button
                    key={label}
                    onClick={() => toggleWorkDay(i)}
                    disabled={setWorkWeek.isPending}
                    aria-pressed={on}
                    className={`rounded-[9px] px-3 py-1.5 text-[0.8125rem] transition-colors ${
                      on
                        ? "bg-[var(--ink-1)] text-[var(--surface-0)]"
                        : "bg-[var(--surface-2)] text-[var(--ink-3)]"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 t-meta">
              Non-working days aren&apos;t charged against leave balances.
            </p>
          </div>

          <div>
            <SectionLabel>Holidays</SectionLabel>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (holidayDay && holidayName) addHoliday.mutate();
              }}
              className="flex flex-wrap items-end gap-3"
            >
              <div className="space-y-1.5">
                <Label htmlFor="hday" className="t-micro">
                  Date
                </Label>
                <Input
                  id="hday"
                  type="date"
                  value={holidayDay}
                  onChange={(e) => setHolidayDay(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="hname" className="t-micro">
                  Name
                </Label>
                <Input
                  id="hname"
                  value={holidayName}
                  onChange={(e) => setHolidayName(e.target.value)}
                  placeholder="Diwali"
                />
              </div>
              <Action type="submit" disabled={addHoliday.isPending}>
                Add holiday
              </Action>
            </form>
            {holidays && holidays.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {holidays.map((h) => (
                  <button
                    key={h.id}
                    onClick={() => removeHoliday.mutate(h.id)}
                    title="Remove"
                    className="group inline-flex items-center gap-2 rounded-full bg-[var(--surface-2)]
                               px-2.5 py-1 text-[0.75rem] text-[var(--ink-2)]
                               transition-colors hover:bg-[var(--surface-3)]"
                  >
                    {h.name} · {h.day}
                    <span className="text-[var(--ink-4)] group-hover:text-[var(--critical)]">×</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="mt-3 t-meta">No holidays yet.</p>
            )}
          </div>
        </div>
      )}

      {composing && canWrite && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (emp === UNSET || type === UNSET || !start || !end) {
              setError("Choose a person, a leave type, and both dates.");
              return;
            }
            createRequest.mutate();
          }}
          className="surface-raised pop mb-8 flex flex-wrap items-end gap-3 p-5"
        >
          <div className="space-y-1.5">
            <Label className="t-micro">Person</Label>
            <Select value={emp} onValueChange={(v) => setEmp(v ?? UNSET)}>
              <SelectTrigger className="w-44">
                <SelectValue>{(v: string) => (v === UNSET ? "Choose" : nameOf(v))}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {employees?.map((e) => (
                  <SelectItem key={e.id} value={e.id}>
                    {e.full_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="t-micro">Type</Label>
            <Select value={type} onValueChange={(v) => setType(v ?? UNSET)}>
              <SelectTrigger className="w-40">
                <SelectValue>{(v: string) => (v === UNSET ? "Choose" : typeOf(v))}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {types?.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
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
          <Action type="submit" disabled={createRequest.isPending}>
            Submit
          </Action>
        </form>
      )}

      {isLoading && <Empty>Loading…</Empty>}

      {/* --- Needs a decision ---------------------------------------------- */}
      {pending.length > 0 && (
        <section className="mb-12">
          <SectionLabel>Needs a decision</SectionLabel>
          <div className="stagger space-y-2.5">
            {pending.map((r, i) => (
              <div
                key={r.id}
                style={{ "--i": i } as React.CSSProperties}
                className="surface flex flex-wrap items-center gap-4 p-4"
              >
                <Avatar name={nameOf(r.employee_id)} size={38} />
                <div className="min-w-0 flex-1">
                  <p className="text-[0.9375rem] font-medium">{nameOf(r.employee_id)}</p>
                  <p className="text-[0.8125rem] text-[var(--ink-3)]">
                    {typeOf(r.leave_type_id)} · {r.days} {r.days === 1 ? "day" : "days"} ·{" "}
                    {r.start_date} → {r.end_date}
                  </p>
                </div>
                {canApprove && (
                  <div className="flex gap-2">
                    <Action
                      size="sm"
                      onClick={() => decide.mutate({ id: r.id, action: "approve" })}
                      disabled={decide.isPending}
                    >
                      <Check size={15} />
                      Approve
                    </Action>
                    <Action
                      size="sm"
                      variant="quiet"
                      onClick={() => decide.mutate({ id: r.id, action: "reject" })}
                      disabled={decide.isPending}
                    >
                      <X size={15} />
                      Decline
                    </Action>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* --- Everything else ------------------------------------------------ */}
      <section>
        <SectionLabel>Recent</SectionLabel>
        {decided.length === 0 ? (
          <Empty>No decided leave yet.</Empty>
        ) : (
          <div className="-mx-3">
            {decided.map((r) => (
              <div
                key={r.id}
                className="flex items-center gap-4 rounded-[10px] px-3 py-3 hover:bg-[var(--surface-1)]"
              >
                <span
                  className={`size-1.5 shrink-0 rounded-full ${
                    r.status === "approved" ? "bg-[var(--positive)]" : "bg-[var(--ink-4)]"
                  }`}
                />
                <span className="min-w-0 flex-1 truncate text-[0.875rem]">
                  {nameOf(r.employee_id)}
                  <span className="text-[var(--ink-3)]">
                    {" · "}
                    {typeOf(r.leave_type_id)} · {r.days}d · {r.start_date}
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
