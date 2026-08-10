"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { landingFor, useAuthStore } from "@/lib/auth-store";
import { canOpen } from "@/lib/nav";
import { Rail } from "@/components/lamoon/rail";
import { CommandPalette } from "@/components/lamoon/command-palette";
import { Lumo } from "@/components/lamoon/lumo";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const accessToken = useAuthStore((s) => s.accessToken);
  const role = useAuthStore((s) => s.role);
  const permissions = useAuthStore((s) => s.permissions);
  const setProfile = useAuthStore((s) => s.setProfile);
  const clear = useAuthStore((s) => s.clear);

  // Send people to a page their role can actually use. Convenience, not
  // security — the API enforces permissions regardless of what's routed here.
  useEffect(() => {
    if (role && !canOpen(permissions, pathname)) {
      router.replace(landingFor(permissions));
    }
  }, [role, permissions, pathname, router]);

  useEffect(() => {
    if (!accessToken) {
      router.replace("/login");
      return;
    }
    // A reload keeps persisted tokens but not in-memory role/permissions —
    // re-fetch so RBAC-gated UI reflects real permissions, not a guess.
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

  return (
    <div className="min-h-screen">
      <Rail />
      <CommandPalette />
      <Lumo />
      {/* Rail is 56px on desktop; the bottom bar owns 68px on mobile. */}
      <main className="px-5 pt-6 pb-24 sm:pt-10 sm:pb-16 sm:pl-[80px] sm:pr-8">
        <div className="mx-auto w-full max-w-[1120px]">{children}</div>
      </main>
    </div>
  );
}
