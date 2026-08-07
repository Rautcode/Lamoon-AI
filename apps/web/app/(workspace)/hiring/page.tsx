"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Application } from "@/lib/types";
import { askLumoGlobally } from "@/components/lamoon/command-palette";
import { LumoMark } from "@/components/lamoon/lumo";
import { Action, Avatar, Empty, Pill, SectionLabel } from "@/components/lamoon/primitives";

/* Hiring pipeline.

   A board, not a table — you're triaging humans, and a spreadsheet row is a
   terrible container for a person. Cards carry the AI score because that's
   the product's actual differentiator; everything else is quiet.

   ponytail: read-only board for now. Drag-to-move needs stage-transition
   endpoints the API doesn't expose yet (it has screen/advance/reject, not a
   general "set stage"), and a board that silently fails to persist a drag is
   worse than one that doesn't offer it. */

const COLUMNS: Array<{ key: string; label: string; match: (s: string) => boolean }> = [
  { key: "new", label: "New", match: (s) => s === "received" },
  { key: "screening", label: "Screening", match: (s) => s === "needs_review" || s === "screening" },
  { key: "shortlist", label: "Shortlisted", match: (s) => s === "shortlisted" },
  {
    key: "interview",
    label: "Interview",
    match: (s) => s === "interview_proposed" || s === "interview_scheduled",
  },
  { key: "closed", label: "Closed", match: (s) => s === "rejected" || s === "pending_reject" },
];

function ScoreDial({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(1, score / 10));
  const r = 13;
  const c = 2 * Math.PI * r;
  return (
    <span className="relative grid size-9 shrink-0 place-items-center" title={`AI score ${score}/10`}>
      <svg viewBox="0 0 32 32" className="absolute size-9 -rotate-90">
        <circle cx="16" cy="16" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="2.5" />
        <circle
          cx="16"
          cy="16"
          r={r}
          fill="none"
          stroke="url(#lumoGrad)"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
        />
        <defs>
          <linearGradient id="lumoGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--lumo)" />
            <stop offset="100%" stopColor="var(--lumo-2)" />
          </linearGradient>
        </defs>
      </svg>
      <span className="relative text-[0.6875rem] font-semibold tabular-nums">
        {score.toFixed(1)}
      </span>
    </span>
  );
}

function CandidateCard({ app, onOpen }: { app: Application; onOpen: () => void }) {
  const name = app.candidate_name ?? "Unnamed candidate";
  return (
    <button
      onClick={onOpen}
      className="surface w-full p-3.5 text-left transition-all duration-200
                 ease-[var(--ease-out-expo)] hover:-translate-y-0.5 hover:shadow-[0_8px_24px_-14px_oklch(0_0_0/20%)]"
    >
      <div className="flex items-start gap-3">
        <Avatar name={name} size={34} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.875rem] font-medium">{name}</p>
          {app.tier && (
            <p className="mt-0.5 text-[0.75rem] text-[var(--ink-3)]">
              Tier {app.tier}
              {app.recommended_action ? ` · ${app.recommended_action}` : ""}
            </p>
          )}
        </div>
        {app.final_score != null && <ScoreDial score={app.final_score} />}
      </div>
      {app.summary && (
        <p className="mt-2.5 line-clamp-2 text-[0.8125rem] leading-relaxed text-[var(--ink-3)]">
          {app.summary}
        </p>
      )}
    </button>
  );
}

