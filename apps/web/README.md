# Lamoon HR — Web

Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + React Query +
Zustand, per [`ARCHITECTURE.md`](../../ARCHITECTURE.md). Talks to `apps/api`
over plain REST — no server-side proxy/BFF yet (see the auth-store ponytail
note on why that matters).

## Status
Real auth (login → JWT → protected shell) and two data views: the employee
directory (list + create, RBAC- and seat-limit-aware) and a read-only ATS
pipeline view (jobs + applications with tier badges). Everything else in the
API (interview scheduling, OAuth, departments, ...) has no UI yet.

## Run it
```bash
cp .env.local.example .env.local   # points at the API; defaults to localhost:8000
npm install
npm run dev
# http://localhost:3000 — needs apps/api running (docker compose up -d db redis api)
```

## Layout
```
app/login/            public login page
app/(dashboard)/      protected shell (nav + sign-out) + employees, ats pages
lib/api.ts            typed fetch client — attaches JWT, retries once on 401
                       via /auth/refresh, then signs out if that also fails
lib/auth-store.ts      zustand: tokens (localStorage) + role/permissions (memory)
lib/types.ts           hand-written mirrors of the API's response_models
```

## Known limitations (by design, not oversight)
- **Tokens in localStorage** — XSS-exposed. Fine for an internal V1 tool; a
  real BFF (Next.js server actions/route handlers holding an httpOnly cookie)
  is the fix before this is exposed more broadly.
- **Client-side auth guard only** — the dashboard layout redirects if there's
  no token, checked in the browser after the page loads. No SSR-level auth.
- **No apply/resume-upload UI** — that flow is the public webhook
  (`POST /api/v1/ats/apply`) and the candidate-facing booking link; neither
  needs an internal-dashboard UI in V1.
