"use client";
import { use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { askLumoGlobally } from "@/components/lamoon/command-palette";
import { LumoMark } from "@/components/lamoon/lumo";
import { Action, Avatar, Empty, Pill, SectionLabel, Status } from "@/components/lamoon/primitives";
import { SalaryEditor } from "@/components/lamoon/salary-editor";

/* One intelligent page per person.

   Everything here is real: identity, department, leave balance, leave history.
   Performance / assets / documents / goals are deliberately ABSENT rather than
   stubbed — those modules don't exist in the API yet, and a greyed-out
   "Performance" panel that never fills in is a promise the product can't keep. */

const TONE = { active: "positive", probation: "caution", exited: "neutral" } as const;

/** Apple-Health-style ring. Reads at a glance; no legend needed. */
function BalanceRing({ used, allocated, label }: { used: number; allocated: number; label: string }) {
  const pct = allocated > 0 ? Math.min(1, used / allocated) : 0;
  const r = 26;
  const c = 2 * Math.PI * r;
  const remaining = allocated - used;
  return (
    <div className="flex flex-col items-center gap-2.5">
      <span className="relative grid size-[68px] place-items-center">
        <svg viewBox="0 0 64 64" className="absolute size-[68px] -rotate-90">
          <circle cx="32" cy="32" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="5" />
          <circle
            cx="32"
            cy="32"
            r={r}
            fill="none"
            stroke="var(--ink-2)"
            strokeWidth="5"
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={c * (1 - pct)}
            style={{ transition: "stroke-dashoffset 600ms var(--ease-out-expo)" }}
          />
        </svg>
        <span className="relative text-center leading-none">
          <span className="block text-[1.0625rem] font-medium tabular-nums">{remaining}</span>
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

export default function PersonPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const queryClient = useQueryClient();
  const permissions = useAuthStore((s) => s.permissions);
  const canWrite = hasPermission(permissions, "employee.write");
  // Pay is a tighter boundary than the rest of this page: HR/admin only, and
  // deliberately not managers.
  const canSeePay = hasPermission(permissions, "payroll.read");
  const canEditPay = hasPermission(permissions, "payroll.write");

  const invite = useMutation({
    mutationFn: () => api.employees.invite(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["employee", id] }),
  });

  const { data: person, isLoading, isError } = useQuery({
    queryKey: ["employee", id],
    queryFn: () => api.employees.get(id),
  });
  const { data: departments } = useQuery({ queryKey: ["departments"], queryFn: api.departments.list });
  const { data: balances } = useQuery({
    queryKey: ["leave-balances", id],
    queryFn: () => api.leave.balances(id),
  });
  const { data: requests } = useQuery({
    queryKey: ["leave-requests"],
    queryFn: api.leave.requests.list,
  });

  const deptName = departments?.find((d) => d.id === person?.department_id)?.name;
  const history = requests?.filter((r) => r.employee_id === id) ?? [];

  if (isLoading) return <Empty>Loading…</Empty>;
  if (isError || !person) return <Empty>Couldn&apos;t find that person.</Empty>;

  return (
    <div className="stagger">
      <Link
        href="/people"
        style={{ "--i": 0 } as React.CSSProperties}
        className="mb-6 inline-flex items-center gap-1.5 text-[0.8125rem] text-[var(--ink-3)]
                   transition-colors hover:text-[var(--ink-1)]"
      >
        <ArrowLeft size={14} /> People
      </Link>

      {/* --- Identity ------------------------------------------------------- */}
      <header
        style={{ "--i": 1 } as React.CSSProperties}
        className="flex flex-wrap items-center gap-5"
      >
        <Avatar name={person.full_name} size={76} />
        <div className="min-w-0">
          <h1 className="t-display">{person.full_name}</h1>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
            <Status tone={TONE[person.status] ?? "neutral"}>{person.status}</Status>
            {deptName && <Pill>{deptName}</Pill>}
            {person.joined_on && <Pill>Joined {person.joined_on}</Pill>}
          </div>
        </div>
      </header>

      {person.email && (
        <p style={{ "--i": 2 } as React.CSSProperties} className="mt-4 t-meta">
          {person.email}
        </p>
      )}

      {/* --- Time off ------------------------------------------------------- */}
      <section style={{ "--i": 3 } as React.CSSProperties} className="mt-12">
        <SectionLabel>Time off this year</SectionLabel>
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
          <p className="t-meta">No leave types configured yet.</p>
        )}
      </section>

      {/* --- History -------------------------------------------------------- */}
      <section style={{ "--i": 4 } as React.CSSProperties} className="mt-12">
        <SectionLabel>Leave history</SectionLabel>
        {history.length === 0 ? (
          <p className="t-meta">No leave taken yet.</p>
        ) : (
          <div className="-mx-3">
            {history.map((r) => (
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
                  {r.days} {r.days === 1 ? "day" : "days"}
                  <span className="text-[var(--ink-3)]">
                    {" · "}
                    {r.start_date} → {r.end_date}
                  </span>
                </span>
                <span className="shrink-0 text-[0.75rem] text-[var(--ink-3)]">{r.status}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* --- Salary ---------------------------------------------------------- */}
      {canSeePay && (
        <section style={{ "--i": 5 } as React.CSSProperties} className="mt-12">
          <SalaryEditor
            employeeId={id}
            firstName={person.full_name.split(" ")[0]}
            canWrite={canEditPay}
          />
        </section>
      )}

      {/* --- Self-service access -------------------------------------------- */}
      {canWrite && (
        <section style={{ "--i": 6 } as React.CSSProperties} className="mt-12">
          <SectionLabel>Self-service access</SectionLabel>
          {invite.isSuccess || person.user_id ? (
            <p className="t-meta">
              {person.full_name.split(" ")[0]} can sign in and manage their own time off.
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <Action
                variant="quiet"
                onClick={() => invite.mutate()}
                disabled={invite.isPending || !person.email}
              >
                {invite.isPending ? "Sending…" : "Give access"}
              </Action>
              <p className="t-meta">
                {person.email
                  ? "Emails them a temporary password to sign in with."
                  : "Add an email address first."}
              </p>
            </div>
          )}
          {invite.isError && (
            <p className="mt-2 text-[0.8125rem] text-[var(--critical)]">
              {invite.error instanceof ApiError ? invite.error.message : "Could not grant access."}
            </p>
          )}
        </section>
      )}

      {/* --- Lumo ----------------------------------------------------------- */}
      <section style={{ "--i": 7 } as React.CSSProperties} className="mt-12">
        <button
          onClick={() => askLumoGlobally(`Tell me about ${person.full_name}`)}
          className="flex w-full items-center gap-3 rounded-[14px] bg-[var(--surface-1)] px-4 py-3.5
                     text-left transition-colors hover:bg-[var(--surface-2)] sm:w-auto"
        >
          <LumoMark size={22} />
          <span className="text-[0.875rem] text-[var(--ink-2)]">
            Ask Lumo about {person.full_name.split(" ")[0]}
          </span>
        </button>
      </section>
    </div>
  );
}
