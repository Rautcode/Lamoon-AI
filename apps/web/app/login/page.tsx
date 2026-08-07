"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function LoginPage() {
  const router = useRouter();
  const accessToken = useAuthStore((s) => s.accessToken);
  const setTokens = useAuthStore((s) => s.setTokens);
  const setProfile = useAuthStore((s) => s.setProfile);

  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [oauthNotice, setOauthNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Static per deployment (env-driven) — no point refetching mid-session.
  const { data: providers } = useQuery({
    queryKey: ["oauth-providers"],
    queryFn: api.oauthProviders,
    staleTime: Infinity,
  });

  // Already signed in (e.g. reloaded /login directly) — skip the form.
  useEffect(() => {
    if (accessToken) router.replace("/employees");
  }, [accessToken, router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const tokens = await api.login(company, email, password);
      setTokens(tokens.access_token, tokens.refresh_token);
      const me = await api.me();
      setProfile(me.role, me.permissions, me.company_id);
      router.push("/employees");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  function startOAuth(provider: "google" | "microsoft") {
    setOauthNotice(null);
    // OAuth's callback only gets an email back from the provider, never a
    // tenant — the company has to come from here, before the redirect chain
    // to Google/Microsoft even starts (see core/auth/oauth.py::new_state).
    if (!company.trim()) {
      setOauthNotice("Enter your company subdomain above first.");
      return;
    }
    if (!providers?.[provider]) {
      const label = provider === "google" ? "Google" : "Microsoft";
      setOauthNotice(`${label} sign-in isn't configured for this deployment.`);
      return;
    }
    window.location.href = api.oauthStartUrl(provider, company);
  }

  return (
    <div className="flex min-h-screen flex-1 items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Lamoon HR</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="company">Company subdomain</Label>
              <Input
                id="company"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="acme"
                autoComplete="organization"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-card px-2 text-muted-foreground">Or</span>
            </div>
          </div>

          <div className="space-y-2">
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => startOAuth("google")}
            >
              Continue with Google
            </Button>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => startOAuth("microsoft")}
            >
              Continue with Microsoft
            </Button>
          </div>
          {oauthNotice && <p className="text-sm text-muted-foreground">{oauthNotice}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
