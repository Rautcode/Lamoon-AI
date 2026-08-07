"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// Mirrors the ?error=<code> values the API's /auth/oauth/{provider}/callback
// redirects with (apps/api/app/modules/auth/routes.py::oauth_callback).
const ERROR_MESSAGES: Record<string, string> = {
  invalid_state: "Your sign-in link expired or was tampered with. Please try again.",
  exchange_failed: "Couldn't complete sign-in with the provider. Please try again.",
  no_account: "No account found for that email in this company. Ask an admin to add you.",
};

export default function OAuthCallbackPage() {
  const router = useRouter();
  const setTokens = useAuthStore((s) => s.setTokens);
  const setProfile = useAuthStore((s) => s.setProfile);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // One-time parse of the redirect params this OAuth-callback page exists
    // to consume — never re-runs, never mirrors ongoing external state, so
    // the "cascading renders" the rule guards against don't apply here.
    const errorCode = new URLSearchParams(window.location.search).get("error");
    if (errorCode) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setError(ERROR_MESSAGES[errorCode] ?? "Sign-in failed. Please try again.");
      return;
    }

    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const access = fragment.get("access_token");
    const refresh = fragment.get("refresh_token");
    if (!access || !refresh) {
      setError("Sign-in didn't complete. Please try again.");
      return;
    }

    setTokens(access, refresh);
    // Tokens shouldn't sit in the address bar / browser history any longer
    // than this one tick.
    window.history.replaceState(null, "", window.location.pathname);

    api
      .me()
      .then((me) => {
        setProfile(me.role, me.permissions, me.company_id);
        router.replace("/home");
      })
      .catch(() => setError("Signed in, but couldn't load your profile. Please try again."));
    // Runs once on mount — the redirect params are only meaningful the first time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-screen flex-1 items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Lamoon HR</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {error ? (
            <>
              <p className="text-sm text-destructive">{error}</p>
              <Button className="w-full" onClick={() => router.push("/login")}>
                Back to sign in
              </Button>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Signing you in…</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
