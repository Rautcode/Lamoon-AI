"""Seat-limit enforcement on POST /hr/employees, backed by companies.seat_limit
via the entitlement engine (core/billing/entitlements.py). Uses its own fresh
company (not the shared `acme` used elsewhere) so the count starts at exactly
zero and this test can't be polluted by — or pollute — anything else.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.db import engine


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


def _fresh_company(client) -> tuple[dict, int]:
    sub = f"seat-{uuid.uuid4().hex[:8]}"
    body = {
        "company_name": "Seat Co", "subdomain": sub,
        "email": f"admin@{sub}.test", "password": "pw123456",
    }
    boot = client.post("/api/v1/auth/bootstrap", json=body).json()
    login = client.post(
        "/api/v1/auth/login",
        json={"company": sub, "email": body["email"], "password": body["password"]},
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    return headers, boot["seat_limit"]


def _create(client, headers, name="Employee"):
    return client.post("/api/v1/hr/employees", json={"full_name": name}, headers=headers)


def test_seat_limit_enforced_and_freed_by_exit_or_delete(client):
    headers, seat_limit = _fresh_company(client)
    assert seat_limit > 0

    ids = []
    for i in range(seat_limit):
        r = _create(client, headers, f"Employee {i}")
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])

    # One over the limit -> 402, not a 500 or a silent overshoot.
    r = _create(client, headers, "One Too Many")
    assert r.status_code == 402
    assert "seat limit" in r.json()["detail"].lower()

    # Exiting an employee frees a seat.
    r = client.patch(
        f"/api/v1/hr/employees/{ids[0]}",
        json={"full_name": "Employee 0", "status": "exited"},
        headers=headers,
    )
    assert r.status_code == 200
    r = _create(client, headers, "Replacement Hire")
    assert r.status_code == 200, r.text
    ids.append(r.json()["id"])

    # Full again -> 402 once more.
    r = _create(client, headers, "One Too Many Again")
    assert r.status_code == 402

    # Soft-deleting also frees a seat.
    r = client.delete(f"/api/v1/hr/employees/{ids[1]}", headers=headers)
    assert r.status_code == 204
    r = _create(client, headers, "Another Replacement")
    assert r.status_code == 200, r.text
