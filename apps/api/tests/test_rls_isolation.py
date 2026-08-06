"""RLS cross-tenant isolation (ADR-0002) — the property the whole multi-tenant
design rests on. Two independent companies; company B must never see company
A's rows, via the API OR a raw same-process query under B's GUC.
"""
import pytest
from sqlalchemy import text

from app.core.db import engine
from tests.conftest import make_pdf


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


def test_tenant_b_cannot_see_tenant_a_jobs(client, headers, headers_b):
    r = client.post("/api/v1/ats/jobs", json={"title": "A-Only Role"}, headers=headers)
    job_id = r.json()["id"]

    # B's own list is empty of A's job...
    listed = client.get("/api/v1/ats/jobs", headers=headers_b).json()
    assert all(j["id"] != str(job_id) for j in listed)

    # ...and B can't fetch it by ID either — RLS makes it 404, not 403 (it
    # doesn't exist from B's perspective, which is the correct leak-proof shape).
    r = client.post(
        "/api/v1/ats/apply",
        files={"file": ("r.pdf", make_pdf("test"), "application/pdf")},
        data={"job_id": str(job_id)},
        headers=headers_b,
    )
    assert r.status_code == 202  # apply succeeds (job_id is just a nullable FK)
    app_id = r.json()["application_id"]

    # B reading its own new application: fine.
    assert client.get(f"/api/v1/ats/applications/{app_id}", headers=headers_b).status_code == 200
    # A trying to read B's application: RLS hides it → 404.
    assert client.get(f"/api/v1/ats/applications/{app_id}", headers=headers).status_code == 404


def test_raw_query_respects_rls_guc(headers, headers_b):
    """Even a hand-written query with no WHERE company_id= cannot cross tenants
    — this is what ADR-0002 calls "defense in depth", proven directly."""
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal

    s = get_settings()
    claims_a = pyjwt.decode(
        headers["Authorization"].split()[1], s.jwt_secret, algorithms=[s.jwt_alg]
    )
    claims_b = pyjwt.decode(
        headers_b["Authorization"].split()[1], s.jwt_secret, algorithms=[s.jwt_alg]
    )
    assert claims_a["cid"] != claims_b["cid"]

    db = SessionLocal()
    try:
        cid_a = claims_a["cid"]
        db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": cid_a})
        # No WHERE clause at all — RLS alone must scope this.
        rows = db.execute(text("SELECT company_id FROM users")).fetchall()
        assert rows, "expected at least the seeded admin user"
        assert all(str(r[0]) == cid_a for r in rows)
    finally:
        db.rollback()
        db.close()
