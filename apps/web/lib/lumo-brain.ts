import { api } from "@/lib/api";

/* ============================================================================
   LUMO v1 — a deterministic intent router over the real API.

   This is NOT an LLM and does not pretend to be. Every answer below is
   computed from live data via the same endpoints the UI uses, so Lumo can
   never state something the product doesn't actually know. The tradeoff is
   that it only understands the intents enumerated here; anything else gets
   an honest "I can't do that yet" plus what it CAN do.

   Wiring this to Gemini for open-ended language is the obvious next step —
   the backend already has an AIProvider seam (core/ai/provider.py), currently
   used only for resume screening. When that lands, this file becomes the
   tool-call layer the model routes THROUGH, not a thing it replaces: the
   grounding-in-real-data property is the valuable part and should survive.
   ============================================================================ */

export type LumoItem = { title: string; meta?: string; href?: string };
export type LumoAnswer = {
  text: string;
  items?: LumoItem[];
  /** Set when Lumo genuinely couldn't parse the request — drives the UI to
      show capabilities rather than a dead end. */
  unmatched?: boolean;
};

const has = (q: string, ...words: string[]) => words.some((w) => q.includes(w));

export async function askLumo(raw: string): Promise<LumoAnswer> {
  const q = raw.toLowerCase().trim();
  if (!q) return { text: "Ask me anything about your people, hiring, or time off." };

  // --- headcount ----------------------------------------------------------
  if (has(q, "how many", "headcount", "team size", "total employees")) {
    const employees = await api.employees.list();
    const active = employees.filter((e) => e.status === "active").length;
    return {
      text: `${employees.length} people on the books — ${active} active, ${
        employees.length - active
      } on probation or exited.`,
      items: employees.slice(0, 5).map((e) => ({
        title: e.full_name,
        meta: e.status,
        href: `/people/${e.id}`,
      })),
    };
  }

  // --- leave --------------------------------------------------------------
  if (has(q, "leave", "time off", "vacation", "holiday", "absent")) {
    const [requests, employees] = await Promise.all([
      api.leave.requests.list(),
      api.employees.list(),
    ]);
    const nameOf = (id: string) => employees.find((e) => e.id === id)?.full_name ?? "Someone";

    if (has(q, "pending", "approve", "waiting", "awaiting")) {
      const pending = requests.filter((r) => r.status === "pending");
      return {
        text: pending.length
          ? `${pending.length} leave ${pending.length === 1 ? "request needs" : "requests need"} a decision.`
          : "Nothing waiting on you — all leave requests are decided.",
        items: pending.map((r) => ({
          title: nameOf(r.employee_id),
          meta: `${r.days}d · ${r.start_date} → ${r.end_date}`,
          href: "/time",
        })),
      };
    }

    const today = new Date().toISOString().slice(0, 10);
    const out = requests.filter(
      (r) => r.status === "approved" && r.start_date <= today && r.end_date >= today
    );
    return {
      text: out.length
        ? `${out.length} ${out.length === 1 ? "person is" : "people are"} on leave today.`
        : "Nobody is on leave today.",
      items: out.map((r) => ({
        title: nameOf(r.employee_id),
        meta: `back ${r.end_date}`,
        href: "/time",
      })),
    };
  }

  // --- hiring -------------------------------------------------------------
  if (has(q, "candidate", "applicant", "hiring", "shortlist", "tier", "interview")) {
    const [applications, jobs] = await Promise.all([api.applications.list(), api.jobs.list()]);
    const titleOf = (id: string | null) =>
      jobs.find((j) => j.id === id)?.title ?? "Unassigned role";

    const wantsTopTier = has(q, "tier a", "best", "top", "shortlist", "strongest");
    const pool = wantsTopTier ? applications.filter((a) => a.tier === "A") : applications;

    return {
      text: pool.length
        ? `${pool.length} ${wantsTopTier ? "Tier A " : ""}${
            pool.length === 1 ? "candidate" : "candidates"
          } in the pipeline.`
        : "No candidates match that yet.",
      items: pool.slice(0, 6).map((a) => ({
        title: titleOf(a.job_opening_id),
        meta: `${a.tier ? `Tier ${a.tier} · ` : ""}${a.status.replace(/_/g, " ")}`,
        href: "/hiring",
      })),
    };
  }

  // --- open roles ---------------------------------------------------------
  if (has(q, "job", "role", "opening", "position", "vacanc")) {
    const jobs = await api.jobs.list();
    const open = jobs.filter((j) => j.status === "open");
    return {
      text: `${open.length} open ${open.length === 1 ? "role" : "roles"}.`,
      items: open.slice(0, 6).map((j) => ({ title: j.title, meta: j.status, href: "/hiring" })),
    };
  }

  // --- org ----------------------------------------------------------------
  if (has(q, "department", "team", "org", "structure", "reports")) {
    const departments = await api.departments.list();
    return {
      text: `${departments.length} ${departments.length === 1 ? "department" : "departments"}.`,
      items: departments.slice(0, 8).map((d) => ({ title: d.name, href: "/org" })),
    };
  }

  // --- people lookup by name ---------------------------------------------
  const employees = await api.employees.list().catch(() => []);
  const match = employees.filter((e) =>
    q.split(/\s+/).some((w) => w.length > 2 && e.full_name.toLowerCase().includes(w))
  );
  if (match.length) {
    return {
      text: match.length === 1 ? `Here's ${match[0].full_name}.` : `${match.length} people match.`,
      items: match.slice(0, 6).map((e) => ({
        title: e.full_name,
        meta: e.email ?? e.status,
        href: `/people/${e.id}`,
      })),
    };
  }

  // --- honest fallback ----------------------------------------------------
  return {
    unmatched: true,
    text: "I can't answer that one yet. Here's what I can do today:",
    items: [
      { title: "Who's on leave today?" },
      { title: "Show me pending leave requests" },
      { title: "Find Tier A candidates" },
      { title: "How many people do we have?" },
      { title: "What roles are open?" },
    ],
  };
}
