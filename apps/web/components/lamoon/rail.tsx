"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useSyncExternalStore } from "react";
import { LogOut, Moon, Sun } from "lucide-react";
import { api } from "@/lib/api";
import { landingFor, useAuthStore } from "@/lib/auth-store";
import { navFor } from "@/lib/nav";
import { Avatar, Kbd } from "@/components/lamoon/primitives";

/* Adaptive navigation.

   Collapsed to a 56px rail by default and expands on hover — because the
   command palette (⌘K) is the intended way to move around, and a permanent
   200px sidebar of links you rarely click is exactly the ERP habit this
   product is trying not to inherit. On mobile it becomes a bottom bar, where
   thumbs are. */


/* The theme lives on <html>, set pre-paint in app/layout.tsx — i.e. it's
   external mutable state, not React state. useSyncExternalStore is the right
   way to read it: no effect, no double render, and it stays correct if
   anything else ever flips the class. */
const themeStore = {
  subscribe(onChange: () => void) {
    const observer = new MutationObserver(onChange);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  },
  get: () => document.documentElement.classList.contains("dark"),
  getServer: () => false,
};

/** Theme toggle. Flips the class and remembers the explicit choice. */
function ThemeToggle() {
  const dark = useSyncExternalStore(themeStore.subscribe, themeStore.get, themeStore.getServer);

  return (
    <button
      onClick={() => {
        const next = !document.documentElement.classList.contains("dark");
        document.documentElement.classList.toggle("dark", next);
        localStorage.setItem("lamoon_theme", next ? "dark" : "light");
      }}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="flex h-10 w-[calc(100%-16px)] items-center gap-3 rounded-[10px] px-[11px]
                 text-[var(--ink-3)] transition-colors hover:bg-[var(--surface-2)]
                 hover:text-[var(--ink-1)]"
    >
      {dark ? (
        <Sun size={18} strokeWidth={1.75} className="shrink-0" />
      ) : (
        <Moon size={18} strokeWidth={1.75} className="shrink-0" />
      )}
      <span className="overflow-hidden text-[0.875rem] whitespace-nowrap opacity-0 transition-opacity duration-200 group-hover:opacity-100">
        {dark ? "Light" : "Dark"}
      </span>
    </button>
  );
}

export function Rail() {
  const pathname = usePathname();
  const router = useRouter();
  const role = useAuthStore((s) => s.role);
  const permissions = useAuthStore((s) => s.permissions);
  const items = navFor(permissions);
  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");

  return (
    <>
      {/* ---------- Desktop rail ---------- */}
      <nav
        className="group fixed top-0 left-0 z-30 hidden h-full w-[56px] flex-col items-center
                   justify-between py-5 transition-[width] duration-300
                   ease-[var(--ease-out-expo)] hover:w-[212px] hover:bg-[var(--surface-1)]
                   hover:shadow-[8px_0_32px_-16px_oklch(0_0_0/16%)] sm:flex"
        aria-label="Main"
      >
        <div className="flex w-full flex-col items-center gap-1">
          <Link
            href={landingFor(permissions)}
            className="mb-4 grid h-9 w-full place-items-center group-hover:justify-items-start group-hover:pl-4"
            aria-label="Lamoon home"
          >
            <span className="lumo-ring size-[18px] rounded-[6px]" />
          </Link>

          {items.map(({ href, label, Icon }) => (
            <Link
              key={href}
              href={href}
              aria-current={isActive(href) ? "page" : undefined}
              className={`flex h-10 w-[calc(100%-16px)] items-center gap-3 rounded-[10px] px-[11px]
                          transition-colors duration-150 ${
                            isActive(href)
                              ? "bg-[var(--surface-2)] text-[var(--ink-1)]"
                              : "text-[var(--ink-3)] hover:bg-[var(--surface-2)] hover:text-[var(--ink-1)]"
                          }`}
            >
              <Icon size={18} strokeWidth={1.75} className="shrink-0" />
              <span
                className="overflow-hidden text-[0.875rem] whitespace-nowrap opacity-0
                           transition-opacity duration-200 group-hover:opacity-100"
              >
                {label}
              </span>
            </Link>
          ))}
        </div>

        <div className="flex w-full flex-col items-center gap-2">
          <div
            className="hidden w-[calc(100%-16px)] px-[11px] pb-1 group-hover:block"
            aria-hidden
          >
            <span className="text-[0.6875rem] text-[var(--ink-4)]">
              <Kbd>⌘K</Kbd> search · <Kbd>⌘J</Kbd> Lumo
            </span>
          </div>
          <ThemeToggle />
          <button
            onClick={() => api.logout().then(() => router.push("/login"))}
            className="flex h-10 w-[calc(100%-16px)] items-center gap-3 rounded-[10px] px-[11px]
                       text-[var(--ink-3)] transition-colors hover:bg-[var(--surface-2)]
                       hover:text-[var(--ink-1)]"
          >
            <LogOut size={18} strokeWidth={1.75} className="shrink-0" />
            <span className="overflow-hidden text-[0.875rem] whitespace-nowrap opacity-0 transition-opacity duration-200 group-hover:opacity-100">
              Sign out
            </span>
          </button>
          <div className="flex h-10 w-[calc(100%-16px)] items-center gap-3 px-[9px]">
            <Avatar name={role ?? "You"} size={26} />
            <span className="overflow-hidden text-[0.8125rem] whitespace-nowrap text-[var(--ink-3)] opacity-0 transition-opacity duration-200 group-hover:opacity-100">
              {role ?? ""}
            </span>
          </div>
        </div>
      </nav>

      {/* ---------- Mobile bottom bar ---------- */}
      <nav
        className="fixed inset-x-0 bottom-0 z-30 flex items-center justify-around
                   bg-[var(--surface-1)] px-2 pt-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]
                   shadow-[0_-1px_0_var(--hairline)] sm:hidden"
        aria-label="Main"
      >
        {items.map(({ href, label, Icon }) => (
          <Link
            key={href}
            href={href}
            aria-current={isActive(href) ? "page" : undefined}
            className={`flex flex-1 flex-col items-center gap-1 rounded-[10px] py-1.5 ${
              isActive(href) ? "text-[var(--ink-1)]" : "text-[var(--ink-4)]"
            }`}
          >
            <Icon size={20} strokeWidth={1.75} />
            <span className="text-[0.625rem]">{label}</span>
          </Link>
        ))}
      </nav>
    </>
  );
}
