# Lamoon HR

AI-first HRMS + ATS for Indian SMEs. Modular monolith: FastAPI + PostgreSQL +
Redis/Celery, with a Next.js frontend. See [`ARCHITECTURE.md`](ARCHITECTURE.md),
[`ARCHITECTURE-2-PLATFORM.md`](ARCHITECTURE-2-PLATFORM.md), and
[`docs/adr/`](docs/adr/README.md) for the frozen design.

## Status
**V1 feature-complete**: multi-tenant auth (JWT + RBAC + OAuth), seat-limit
enforcement, employee directory, and the full AI ATS flow — resume intake →
Gemini screening → ranking → email automation → self-service interview
scheduling with reminders. A real (if minimal) **web UI**: login, employee
directory, ATS pipeline view. All cross-cutting seams (`core/`) are in place;
several are still stubs pending a real 2nd implementation (Drive, SMS/Slack,
Meilisearch, Razorpay, a generic entitlements table beyond seats — see
`ARCHITECTURE-2-PLATFORM.md`). 32 backend tests, `ruff`/`mypy` clean, CI on
every push.

## Run the stack
```bash
docker compose up --build
# API:     http://localhost:8000/api/v1/health
# Swagger: http://localhost:8000/docs
```

## Backend dev (apps/api)
```bash
cd apps/api
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]" || pip install . && pip install pytest ruff mypy
pytest            # tests pass without a DB (health + entitlement logic)
ruff check .      # lint
mypy app          # types
```

## Migrations
```bash
cd apps/api
alembic revision -m "create <table>"    # hand-add RLS policy for tenant tables
alembic upgrade head
```
Local Postgres (via `docker compose up -d db`) auto-creates the non-superuser
`app` role from `db/init/01-app-role.sql` on first start. **Never** point
`DATABASE_URL` at the `lamoon` bootstrap user — it's a superuser, and
superusers implicitly bypass Row-Level Security (ADR-0002).

## CI/CD
[`.github/workflows/ci.yml`](.github/workflows/ci.yml), on every push/PR:
lint (`ruff`) → type check (`mypy`) → spin up real Postgres+Redis service
containers, create the `app` role → run migrations → run the full test suite.
On push to `main`/`master` after tests pass, a second job builds the API
image and publishes it to GHCR (`ghcr.io/<owner>/<repo>`) — no secrets needed
beyond the built-in `GITHUB_TOKEN`. **Deploying that image to a live
server/cloud is a separate, later step** — it needs a real target and
credentials this repo doesn't have yet.

CI only runs once this repo has a GitHub remote and is pushed there.

## Frontend
`apps/web` — Next.js + shadcn/ui + React Query + Zustand. See
[`apps/web/README.md`](apps/web/README.md) to run it; needs the API up
(CORS is origin-gated via `CORS_ORIGINS`, defaulting to `localhost:3000`).

## Layout
```
apps/api/app/core/      # seams: auth, ai, storage, notify, search, billing, events, flags
apps/api/app/modules/   # auth, hr_core, ats, public (interview booking), audit, system
apps/api/app/workers/   # celery (high/normal/background queues) + beat schedules
apps/web/               # Next.js — login, employee directory, ATS pipeline view
db/init/                # non-superuser `app` role — required for RLS to actually work
docs/adr/               # frozen decisions
frappe_docker/          # gitignored — ERPNext, payroll-rules reference only
```
