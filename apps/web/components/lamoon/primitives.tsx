"use client";
import { cn } from "@/lib/utils";

/* Lamoon primitives. Deliberately few: a small vocabulary used consistently
   beats a large one used approximately. Nothing here takes a `color` prop —
   chroma belongs to Lumo alone (see globals.css). */

export function Surface({
  className,
  raised,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { raised?: boolean }) {
  return <div className={cn(raised ? "surface-raised" : "surface", className)} {...props} />;
}

/** Section heading. The micro-label is the only ALL CAPS in the product. */
export function SectionLabel({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between">
      <h2 className="t-micro">{children}</h2>
      {action}
    </div>
  );
}

/** Status dot + text. Never a saturated fill — see the governing rule. */
export function Status({ tone, children }: { tone: "positive" | "caution" | "critical" | "neutral"; children: React.ReactNode }) {
  const dot = {
    positive: "bg-[var(--positive)]",
    caution: "bg-[var(--caution)]",
    critical: "bg-[var(--critical)]",
    neutral: "bg-[var(--ink-4)]",
  }[tone];
  return (
    <span className="inline-flex items-center gap-2 text-[0.8125rem] text-[var(--ink-2)]">
      <span className={cn("size-1.5 shrink-0 rounded-full", dot)} />
      {children}
    </span>
  );
}

export function Pill({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-[var(--surface-2)] px-2.5 py-1",
        "text-[0.75rem] font-medium text-[var(--ink-2)]",
        className
      )}
      {...props}
    />
  );
}

/** Deterministic initials — same person always gets the same tile. */
export function Avatar({ name, size = 36 }: { name: string; size?: number }) {
  const initials = name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  return (
    <span
      aria-hidden
      className="inline-grid shrink-0 place-items-center rounded-full bg-[var(--surface-3)] font-medium text-[var(--ink-2)]"
      style={{ width: size, height: size, fontSize: size * 0.36 }}
    >
      {initials || "?"}
    </span>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className="rounded-[5px] bg-[var(--surface-3)] px-1.5 py-0.5 font-sans text-[0.6875rem]
                 font-medium text-[var(--ink-3)]"
    >
      {children}
    </kbd>
  );
}

/** Primary action. Monochrome by rule — inverted ink, never a brand color. */
export function Action({
  className,
  variant = "primary",
  size = "md",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "quiet" | "ghost";
  size?: "sm" | "md";
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-[10px] font-medium",
        "transition-all duration-150 ease-[var(--ease-out-expo)]",
        "active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40",
        size === "sm" ? "h-8 px-3 text-[0.8125rem]" : "h-10 px-4 text-[0.875rem]",
        variant === "primary" &&
          "bg-[var(--ink-1)] text-[var(--surface-0)] hover:opacity-90",
        variant === "quiet" &&
          "bg-[var(--surface-2)] text-[var(--ink-1)] hover:bg-[var(--surface-3)]",
        variant === "ghost" && "text-[var(--ink-2)] hover:bg-[var(--surface-2)]",
        className
      )}
      {...props}
    />
  );
}

/** Big number + label. Used instead of stat "cards" — no box, just type. */
export function Metric({ value, label, hint }: { value: React.ReactNode; label: string; hint?: string }) {
  return (
    <div>
      <div className="text-[1.75rem] leading-none font-medium tracking-[-0.03em] text-[var(--ink-1)]">
        {value}
      </div>
      <div className="mt-2 t-micro">{label}</div>
      {hint && <div className="mt-1 text-[0.75rem] text-[var(--ink-3)]">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-10 text-center text-[0.875rem] text-[var(--ink-3)]">{children}</p>;
}
