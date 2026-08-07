"""Leave management: types (config), request workflow, balance derived from
approved requests only (never a stored counter). Each test gets its OWN fresh
company + employee (not the shared `acme`), so balance math starts at exactly
zero and this suite can't be polluted by, or pollute, anything else.
"""
import uuid
from datetime import date, timedelta

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


@pytest.fixture
def company(client):
    sub = f"leave-{uuid.uuid4().hex[:8]}"
    body = {
        "company_name": "Leave Co", "subdomain": sub,
        "email": f"admin@{sub}.test", "password": "pw123456",
    }
    client.post("/api/v1/auth/bootstrap", json=body)
    login = client.post(
        "/api/v1/auth/login",
        json={"company": sub, "email": body["email"], "password": "pw123456"},
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    emp = client.post("/api/v1/hr/employees", json={"full_name": "Asha Rao"}, headers=headers).json()
    return {"sub": sub, "headers": headers, "employee_id": emp["id"]}


def _make_type(client, headers, name: str, quota: int) -> dict:
    r = client.post("/api/v1/leave/types", json={"name": name, "annual_quota": quota}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _make_request(client, headers, employee_id, leave_type_id, start: date, days: int) -> dict:
    end = start + timedelta(days=days - 1)
    r = client.post(
        "/api/v1/leave/requests",
        json={
            "employee_id": employee_id, "leave_type_id": leave_type_id,
            "start_date": str(start), "end_date": str(end),
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_leave_type_crud(client, company):
    headers = company["headers"]
    lt = _make_type(client, headers, "Annual", 18)
    assert lt["annual_quota"] == 18

    r = client.get("/api/v1/leave/types", headers=headers)
    assert any(t["name"] == "Annual" for t in r.json())


def test_leave_request_days_computed_and_pending_by_default(client, company):
    headers, emp_id = company["headers"], company["employee_id"]
    lt = _make_type(client, headers, "Sick", 10)
    req = _make_request(client, headers, emp_id, lt["id"], date.today(), 3)
    assert req["days"] == 3
    assert req["status"] == "pending"


def test_pending_request_does_not_affect_balance(client, company):
    headers, emp_id = company["headers"], company["employee_id"]
    lt = _make_type(client, headers, "Sick", 10)
    _make_request(client, headers, emp_id, lt["id"], date.today(), 3)

    bal = client.get(f"/api/v1/leave/balances/{emp_id}", headers=headers).json()
    sick = next(b for b in bal if b["leave_type_name"] == "Sick")
    assert sick["used"] == 0
    assert sick["remaining"] == 10


def test_approve_updates_balance_and_is_single_use(client, company):
    headers, emp_id = company["headers"], company["employee_id"]
    lt = _make_type(client, headers, "Sick", 10)
    req = _make_request(client, headers, emp_id, lt["id"], date.today(), 3)

    r = client.post(f"/api/v1/leave/requests/{req['id']}/approve", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    bal = client.get(f"/api/v1/leave/balances/{emp_id}", headers=headers).json()
    sick = next(b for b in bal if b["leave_type_name"] == "Sick")
    assert sick["used"] == 3
    assert sick["remaining"] == 7

    # a decided request can't be decided again
    r = client.post(f"/api/v1/leave/requests/{req['id']}/approve", headers=headers)
    assert r.status_code == 409


def test_reject_does_not_affect_balance(client, company):
    headers, emp_id = company["headers"], company["employee_id"]
    lt = _make_type(client, headers, "Casual", 7)
    req = _make_request(client, headers, emp_id, lt["id"], date.today(), 1)

    r = client.post(f"/api/v1/leave/requests/{req['id']}/reject", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    bal = client.get(f"/api/v1/leave/balances/{emp_id}", headers=headers).json()
    casual = next(b for b in bal if b["leave_type_name"] == "Casual")
    assert casual["used"] == 0
    assert casual["remaining"] == 7


def test_approval_exceeding_balance_is_rejected(client, company):
    headers, emp_id = company["headers"], company["employee_id"]
    lt = _make_type(client, headers, "Comp Off", 2)

    req1 = _make_request(client, headers, emp_id, lt["id"], date.today(), 2)  # uses the full quota
    req2 = _make_request(client, headers, emp_id, lt["id"], date.today() + timedelta(days=30), 2)

    assert client.post(f"/api/v1/leave/requests/{req1['id']}/approve", headers=headers).status_code == 200
    r = client.post(f"/api/v1/leave/requests/{req2['id']}/approve", headers=headers)
    assert r.status_code == 409
    assert "exceed" in r.json()["detail"].lower()


def test_end_before_start_is_rejected(client, company):
    headers, emp_id = company["headers"], company["employee_id"]
    lt = _make_type(client, headers, "Marriage", 3)
    start = date.today()
    r = client.post(
        "/api/v1/leave/requests",
        json={
            "employee_id": emp_id, "leave_type_id": lt["id"],
            "start_date": str(start), "end_date": str(start - timedelta(days=1)),
        },
        headers=headers,
    )
    assert r.status_code == 422


def test_manager_can_approve_but_not_create(client, company):
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import hash_password
    from app.modules.auth.models import User

    headers, emp_id = company["headers"], company["employee_id"]
    s = get_settings()
    cid = pyjwt.decode(
        headers["Authorization"].split()[1], s.jwt_secret, algorithms=[s.jwt_alg]
    )["cid"]

    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": cid})
    db.add(
        User(
            company_id=uuid.UUID(cid), email="mgr@leave.test", role="manager",
            password_hash=hash_password("pw123456"),
        )
    )
    db.commit()
    db.close()

    mgr_login = client.post(
        "/api/v1/auth/login",
        json={"company": company["sub"], "email": "mgr@leave.test", "password": "pw123456"},
    ).json()
    mgr_headers = {"Authorization": f"Bearer {mgr_login['access_token']}"}

    lt = _make_type(client, headers, "Bereavement", 5)

    r = client.post(
        "/api/v1/leave/requests",
        json={
            "employee_id": emp_id, "leave_type_id": lt["id"],
            "start_date": str(date.today()), "end_date": str(date.today()),
        },
        headers=mgr_headers,
    )
    assert r.status_code == 403  # manager has no leave.write

    req = _make_request(client, headers, emp_id, lt["id"], date.today(), 1)  # HR files it
    r = client.post(f"/api/v1/leave/requests/{req['id']}/approve", headers=mgr_headers)
    assert r.status_code == 200  # manager DOES have leave.approve
