# Lamoon HR — Web

Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + React Query +
Zustand, per [`ARCHITECTURE.md`](../../ARCHITECTURE.md). Talks to `apps/api`
over plain REST — no server-side proxy/BFF yet (see the auth-store ponytail
note on why that matters).

**Design language and interaction model: [`DESIGN.md`](../../DESIGN.md).**
Read that first — it explains the one rule the whole UI obeys.

## Status
Real auth (login → JWT → protected shell, sign-out revokes tokens
server-side, Google/Microsoft sign-in), an AI Workspace home, a hiring
pipeline board with AI scores and a candidate inspector, the people directory
and per-person profile, leave (decisions-first), and the org tree. Plus the
three shell surfaces: **Lumo** (`⌘J`), the **command palette** (`⌘K`), and a
hover-expanding rail. Light + dark themes.

Interview scheduling and Employee Self-Service (employees filing their own
leave) still have no UI — see DESIGN.md §11 for everything deliberately not
built and why.

## Run it
```bash
cp .env.local.example .env.local   # points at the API; defaults to localhost:8000
npm install
npm run dev
# http://localhost:3000 — needs apps/api running (docker compose up -d db redis api)
```

## Layout
```
app/login/             workspace + password, or Google/Microsoft
app/oauth/callback/    parses tokens from the URL fragment (or ?error=)
app/(workspace)/       authenticated shell: rail + palette + Lumo
  home/                AI Workspace — greeting, ask, "needs you"
  hiring/              pipeline board + candidate inspector
  people/ , people/[id]  directory + one intelligent page per person
  time/                leave, decisions first
  attendance/          presence now + 14-day hours heatmap
  org/                 department tree
components/lamoon/     the design system (primitives, rail, palette, Lumo)
lib/lumo-brain.ts      thin client for /assistant/ask — routing, tools and the
                        model all live server-side now (DESIGN.md §2)
lib/nav.ts             permission-derived nav: feeds the rail AND the guard
lib/api.ts             typed fetch client — attaches JWT, retries once on 401
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
