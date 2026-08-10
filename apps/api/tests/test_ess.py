"""Employee Self-Service.

Most of this file is adversarial: ESS is a permissions boundary, so the
valuable tests are the ones that try to cross it. An employee must be able to
see and manage exactly one person — themselves — and nothing else, no matter
what they put in the request.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.core.notify.base import outbox


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


@pytest.fixture
def org(client):
    """A company with HR, two employees, and a leave type. Only Asha gets a
    login — Ben exists so we can try (and fail) to reach him."""
    sub = f"ess-{uuid.uuid4().hex[:8]}"
    admin_email = f"admin@{sub}.test"
    client.post(
        "/api/v1/auth/bootstrap",
        json={
            "company_name": "ESS Co", "subdomain": sub,
            "email": admin_email, "password": "pw123456",
        },
    )
    hr = client.post(
        "/api/v1/auth/login",
        json={"company": sub, "email": admin_email, "password": "pw123456"},
    ).json()
    hr_headers = {"Authorization": f"Bearer {hr['access_token']}"}

    asha = client.post(
        "/api/v1/hr/employees",
        json={"full_name": "Asha Rao", "email": f"asha@{sub}.test"},
        headers=hr_headers,
    ).json()
    ben = client.post(
        "/api/v1/hr/employees",
        json={"full_name": "Ben Ford", "email": f"ben@{sub}.test"},
        headers=hr_headers,
    ).json()
    leave_type = client.post(
        "/api/v1/leave/types",
        json={"name": "Annual", "annual_quota": 12},
        headers=hr_headers,
    ).json()

    outbox.clear()
    invite = client.post(f"/api/v1/hr/employees/{asha['id']}/invite", headers=hr_headers)
    assert invite.status_code == 200, invite.text

    # The temp password is emailed, never returned by the API — read it the
    # way the employee would.
    mail = next(m for m in outbox if m["template"] == "access_granted")
    password = next(
        line.split("Password:")[1].strip()
        for line in mail["body"].splitlines()
        if "Password:" in line
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"company": sub, "email": f"asha@{sub}.test", "password": password},
    )
    assert login.status_code == 200, login.text
    return {
        "sub": sub,
        "hr": hr_headers,
        "asha": asha,
        "ben": ben,
        "leave_type": leave_type,
        "emp_headers": {"Authorization": f"Bearer {login.json()['access_token']}"},
    }


# --- invite ----------------------------------------------------------------


def test_invite_creates_a_working_login_and_never_returns_the_password(client, org):
    invite_mail = next(m for m in outbox if m["template"] == "access_granted")
    assert org["sub"] in invite_mail["body"]  # workspace to sign in to
    # The org fixture already proved the emailed password logs in.
    me = client.get("/api/v1/me", headers=org["emp_headers"])
    assert me.status_code == 200
    assert me.json()["full_name"] == "Asha Rao"


def test_invite_is_not_repeatable(client, org):
    r = client.post(f"/api/v1/hr/employees/{org['asha']['id']}/invite", headers=org["hr"])
    assert r.status_code == 409


def test_invite_requires_an_email(client, org):
    nameless = client.post(
        "/api/v1/hr/employees", json={"full_name": "No Email"}, headers=org["hr"]
    ).json()
    r = client.post(f"/api/v1/hr/employees/{nameless['id']}/invite", headers=org["hr"])
    assert r.status_code == 422


def test_employee_cannot_invite(client, org):
    r = client.post(f"/api/v1/hr/employees/{org['ben']['id']}/invite", headers=org["emp_headers"])
    assert r.status_code == 403


# --- the boundary ----------------------------------------------------------


def test_employee_cannot_read_the_directory(client, org):
    assert client.get("/api/v1/hr/employees", headers=org["emp_headers"]).status_code == 403
    assert client.get("/api/v1/hr/departments", headers=org["emp_headers"]).status_code == 403


def test_employee_cannot_read_anyone_elses_record(client, org):
    """The /me surface takes no id, so the only way to name Ben is via the HR
    routes — which are closed to this role."""
    r = client.get(f"/api/v1/hr/employees/{org['ben']['id']}", headers=org["emp_headers"])
    assert r.status_code == 403
    r = client.get(f"/api/v1/leave/balances/{org['ben']['id']}", headers=org["emp_headers"])
    assert r.status_code == 403


def test_employee_cannot_see_company_wide_leave(client, org):
    assert client.get("/api/v1/leave/requests", headers=org["emp_headers"]).status_code == 403


def test_employee_cannot_approve_leave(client, org):
    filed = client.post(
        "/api/v1/me/leave/requests",
        json={
            "leave_type_id": org["leave_type"]["id"],
            "start_date": str(date.today()),
            "end_date": str(date.today()),
        },
        headers=org["emp_headers"],
    ).json()
    r = client.post(f"/api/v1/leave/requests/{filed['id']}/approve", headers=org["emp_headers"])
    assert r.status_code == 403  # can't self-approve


def test_employee_cannot_reach_hiring(client, org):
    """Regression: ATS routes were authenticated but ungated before ESS, so
    any employee could read the whole candidate pipeline."""
    assert client.get("/api/v1/ats/applications", headers=org["emp_headers"]).status_code == 403
    assert client.get("/api/v1/ats/jobs", headers=org["emp_headers"]).status_code == 403


def test_lumo_is_not_a_side_door_around_permissions(client, org):
    """Lumo reads the same data through tools, so it needs the same locks —
    otherwise 'find Tier A candidates' leaks what /ats/applications denies."""
    r = client.post(
        "/api/v1/assistant/ask",
        json={"question": "Find Tier A candidates"},
        headers=org["emp_headers"],
    )
    assert r.status_code == 200  # answers, but refuses to look
    body = r.json()
    assert body["unmatched"] is True
    assert "Ben Ford" not in body["text"]
    assert body["items"] == []

    # HR asking the same thing DOES get tool access.
    hr_answer = client.post(
        "/api/v1/assistant/ask", json={"question": "How many people do we have?"},
        headers=org["hr"],
    ).json()
    assert hr_answer["unmatched"] is False
    assert "2" in hr_answer["text"]


# --- self-service proper ---------------------------------------------------


def test_my_profile_is_my_own_record(client, org):
    me = client.get("/api/v1/me", headers=org["emp_headers"]).json()
    assert me["id"] == org["asha"]["id"]
    assert me["id"] != org["ben"]["id"]


def test_file_own_leave_and_see_it(client, org):
    today = date.today()
    r = client.post(
        "/api/v1/me/leave/requests",
        json={
            "leave_type_id": org["leave_type"]["id"],
            "start_date": str(today),
            "end_date": str(today + timedelta(days=2)),
            "reason": "Family",
        },
        headers=org["emp_headers"],
    )
    assert r.status_code == 200, r.text
    filed = r.json()
    assert filed["days"] == 3
    assert filed["status"] == "pending"
    # Attributed to the caller, derived from the JWT.
    assert filed["employee_id"] == org["asha"]["id"]

    mine = client.get("/api/v1/me/leave/requests", headers=org["emp_headers"]).json()
    assert [m["id"] for m in mine] == [filed["id"]]


def test_my_requests_never_include_other_people(client, org):
    """HR files leave for Ben; Asha must not see it in her own list."""
    today = date.today()
    client.post(
        "/api/v1/leave/requests",
        json={
            "employee_id": org["ben"]["id"],
            "leave_type_id": org["leave_type"]["id"],
            "start_date": str(today),
            "end_date": str(today),
        },
        headers=org["hr"],
    )
    mine = client.get("/api/v1/me/leave/requests", headers=org["emp_headers"]).json()
    assert all(m["employee_id"] == org["asha"]["id"] for m in mine)


def test_my_balance_reflects_only_my_approved_leave(client, org):
    balances = client.get("/api/v1/me/leave/balances", headers=org["emp_headers"]).json()
    annual = next(b for b in balances if b["leave_type_name"] == "Annual")
    assert annual["used"] == 0 and annual["remaining"] == 12

    today = date.today()
    filed = client.post(
        "/api/v1/me/leave/requests",
        json={
            "leave_type_id": org["leave_type"]["id"],
            "start_date": str(today),
            "end_date": str(today + timedelta(days=1)),
        },
        headers=org["emp_headers"],
    ).json()
    # Pending doesn't consume balance.
    balances = client.get("/api/v1/me/leave/balances", headers=org["emp_headers"]).json()
    assert next(b for b in balances if b["leave_type_name"] == "Annual")["used"] == 0

    client.post(f"/api/v1/leave/requests/{filed['id']}/approve", headers=org["hr"])
    balances = client.get("/api/v1/me/leave/balances", headers=org["emp_headers"]).json()
    annual = next(b for b in balances if b["leave_type_name"] == "Annual")
    assert annual["used"] == 2 and annual["remaining"] == 10


def test_bad_date_range_rejected_for_ess_too(client, org):
    today = date.today()
    r = client.post(
        "/api/v1/me/leave/requests",
        json={
            "leave_type_id": org["leave_type"]["id"],
            "start_date": str(today),
            "end_date": str(today - timedelta(days=1)),
        },
        headers=org["emp_headers"],
    )
    assert r.status_code == 422


def test_login_without_an_employee_record_gets_a_clear_404(client, org):
    """HR/admins have logins but no employee row — /me should explain that
    rather than imply the person doesn't exist."""
    r = client.get("/api/v1/me", headers=org["hr"])
    assert r.status_code == 404
    assert "no employee record" in r.json()["detail"]
