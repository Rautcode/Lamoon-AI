"""Refresh-token revocation: /auth/logout kills both tokens immediately,
refresh rotation kills the old refresh token on every successful /auth/refresh.
Needs Postgres AND Redis (the deny-list store) — skips cleanly without either.
"""
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


def _redis_up() -> bool:
    try:
        from app.core.auth.revocation import _redis

        return bool(_redis().ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_db_up() and _redis_up()), reason="Postgres or Redis not reachable"
)


def _login(client) -> dict:
    client.post("/api/v1/auth/bootstrap", json=COMPANY)
    return client.post("/api/v1/auth/login", json=CREDS).json()


def test_logout_revokes_access_and_refresh_immediately(client):
    tokens = _login(client)
    auth_header = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Works before logout.
    assert client.get("/api/v1/auth/me", headers=auth_header).status_code == 200

    r = client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=auth_header
    )
    assert r.status_code == 204

    # The SAME access token is now dead — not just "will expire eventually".
    assert client.get("/api/v1/auth/me", headers=auth_header).status_code == 401

    # The refresh token is dead too — can't mint a new session from it either.
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


def test_logout_needs_a_valid_access_token(client):
    r = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": "irrelevant"},
        headers={"Authorization": "Bearer garbage"},
    )
    assert r.status_code == 401


def test_refresh_rotation_old_token_is_single_use(client):
    tokens = _login(client)
    old_refresh = tokens["refresh_token"]

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200, r.text
    new_tokens = r.json()

    # Replaying the OLD (now-rotated) refresh token is rejected...
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401

    # ...but the token minted BY that refresh still works.
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]})
    assert r.status_code == 200, r.text
