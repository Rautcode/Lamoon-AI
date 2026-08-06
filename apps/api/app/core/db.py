"""DB session + the RLS tenant guard.

Every request/transaction runs `SET LOCAL app.company_id = <tenant>` so Postgres
RLS policies physically scope rows to the tenant (ADR-0002). Even a query that
forgets `WHERE company_id=` cannot cross tenants.

ponytail: sync SQLAlchemy — simplest correct choice for a skeleton; FastAPI runs
`def` deps in a threadpool. Switch to async only if DB I/O contention shows up.
"""
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.tenant import resolve_tenant

engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def open_session() -> Iterator[Session]:
    """A session with no tenant pre-set — for the auth bootstrap/login paths that
    must run *before* a tenant is known. They set the RLS GUC themselves once the
    company is resolved (see auth.service)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db(tenant: str = Depends(resolve_tenant)) -> Iterator[Session]:
    """FastAPI dependency: a tenant-scoped session. `tenant` resolves first, so
    every statement runs under the RLS GUC — a query that forgets company_id
    still cannot cross tenants (ADR-0002)."""
    db = SessionLocal()
    try:
        # set_config(..., is_local=true) is the parameterized form of SET LOCAL:
        # transaction-scoped (no pooled-connection leak) AND safely bound, so the
        # tenant value can't be a SQL-injection vector.
        db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": tenant})
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
