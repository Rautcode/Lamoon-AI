"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { landingFor, useAuthStore } from "@/lib/auth-store";
import { Action } from "@/components/lamoon/primitives";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginPage() {
  const router = useRouter();
  const accessToken = useAuthStore((s) => s.accessToken);
  const setTokens = useAuthStore((s) => s.setTokens);
  const setProfile = useAuthStore((s) => s.setProfile);

  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const { data: providers } = useQuery({
    queryKey: ["oauth-providers"],
    queryFn: api.oauthProviders,
    staleTime: Infinity,
  });

  useEffect(() => {
    // Already signed in — the workspace layout re-checks and routes by role.
    if (accessToken) router.replace("/");
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
      router.push(landingFor(me.permissions));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  function startOAuth(provider: "google" | "microsoft") {
    setNotice(null);
    // The OAuth callback only gets an email back, never a tenant — the company
    // has to be known before the redirect chain starts.
    if (!company.trim()) return setNotice("Enter your company workspace first.");
    if (!providers?.[provider])
      return setNotice(
        `${provider === "google" ? "Google" : "Microsoft"} sign-in isn't configured here.`
      );
    window.location.href = api.oauthStartUrl(provider, company);
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-5 py-12">
      <div className="rise w-full max-w-[380px]">
        <div className="mb-10 flex flex-col items-start gap-4">
          <span className="lumo-ring size-7 rounded-[9px]" />
          <div>
            <h1 className="t-title">Lamoon</h1>
            <p className="mt-1 t-meta">Sign in to your workspace.</p>
          </div>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="company" className="t-micro">
              Workspace
            </Label>
            <Input
              id="company"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="acme"
              autoComplete="organization"
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="email" className="t-micro">
              Email
            </Label>
            <Input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password" className="t-micro">
              Password
            </Label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>

          {error && <p className="text-[0.8125rem] text-[var(--critical)]">{error}</p>}

          <Action type="submit" className="w-full" disabled={loading}>
            {loading ? "Signing in…" : "Continue"}
          </Action>
        </form>

        <div className="my-6 flex items-center gap-3">
          <span className="h-px flex-1 bg-[var(--hairline)]" />
          <span className="t-micro">or</span>
          <span className="h-px flex-1 bg-[var(--hairline)]" />
        </div>

        <div className="space-y-2">
          <Action variant="quiet" className="w-full" onClick={() => startOAuth("google")}>
            Continue with Google
          </Action>
          <Action variant="quiet" className="w-full" onClick={() => startOAuth("microsoft")}>
            Continue with Microsoft
          </Action>
        </div>

        {notice && <p className="mt-3 text-[0.8125rem] text-[var(--ink-3)]">{notice}</p>}
      </div>
    </div>
  );
}
