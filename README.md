# Lamoon HR

AI-first HRMS + ATS for Indian SMEs. Modular monolith: FastAPI + PostgreSQL +
Redis/Celery, with a Next.js frontend. See [`ARCHITECTURE.md`](ARCHITECTURE.md),
[`ARCHITECTURE-2-PLATFORM.md`](ARCHITECTURE-2-PLATFORM.md), and
[`docs/adr/`](docs/adr/README.md) for the frozen design.

## Status
Skeleton only — all cross-cutting **seams stubbed**, no feature code yet.
Boots, serves `/api/v1/health`, and passes tests. V1 ATS flow is next.

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

## Frontend
`apps/web` is a placeholder — scaffold with `create-next-app` (see
`apps/web/README.md`) when UI work starts.

## Layout
```
apps/api/app/core/      # seams: auth, ai, storage, notify, search, billing, events, flags
apps/api/app/modules/   # feature modules (system live; rest stubbed)
apps/api/app/workers/   # celery (high/normal/background queues)
docs/adr/               # frozen decisions
frappe_docker/          # gitignored — ERPNext, payroll-rules reference only
```
