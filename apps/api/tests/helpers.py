"""Test helpers that need a real session with the tenant armed.

RLS is FORCEd, so a bare SessionLocal() sees nothing. Anything poking at rows
directly has to set `app.company_id` first — forgetting it produces an empty
result and a test that passes while proving nothing.
"""
import contextlib

from sqlalchemy import text

from app.core.db import SessionLocal


@contextlib.contextmanager
def company_session(client, subdomain: str):
    """A session scoped to one tenant, plus its company id."""
    db = SessionLocal()
    try:
        company_id = db.scalar(
            text("SELECT id FROM companies WHERE subdomain = :s"), {"s": subdomain}
        )
        db.execute(
            text("SELECT set_config('app.company_id', :c, true)"),
            {"c": str(company_id)},
        )
        yield db, company_id
    finally:
        db.close()
