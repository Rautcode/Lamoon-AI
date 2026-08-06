"""Workflow 2 — Daily Auto Reject: Tier C/D applications past the 10-day grace
window get rejected with an email; still-fresh ones are left alone.

Fixture data is flushed (visible in-transaction) but never committed, and the
whole test rolls back at the end — so nothing leaks into the next run and no
GUC re-application dance is needed (SET LOCAL stays valid for the transaction's
whole life as long as we never commit).
"""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.core.notify.base import outbox
from app.modules.ats.models import Application, Candidate
from app.modules.ats.tasks import auto_reject_stale_for_company


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


class _Notifier:
    async def send(self, *, to, template, ctx, channel="email"):
        outbox.append({"to": to, "template": template, "ctx": ctx})


def test_stale_pending_reject_gets_rejected(client, headers):
    import jwt as pyjwt

    from app.core.config import get_settings

    s = get_settings()
    token = headers["Authorization"].split()[1]
    cid = pyjwt.decode(token, s.jwt_secret, algorithms=[s.jwt_alg])["cid"]

    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": cid})
    try:
        stale_cand = Candidate(
            company_id=uuid.UUID(cid), email="stale@example.com", resume_blob_key="x",
            resume_sha256=uuid.uuid4().hex.ljust(64, "0"), extracted_text="x",
        )
        fresh_cand = Candidate(
            company_id=uuid.UUID(cid), email="fresh@example.com", resume_blob_key="y",
            resume_sha256=uuid.uuid4().hex.ljust(64, "0"), extracted_text="y",
        )
        db.add_all([stale_cand, fresh_cand])
        db.flush()

        stale_app = Application(
            company_id=uuid.UUID(cid), candidate_id=stale_cand.id, status="pending_reject",
            tier="D", screened_at=datetime.now(UTC) - timedelta(days=11),
        )
        fresh_app = Application(
            company_id=uuid.UUID(cid), candidate_id=fresh_cand.id, status="pending_reject",
            tier="C", screened_at=datetime.now(UTC) - timedelta(days=3),
        )
        db.add_all([stale_app, fresh_app])
        db.flush()

        outbox.clear()
        rejected_count = asyncio.run(auto_reject_stale_for_company(db, uuid.UUID(cid), _Notifier()))
        db.flush()

        # Assert on OUR two rows specifically — not a global count, since a
        # shared dev DB may carry unrelated pending_reject rows from other tests.
        assert rejected_count >= 1
        assert stale_app.status == "rejected"
        assert stale_app.rejected_at is not None
        assert fresh_app.status == "pending_reject"  # untouched — still in grace window
        assert any(m["to"] == "stale@example.com" for m in outbox)
        assert not any(m["to"] == "fresh@example.com" for m in outbox)
    finally:
        db.rollback()  # never persist test data
        db.close()