/** Right-hand inspector. Slides over on desktop, full-screen on mobile. */
function Inspector({ app, onClose }: { app: Application; onClose: () => void }) {
  const name = app.candidate_name ?? "Unnamed candidate";
  return (
    <div className="fixed inset-0 z-40" onClick={onClose}>
      <div className="absolute inset-0 bg-[oklch(0_0_0/28%)]" />
      <aside
        onClick={(e) => e.stopPropagation()}
        className="pop absolute inset-y-0 right-0 flex w-full flex-col bg-[var(--surface-1)]
                   shadow-2xl sm:w-[440px]"
      >
        <header className="flex items-start justify-between gap-4 p-6">
          <div className="flex min-w-0 items-center gap-3.5">
            <Avatar name={name} size={46} />
            <div className="min-w-0">
              <h2 className="t-title truncate">{name}</h2>
              {app.candidate_email && (
                <p className="truncate text-[0.8125rem] text-[var(--ink-3)]">
                  {app.candidate_email}
                </p>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-[var(--ink-3)] hover:bg-[var(--surface-2)]"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 space-y-8 overflow-y-auto px-6 pb-6">
          <div className="flex flex-wrap items-center gap-2">
            <Pill>{app.status.replace(/_/g, " ")}</Pill>
            {app.tier && <Pill>Tier {app.tier}</Pill>}
            {app.recommended_action && <Pill>{app.recommended_action}</Pill>}
          </div>

          {app.final_score != null && (
            <section>
              <SectionLabel>AI assessment</SectionLabel>
              <div className="flex items-center gap-4">
                <ScoreDial score={app.final_score} />
                <p className="t-meta">
                  Screened by Gemini against the role&apos;s required skills, then
                  scored deterministically — 60% model, 40% rule-based match.
                </p>
              </div>
            </section>
          )}

          {app.summary && (
            <section>
              <SectionLabel>Summary</SectionLabel>
              <p className="t-body text-[var(--ink-2)]">{app.summary}</p>
            </section>
          )}

          <section>
            <SectionLabel>Ask about this candidate</SectionLabel>
            <button
              onClick={() => askLumoGlobally(`Tell me about ${name}`)}
              className="flex w-full items-center gap-3 rounded-[12px] bg-[var(--surface-2)]
                         px-4 py-3 text-left transition-colors hover:bg-[var(--surface-3)]"
            >
              <LumoMark size={22} />
              <span className="text-[0.875rem] text-[var(--ink-2)]">
                Ask Lumo about {name.split(" ")[0]}
              </span>
            </button>
          </section>
        </div>
      </aside>
    </div>
  );
}

export default function HiringPage() {
  const [selected, setSelected] = useState<Application | null>(null);
  const { data: applications, isLoading } = useQuery({
    queryKey: ["applications"],
    queryFn: api.applications.list,
  });
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: api.jobs.list });

  const openRoles = jobs?.filter((j) => j.status === "open").length ?? 0;

  return (
    <div className="fade">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="t-display">Hiring</h1>
          <p className="mt-2 t-meta">
            {applications?.length ?? 0} in pipeline · {openRoles} open{" "}
            {openRoles === 1 ? "role" : "roles"}
          </p>
        </div>
        <Action variant="quiet" onClick={() => askLumoGlobally("Find Tier A candidates")}>
          <LumoMark size={18} />
          Ask Lumo
        </Action>
      </header>

      {isLoading && <Empty>Loading pipeline…</Empty>}

      {applications && applications.length === 0 && (
        <Empty>No applications yet. They&apos;ll appear here as candidates apply.</Empty>
      )}

      {applications && applications.length > 0 && (
        /* Horizontal scroll on narrow screens — a board should stay a board on
           tablet rather than collapsing into a list that hides the stages. */
        <div className="-mx-5 flex gap-4 overflow-x-auto px-5 pb-4 sm:mx-0 sm:px-0">
          {COLUMNS.map((col) => {
            const items = applications.filter((a) => col.match(a.status));
            return (
              <section key={col.key} className="w-[272px] shrink-0">
                <div className="mb-3 flex items-baseline justify-between px-1">
                  <h2 className="t-micro">{col.label}</h2>
                  <span className="text-[0.75rem] tabular-nums text-[var(--ink-4)]">
                    {items.length}
                  </span>
                </div>
                <div className="stagger space-y-2.5">
                  {items.map((a, i) => (
                    <div key={a.id} style={{ "--i": i } as React.CSSProperties}>
                      <CandidateCard app={a} onOpen={() => setSelected(a)} />
                    </div>
                  ))}
                  {items.length === 0 && (
                    <div className="rounded-[12px] border border-dashed border-[var(--hairline)] py-8 text-center text-[0.75rem] text-[var(--ink-4)]">
                      Empty
                    </div>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {selected && <Inspector app={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
