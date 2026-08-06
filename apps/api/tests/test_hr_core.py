"""Employee directory: CRUD + RBAC actually denies a role without the
permission (not just admin's wildcard sailing through everything)."""
import uuid

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.core.security import hash_password
from app.modules.auth.models import User


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


def test_department_and_employee_crud(client, headers):
    r = client.post("/api/v1/hr/departments", json={"name": "Engineering"}, headers=headers)
    assert r.status_code == 200, r.text
    dept_id = r.json()["id"]

    r = client.post(
        "/api/v1/hr/employees",
        json={"full_name": "Priya Shah", "email": "priya@acme.test", "department_id": dept_id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    emp_id = r.json()["id"]
    assert r.json()["status"] == "active"

    # filter by department
    r = client.get(f"/api/v1/hr/employees?department_id={dept_id}", headers=headers)
    assert any(e["id"] == emp_id for e in r.json())

    # update
    r = client.patch(
        f"/api/v1/hr/employees/{emp_id}",
        json={"full_name": "Priya Shah", "status": "probation", "department_id": dept_id},
        headers=headers,
    )
    assert r.json()["status"] == "probation"

    # soft delete → disappears from list and single-get 404s
    r = client.delete(f"/api/v1/hr/employees/{emp_id}", headers=headers)
    assert r.status_code == 204
    assert client.get(f"/api/v1/hr/employees/{emp_id}", headers=headers).status_code == 404
    listed = client.get("/api/v1/hr/employees", headers=headers).json()
    assert all(e["id"] != emp_id for e in listed)


def _login_as(client, cid: str, email: str, role: str) -> dict:
    """Insert a user with a specific role directly (no signup endpoint yet) and
    log them in — the only way to test non-admin RBAC without one."""
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": cid})
    pw_hash = hash_password("pw123456")
    db.add(User(company_id=uuid.UUID(cid), email=email, role=role, password_hash=pw_hash))
    db.commit()
    db.close()
    creds = {"company": "acme", "email": email, "password": "pw123456"}
    r = client.post("/api/v1/auth/login", json=creds)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_role_without_permission_is_denied(client, headers):
    import jwt as pyjwt

    from app.core.config import get_settings

    s = get_settings()
    cid = pyjwt.decode(
        headers["Authorization"].split()[1], s.jwt_secret, algorithms=[s.jwt_alg]
    )["cid"]

    run_id = uuid.uuid4().hex[:8]
    plain_headers = _login_as(client, cid, f"worker-{run_id}@acme.test", "employee")
    r = client.get("/api/v1/hr/employees", headers=plain_headers)
    assert r.status_code == 403

    r = client.post(
        "/api/v1/hr/employees", json={"full_name": "Nobody"}, headers=plain_headers
    )
    assert r.status_code == 403

    # a manager CAN read but still can't write
    mgr_headers = _login_as(client, cid, f"mgr-{run_id}@acme.test", "manager")
    assert client.get("/api/v1/hr/employees", headers=mgr_headers).status_code == 200
    r = client.post(
        "/api/v1/hr/employees", json={"full_name": "Nobody"}, headers=mgr_headers
    )
    assert r.status_code == 403
