"use client";
import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { askLumoGlobally } from "@/components/lamoon/command-palette";
import { LumoMark } from "@/components/lamoon/lumo";
import { Kbd, SectionLabel } from "@/components/lamoon/primitives";

/* The AI Workspace.

   Explicitly NOT a dashboard: no widget grid, no KPI tiles, no charts you
   didn't ask for. You get a greeting, one input, and a short list of things
   that actually need a human — each one a real count from live data, not a
   decorative number. If nothing needs you, the list says so and shuts up. */

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

type Signal = { label: string; detail: string; href: string; urgent?: boolean };

export default function HomePage() {
  const [q, setQ] = useState("");

  const { data: me } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const { data: leaveRequests } = useQuery({
    queryKey: ["leave-requests"],
    queryFn: api.leave.requests.list,
  });
  const { data: applications } = useQuery({
    queryKey: ["applications"],
    queryFn: api.applications.list,
  });
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: api.jobs.list });
  const { data: employees } = useQuery({
    queryKey: ["employees"],
    queryFn: () => api.employees.list(),
  });

  const firstName =
    me?.full_name?.split(" ")[0] ??
    (me?.email ? me.email.split("@")[0].replace(/[._]/g, " ") : null);

  const signals: Signal[] = [];
  const pendingLeave = leaveRequests?.filter((r) => r.status === "pending").length ?? 0;
  if (pendingLeave)
    signals.push({
      label: `${pendingLeave} leave ${pendingLeave === 1 ? "request" : "requests"}`,
      detail: "waiting on a decision",
      href: "/time",
      urgent: true,
    });

  const tierA = applications?.filter((a) => a.tier === "A" && a.status === "shortlisted").length ?? 0;
  if (tierA)
    signals.push({
      label: `${tierA} Tier A ${tierA === 1 ? "candidate" : "candidates"}`,
      detail: "ready to move to interview",
      href: "/hiring",
      urgent: true,
    });

  const scheduled = applications?.filter((a) => a.status === "interview_scheduled").length ?? 0;
  if (scheduled)
    signals.push({
      label: `${scheduled} ${scheduled === 1 ? "interview" : "interviews"}`,
      detail: "scheduled",
      href: "/hiring",
    });

  const openRoles = jobs?.filter((j) => j.status === "open").length ?? 0;
  if (openRoles)
    signals.push({
      label: `${openRoles} open ${openRoles === 1 ? "role" : "roles"}`,
      detail: "collecting applications",
      href: "/hiring",
    });

  const loading = !leaveRequests && !applications && !jobs;

  return (
    <div className="stagger">
      {/* --- Greeting ------------------------------------------------------ */}
      <header style={{ "--i": 0 } as React.CSSProperties}>
        <h1 className="t-display">
          {greeting()}
          {firstName && (
            <>
              ,{" "}
              <span className="capitalize text-[var(--ink-2)]">{firstName}</span>
            </>
          )}
          .
        </h1>
        <p className="mt-3 text-[1.0625rem] text-[var(--ink-3)]">
          What would you like to do today?
        </p>
      </header>

      {/* --- The ask -------------------------------------------------------- */}
      <form
        style={{ "--i": 1 } as React.CSSProperties}
        onSubmit={(e) => {
          e.preventDefault();
          if (!q.trim()) return;
          askLumoGlobally(q.trim());
          setQ("");
        }}
        className="mt-8"
      >
        <div
          className="surface-raised flex items-center gap-3.5 px-5 py-4 transition-shadow
                     duration-300 focus-within:lumo-glow"
        >
          <LumoMark size={26} />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask about your people, hiring, or time off…"
            aria-label="Ask Lumo"
            className="min-w-0 flex-1 bg-transparent text-[1.0625rem] outline-none
                       placeholder:text-[var(--ink-4)]"
          />
          <span className="hidden sm:block">
            <Kbd>⌘J</Kbd>
          </span>
        </div>
      </form>

      <div
        style={{ "--i": 2 } as React.CSSProperties}
        className="mt-3 flex flex-wrap gap-2"
      >
        {["Who's on leave today?", "Find Tier A candidates", "How many people do we have?"].map(
          (s) => (
            <button
              key={s}
              onClick={() => askLumoGlobally(s)}
              className="rounded-full bg-[var(--surface-2)] px-3.5 py-1.5 text-[0.8125rem]
                         text-[var(--ink-2)] transition-colors hover:bg-[var(--surface-3)]"
            >
              {s}
            </button>
          )
        )}
      </div>

      {/* --- Signals -------------------------------------------------------- */}
      <section style={{ "--i": 3 } as React.CSSProperties} className="mt-14">
        <SectionLabel>Needs you</SectionLabel>

        {loading && <p className="t-meta">Checking…</p>}

        {!loading && signals.length === 0 && (
          <p className="t-body text-[var(--ink-3)]">
            Nothing needs a decision right now.
          </p>
        )}

        <div className="-mx-3">
          {signals.map((s) => (
            <Link
              key={s.label}
              href={s.href}
              className="group flex items-center gap-4 rounded-[12px] px-3 py-3.5
                         transition-colors hover:bg-[var(--surface-1)]"
            >
              <span
                className={`size-1.5 shrink-0 rounded-full ${
                  s.urgent ? "bg-[var(--caution)]" : "bg-[var(--ink-4)]"
                }`}
              />
              <span className="min-w-0 flex-1">
                <span className="text-[0.9375rem] text-[var(--ink-1)]">{s.label}</span>{" "}
                <span className="text-[0.9375rem] text-[var(--ink-3)]">{s.detail}</span>
              </span>
              <ArrowRight
                size={16}
                className="shrink-0 text-[var(--ink-4)] opacity-0 transition-all
                           duration-200 group-hover:translate-x-0.5 group-hover:opacity-100"
              />
            </Link>
          ))}
        </div>
      </section>

      {/* --- Quiet context -------------------------------------------------- */}
      {employees && (
        <section style={{ "--i": 4 } as React.CSSProperties} className="mt-12">
          <SectionLabel>Company</SectionLabel>
          <p className="t-body text-[var(--ink-2)]">
            <Link href="/people" className="underline-offset-4 hover:underline">
              {employees.length} {employees.length === 1 ? "person" : "people"}
            </Link>
            {" · "}
            {employees.filter((e) => e.status === "active").length} active
            {jobs ? ` · ${jobs.filter((j) => j.status === "open").length} roles open` : ""}
          </p>
        </section>
      )}
    </div>
  );
}
