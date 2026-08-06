"""Real auth: bootstrap → login → JWT → protected access. Needs Postgres."""
import pytest
from sqlalchemy import text

from app.core.db import engine
from tests.conftest import COMPANY, CREDS


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


def _bootstrap(client):
    client.post("/api/v1/auth/bootstrap", json=COMPANY)


def test_login_then_me(client):
    _bootstrap(client)
    r = client.post("/api/v1/auth/login", json=CREDS)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
    assert "*" in me.json()["permissions"]


def test_wrong_password_401(client):
    _bootstrap(client)
    r = client.post("/api/v1/auth/login", json={**CREDS, "password": "wrong"})
    assert r.status_code == 401


def test_protected_route_needs_token(client):
    # ATS now requires a valid JWT — no token → 401 (the dev shim is gone).
    assert client.get("/api/v1/ats/jobs").status_code == 401
    bad = client.get("/api/v1/ats/jobs", headers={"Authorization": "Bearer garbage"})
    assert bad.status_code == 401
