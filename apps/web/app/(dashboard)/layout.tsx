"use client";
import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { Button } from "@/components/ui/button";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const accessToken = useAuthStore((s) => s.accessToken);
  const role = useAuthStore((s) => s.role);
  const setProfile = useAuthStore((s) => s.setProfile);
  const clear = useAuthStore((s) => s.clear);

  useEffect(() => {
    if (!accessToken) {
      router.replace("/login");
      return;
    }
    // A reload keeps the persisted tokens but not the in-memory role/
    // permissions — re-fetch so RBAC-gated UI (e.g. the "Add employee" form)
    // reflects the account's actual current permissions, not a guess.
    if (!role) {
      api
        .me()
        .then((me) => setProfile(me.role, me.permissions, me.company_id))
        .catch(() => {
          clear();
          router.replace("/login");
        });
    }
  }, [accessToken, role, router, setProfile, clear]);

  if (!accessToken) return null;

  const linkClass = (href: string) =>
    pathname === href ? "font-medium" : "text-muted-foreground hover:text-foreground";

  return (
    <div className="flex min-h-screen flex-1 flex-col">
      <header className="border-b">
        <div className="mx-auto flex max-w-5xl items-center justify-between p-4">
          <div className="flex items-center gap-6">
            <span className="font-semibold">Lamoon HR</span>
            <nav className="flex gap-4 text-sm">
              <Link href="/employees" className={linkClass("/employees")}>
                Employees
              </Link>
              <Link href="/ats" className={linkClass("/ats")}>
                ATS
              </Link>
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm">
            {role && <span className="text-muted-foreground">{role}</span>}
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                // Revokes the tokens server-side (immediate, not just "the
                // client forgot them") before routing away.
                api.logout().then(() => router.push("/login"));
              }}
            >
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 p-6">{children}</main>
    </div>
  );
}
