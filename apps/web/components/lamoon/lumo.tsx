"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { askLumo, type LumoAnswer } from "@/lib/lumo-brain";
import { Avatar, Kbd } from "@/components/lamoon/primitives";

/** The iridescent mark. This is the only chroma in the product — if you see
    color anywhere else, that's a bug (see globals.css governing rule). */
export function LumoMark({ size = 28, thinking }: { size?: number; thinking?: boolean }) {
  return (
    <span
      className="lumo-ring relative inline-block shrink-0 rounded-full"
      style={{
        width: size,
        height: size,
        animation: thinking ? "breathe 1.4s ease-in-out infinite" : undefined,
      }}
    >
      <span
        className="absolute inset-[3px] rounded-full"
        style={{ background: "var(--surface-0)" }}
      />
      <span
        className="lumo-ring absolute rounded-full"
        style={{ inset: size * 0.3 }}
      />
    </span>
  );
}

function Thinking() {
  return (
    <span className="inline-flex items-center gap-1" aria-label="Lumo is thinking">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 rounded-full bg-[var(--lumo)]"
          style={{ animation: `think 1.1s ease-in-out ${i * 0.15}s infinite` }}
        />
      ))}
    </span>
  );
}

type Turn = { q: string; a: LumoAnswer | null };

/** Floating assistant. Opens with the Lumo orb or ⌘J / Ctrl+J. */
export function Lumo() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const lastAnswer = [...turns].reverse().find((t) => t.a)?.a ?? null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen((v) => !v);
      }
      if (e.key === "Escape") setOpen(false);
    };
    // Anything in the app can hand Lumo a question — the command palette and
    // the home input both do. An event keeps them from having to own Lumo's
    // state (or Lumo theirs).
    const onAsk = (e: Event) => {
      const q = (e as CustomEvent<string>).detail;
      setOpen(true);
      if (q) void ask(q);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("lumo:ask", onAsk);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("lumo:ask", onAsk);
    };
    // `ask` is stable enough for this listener's lifetime; re-binding on every
    // turn would drop in-flight questions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    setTurns((t) => [...t, { q, a: null }]);
    setBusy(true);
    try {
      const a = await askLumo(q);
      setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, a } : turn)));
    } catch {
      setTurns((t) =>
        t.map((turn, i) =>
          i === t.length - 1
            ? { ...turn, a: { text: "I couldn't reach your data just then. Try again?" } }
            : turn
        )
      );
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        aria-label="Open Lumo"
        className="lumo-glow fixed right-6 bottom-6 z-40 grid size-12 place-items-center rounded-full
                   bg-[var(--surface-1)] transition-transform duration-200
                   ease-[var(--ease-out-expo)] hover:scale-105 active:scale-95"
      >
        <LumoMark size={26} />
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 sm:inset-auto sm:right-6 sm:bottom-6">
      {/* Mobile takes the full screen; desktop gets a floating panel. */}
      <div
        className="pop flex h-full w-full flex-col bg-[var(--surface-1)] shadow-2xl
                   sm:h-[min(620px,80vh)] sm:w-[420px] sm:rounded-[var(--radius-2xl)]"
      >
        <header className="flex items-center justify-between px-5 py-4">
          <span className="flex items-center gap-2.5">
            <LumoMark size={24} thinking={busy} />
            <span className="text-[0.9375rem] font-medium">Lumo</span>
            {/* Honest about which path answered. Both are grounded in real
                data; only the phrasing differs. */}
            {lastAnswer && !lastAnswer.modelUsed && (
              <span
                className="rounded-full bg-[var(--surface-3)] px-2 py-0.5 text-[0.6875rem] text-[var(--ink-3)]"
                title="No GEMINI_API_KEY configured — answers come from a deterministic router over your data."
              >
                direct
              </span>
            )}
          </span>
          <button
            onClick={() => setOpen(false)}
            className="rounded-md p-1 text-[var(--ink-3)] hover:bg-[var(--surface-2)]"
            aria-label="Close Lumo"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto px-5 pb-4">
          {turns.length === 0 && (
            <div className="pt-6">
              <p className="t-body text-[var(--ink-2)]">
                I read your live data — headcount, leave, hiring. Ask me something.
              </p>
              <div className="mt-4 space-y-1.5">
                {[
                  "Who's on leave today?",
                  "Show pending leave requests",
                  "Find Tier A candidates",
                  "What roles are open?",
                ].map((s) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    className="block w-full rounded-[10px] px-3 py-2 text-left text-[0.875rem]
                               text-[var(--ink-2)] transition-colors hover:bg-[var(--surface-2)]"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i} className="space-y-3">
              <p className="ml-auto w-fit max-w-[85%] rounded-[14px] bg-[var(--surface-3)] px-3.5 py-2 text-[0.875rem]">
                {t.q}
              </p>
              <div className="flex gap-2.5">
                <LumoMark size={22} thinking={!t.a} />
                <div className="min-w-0 flex-1 pt-0.5">
                  {!t.a ? (
                    <Thinking />
                  ) : (
                    <>
                      <p className="t-body text-[var(--ink-1)]">{t.a.text}</p>
                      {t.a.items && t.a.items.length > 0 && (
                        <div className="mt-2.5 space-y-0.5">
                          {t.a.items.map((item, j) => (
                            <button
                              key={j}
                              disabled={!item.href && !t.a?.unmatched}
                              onClick={() => {
                                if (item.href) {
                                  router.push(item.href);
                                  setOpen(false);
                                } else if (t.a?.unmatched) {
                                  ask(item.title);
                                }
                              }}
                              className="flex w-full items-center justify-between gap-3 rounded-[9px] px-2.5
                                         py-1.5 text-left transition-colors enabled:hover:bg-[var(--surface-2)]
                                         disabled:cursor-default"
                            >
                              <span className="truncate text-[0.875rem]">{item.title}</span>
                              {item.meta && (
                                <span className="shrink-0 text-[0.75rem] text-[var(--ink-3)]">
                                  {item.meta}
                                </span>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
          className="hairline-t p-3"
        >
          <div className="flex items-center gap-2 rounded-[14px] bg-[var(--surface-2)] px-3.5 py-2.5">
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask Lumo…"
              className="min-w-0 flex-1 bg-transparent text-[0.9375rem] outline-none
                         placeholder:text-[var(--ink-4)]"
            />
            <Kbd>esc</Kbd>
          </div>
        </form>
      </div>
    </div>
  );
}

/** Small avatar+name row reused by Lumo results and lists. */
export function PersonRow({ name, meta }: { name: string; meta?: string }) {
  return (
    <span className="flex items-center gap-2.5">
      <Avatar name={name} size={28} />
      <span className="min-w-0">
        <span className="block truncate text-[0.875rem]">{name}</span>
        {meta && <span className="block truncate text-[0.75rem] text-[var(--ink-3)]">{meta}</span>}
      </span>
    </span>
  );
}
