# Lamoon HR — Web

Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + React Query +
Zustand, per [`ARCHITECTURE.md`](../../ARCHITECTURE.md). Talks to `apps/api`
over plain REST — no server-side proxy/BFF yet (see the auth-store ponytail
note on why that matters).

## Status
Real auth (login → JWT → protected shell, sign-out actually revokes tokens
server-side, Google/Microsoft "Continue with..." buttons), the employee
directory (list + create, RBAC- and seat-limit-aware), departments (with a
department filter on Employees), leave management (configurable leave types,
request → approve/reject workflow, balance derived from approved requests),
and a read-only ATS pipeline view (jobs + applications with tier badges).
Interview scheduling and Employee Self-Service (employees filing their own
leave) still have no UI.

## Run it
```bash
cp .env.local.example .env.local   # points at the API; defaults to localhost:8000
npm install
npm run dev
# http://localhost:3000 — needs apps/api running (docker compose up -d db redis api)
```

## Layout
```
app/login/            public login page + OAuth buttons
app/oauth/callback/   lands after Google/Microsoft — parses tokens from the
                       URL fragment (success) or ?error= (failure)
app/(dashboard)/      protected shell (nav + sign-out) + employees, departments,
                       leave, ats pages
lib/api.ts            typed fetch client — attaches JWT, retries once on 401
                       via /auth/refresh, then signs out if that also fails
lib/auth-store.ts      zustand: tokens (localStorage) + role/permissions (memory)
lib/types.ts           hand-written mirrors of the API's response_models
```

## OAuth needs real credentials to actually work
The buttons are real and wired end-to-end, but there are no Google/Microsoft
app credentials in this environment (or, likely, yours). With
`GOOGLE_CLIENT_ID`/`MICROSOFT_CLIENT_ID` unset, `/auth/oauth/providers`
reports `false` and clicking a button shows "isn't configured" instead of a
dead 503. Set real credentials in the API's env to light one up — nothing on
the frontend needs to change.

## Known limitations (by design, not oversight)
- **Tokens in localStorage** — XSS-exposed. Fine for an internal V1 tool; a
  real BFF (Next.js server actions/route handlers holding an httpOnly cookie)
  is the fix before this is exposed more broadly.
- **Client-side auth guard only** — the dashboard layout redirects if there's
  no token, checked in the browser after the page loads. No SSR-level auth.
- **No apply/resume-upload UI** — that flow is the public webhook
  (`POST /api/v1/ats/apply`) and the candidate-facing booking link; neither
  needs an internal-dashboard UI in V1.
