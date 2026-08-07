"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Avatar, Kbd } from "@/components/lamoon/primitives";
import { LumoMark } from "@/components/lamoon/lumo";

/* Universal search + quick actions. ⌘K / Ctrl+K from anywhere.

   Design intent: this is the PRIMARY way to move around Lamoon. The left rail
   is the fallback for people who'd rather point. Everything is reachable in
   two keystrokes, which is why the rail can afford to stay collapsed. */

type Cmd = {
  id: string;
  label: string;
  hint?: string;
  group: "Go" | "Create" | "People" | "Ask Lumo";
  run: () => void;
  avatar?: string;
};

const NAV: Array<{ label: string; href: string; hint: string }> = [
  { label: "Home", href: "/home", hint: "Your workspace" },
  { label: "Hiring", href: "/hiring", hint: "Pipeline & candidates" },
  { label: "People", href: "/people", hint: "Directory" },
  { label: "Time", href: "/time", hint: "Leave & balances" },
  { label: "Org", href: "/org", hint: "Departments" },
];

/** Hand a question to Lumo from anywhere. */
export function askLumoGlobally(q: string) {
  window.dispatchEvent(new CustomEvent("lumo:ask", { detail: q }));
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const [people, setPeople] = useState<Array<{ id: string; full_name: string; email: string | null }>>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Mirrors `open` for the keydown listener, which is bound once and would
  // otherwise close over a stale value.
  const openRef = useRef(false);
  useEffect(() => {
    openRef.current = open;
  }, [open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        // Reset here, in the event handler, rather than in an effect watching
        // `open` — same result without the extra render pass.
        if (!openRef.current) {
          setQ("");
          setCursor(0);
        }
        setOpen(!openRef.current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Load people the first time the palette opens — not on app boot.
  useEffect(() => {
    if (open && people.length === 0) {
      api.employees
        .list()
        .then((rows) => setPeople(rows.map((r) => ({ id: r.id, full_name: r.full_name, email: r.email }))))
        .catch(() => {});
    }
  }, [open, people.length]);

  // Pure DOM sync — focus follows the panel opening.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const commands = useMemo<Cmd[]>(() => {
    const go: Cmd[] = NAV.map((n) => ({
      id: `go:${n.href}`,
      label: n.label,
      hint: n.hint,
      group: "Go",
      run: () => router.push(n.href),
    }));

    const create: Cmd[] = [
      { id: "c:person", label: "Add a person", group: "Create", run: () => router.push("/people?new=1") },
      { id: "c:leave", label: "File leave", group: "Create", run: () => router.push("/time?new=1") },
      { id: "c:dept", label: "New department", group: "Create", run: () => router.push("/org?new=1") },
    ];

    const persons: Cmd[] = people.map((p) => ({
      id: `p:${p.id}`,
      label: p.full_name,
      hint: p.email ?? undefined,
      group: "People",
      avatar: p.full_name,
      run: () => router.push(`/people/${p.id}`),
    }));

    const ask: Cmd[] = q.trim()
      ? [
          {
            id: "ask",
            label: `Ask Lumo “${q.trim()}”`,
            group: "Ask Lumo",
            run: () => askLumoGlobally(q.trim()),
          },
        ]
      : [];

    return [...ask, ...go, ...create, ...persons];
  }, [people, q, router]);

  const filtered = useMemo(() => {
    const needle = q.toLowerCase().trim();
    if (!needle) return commands.filter((c) => c.group !== "People").slice(0, 12);
    return commands
      .filter(
        (c) =>
          c.group === "Ask Lumo" ||
          c.label.toLowerCase().includes(needle) ||
          c.hint?.toLowerCase().includes(needle)
      )
      .slice(0, 14);
  }, [commands, q]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") return setOpen(false);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, filtered.length - 1));
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const cmd = filtered[cursor];
      if (cmd) {
        cmd.run();
        setOpen(false);
      }
    }
  }

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-idx="${cursor}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (!open) return null;

  let lastGroup = "";

  return (
    <div
      className="fade fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]"
      style={{ background: "oklch(0 0 0 / 32%)", backdropFilter: "blur(4px)" }}
      onClick={() => setOpen(false)}
    >
      <div
        className="pop w-full max-w-[600px] overflow-hidden rounded-[var(--radius-xl)]
                   bg-[var(--surface-1)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-5 py-4">
          <span className="text-[var(--ink-4)]">⌕</span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setCursor(0); // a new query invalidates the old highlight
            }}
            onKeyDown={onKeyDown}
            placeholder="Search or ask…"
            className="flex-1 bg-transparent text-[1rem] outline-none placeholder:text-[var(--ink-4)]"
          />
          <Kbd>esc</Kbd>
        </div>

        <div ref={listRef} className="hairline-t max-h-[52vh] overflow-y-auto p-2">
          {filtered.length === 0 && (
            <p className="px-3 py-6 text-center text-[0.875rem] text-[var(--ink-3)]">
              Nothing matched.
            </p>
          )}
          {filtered.map((c, i) => {
            const showGroup = c.group !== lastGroup;
            lastGroup = c.group;
            return (
              <div key={c.id}>
                {showGroup && <div className="t-micro px-3 pt-3 pb-1.5">{c.group}</div>}
                <button
                  data-idx={i}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => {
                    c.run();
                    setOpen(false);
                  }}
                  className={`flex w-full items-center gap-3 rounded-[10px] px-3 py-2.5 text-left
                              transition-colors ${
                                i === cursor ? "bg-[var(--surface-3)]" : ""
                              }`}
                >
                  {c.group === "Ask Lumo" ? (
                    <LumoMark size={22} />
                  ) : c.avatar ? (
                    <Avatar name={c.avatar} size={22} />
                  ) : (
                    <span className="grid size-[22px] place-items-center text-[var(--ink-4)]">→</span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-[0.9375rem]">{c.label}</span>
                  {c.hint && (
                    <span className="shrink-0 truncate text-[0.75rem] text-[var(--ink-4)]">
                      {c.hint}
                    </span>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
