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


# --- PATCH is a partial update, not a replace --------------------------------
#
# Regression. Both PATCH handlers took the CREATE schema and wrote every field
# back, so any field the caller omitted arrived as its default and overwrote
# real data. Renaming somebody erased their email, department and joining date
# and reset an 'exited' employee to 'active'.


@pytest.fixture
def person(client, headers):
    """An employee with every optional field populated, so a partial PATCH has
    something to destroy."""
    dept = client.post(
        "/api/v1/hr/departments", json={"name": f"Dept-{uuid.uuid4().hex[:6]}"}, headers=headers
    ).json()
    emp = client.post(
        "/api/v1/hr/employees",
        json={
            "full_name": "Original Name",
            "email": f"orig-{uuid.uuid4().hex[:6]}@acme.test",
            "department_id": dept["id"],
            "status": "probation",
            "joined_on": "2020-01-15",
        },
        headers=headers,
    ).json()
    return {"employee": emp, "department": dept}


def test_patching_one_field_leaves_the_others_alone(client, headers, person):
    emp = person["employee"]
    r = client.patch(
        f"/api/v1/hr/employees/{emp['id']}", json={"full_name": "Renamed"}, headers=headers
    )
    assert r.status_code == 200, r.text
    after = r.json()

    assert after["full_name"] == "Renamed"
    assert after["email"] == emp["email"]
    assert after["department_id"] == emp["department_id"]
    assert after["joined_on"] == "2020-01-15"
    assert after["status"] == "probation"


def test_patch_does_not_resurrect_an_exited_employee(client, headers, person):
    """The costly version of the bug: `status` is how a payroll run excludes
    leavers, so a rename that reset it to 'active' put a departed person back
    on the payroll."""
    emp = person["employee"]
    client.patch(f"/api/v1/hr/employees/{emp['id']}", json={"status": "exited"}, headers=headers)

    after = client.patch(
        f"/api/v1/hr/employees/{emp['id']}", json={"full_name": "Left Last Month"},
        headers=headers,
    ).json()
    assert after["status"] == "exited"


def test_patch_can_still_clear_a_field_with_an_explicit_null(client, headers, person):
    """Omitted and null must mean different things, or there's no way to
    un-assign someone from a department."""
    emp = person["employee"]
    after = client.patch(
        f"/api/v1/hr/employees/{emp['id']}", json={"department_id": None}, headers=headers
    ).json()
    assert after["department_id"] is None
    assert after["full_name"] == "Original Name"  # untouched


def test_patch_accepts_a_body_without_full_name(client, headers, person):
    """This is how the bug was found: PATCHing only an email 422'd, because
    the create schema required full_name."""
    emp = person["employee"]
    r = client.patch(
        f"/api/v1/hr/employees/{emp['id']}", json={"email": "new@acme.test"}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "new@acme.test"
    assert r.json()["full_name"] == "Original Name"


def test_patch_rejects_an_explicit_null_name(client, headers, person):
    """Optional-for-PATCH must not mean nullable — the column isn't."""
    r = client.patch(
        f"/api/v1/hr/employees/{person['employee']['id']}",
        json={"full_name": None}, headers=headers,
    )
    assert r.status_code == 422


def test_patching_a_department_leaves_its_parent_alone(client, headers, person):
    """Same bug, same shape, on the other PATCH handler."""
    parent = client.post(
        "/api/v1/hr/departments", json={"name": f"Parent-{uuid.uuid4().hex[:6]}"}, headers=headers
    ).json()
    child = client.post(
        "/api/v1/hr/departments",
        json={"name": f"Child-{uuid.uuid4().hex[:6]}", "parent_id": parent["id"]},
        headers=headers,
    ).json()

    after = client.patch(
        f"/api/v1/hr/departments/{child['id']}", json={"name": "Renamed Child"}, headers=headers
    ).json()
    assert after["name"] == "Renamed Child"
    assert after["parent_id"] == parent["id"]


def test_update_schemas_cover_every_creatable_field():
    """`*Update` duplicates its `*In` field list (subclassing would widen a
    required field, which mypy rightly rejects). This catches the duplication
    drifting: a field added to create but not to update would silently be
    un-editable."""
    from app.modules.hr_core.schemas import (
        DepartmentIn,
        DepartmentUpdate,
        EmployeeIn,
        EmployeeUpdate,
    )

    assert set(EmployeeUpdate.model_fields) == set(EmployeeIn.model_fields)
    assert set(DepartmentUpdate.model_fields) == set(DepartmentIn.model_fields)
    # And every update field must be genuinely optional, or PATCH still forces
    # callers to resend data they aren't changing.
    for model in (EmployeeUpdate, DepartmentUpdate):
        required = [n for n, f in model.model_fields.items() if f.is_required()]
        assert not required, f"{model.__name__} still requires {required}"


def test_patch_rejects_an_explicit_null_status(client, headers, person):
    r = client.patch(
        f"/api/v1/hr/employees/{person['employee']['id']}",
        json={"status": None}, headers=headers,
    )
    assert r.status_code == 422
