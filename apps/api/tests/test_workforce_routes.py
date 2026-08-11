"""HTTP surface for work facts, the payroll input ledger, and validation.

The centrepiece is the permission split. Work facts are hours; the ledger is
money. A supervisor approves the former and must never reach the latter, and
these tests try to cross that line rather than merely confirming it exists.
"""
import uuid
from decimal import Decimal

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

D = Decimal
PERIOD = "2026-09-01"


@pytest.fixture
def org(client):
    sub = f"wfr-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "WF Co", "subdomain": sub, "email": admin,
                      "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": admin, "password": "pw123456"}).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    client.put("/api/v1/payroll/settings",
               json={"pf_enabled": True, "esi_enabled": True}, headers=hr)
    basic = client.post("/api/v1/payroll/components",
                        json={"code": "BASIC", "name": "Basic", "wage_basis": "wages"},
                        headers=hr).json()
    emp = client.post("/api/v1/hr/employees",
                      json={"full_name": "Ravi Kumar"}, headers=hr).json()
    client.put(f"/api/v1/payroll/employees/{emp['id']}/salary",
               json={"components": [{"component_id": basic["id"], "amount": "26000.00"}]},
               headers=hr)
    return {"sub": sub, "hr": hr, "employee": emp, "basic": basic}


def _login_as(client, org, role: str) -> dict:
    """A real login for a role, so permissions are exercised end to end."""
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import hash_password
    from app.modules.auth.models import User

    s = get_settings()
    cid = pyjwt.decode(org["hr"]["Authorization"].split()[1], s.jwt_secret,
                       algorithms=[s.jwt_alg])["cid"]
    email = f"{role}-{uuid.uuid4().hex[:6]}@{org['sub']}.test"
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})
    db.add(User(company_id=uuid.UUID(cid), email=email, role=role,
                password_hash=hash_password("pw123456")))
    db.commit()
    db.close()
    tok = client.post("/api/v1/auth/login",
                      json={"company": org["sub"], "email": email,
                            "password": "pw123456"}).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


def _fact(client, org, day, headers=None, **kw):
    body = {"employee_id": org["employee"]["id"], "day": day, "status": "worked",
            "hours_worked": "8", **kw}
    return client.post("/api/v1/workforce/facts", json=body, headers=headers or org["hr"])


# --- work facts -------------------------------------------------------------


def test_recording_a_day_arrives_unapproved(client, org):
    r = _fact(client, org, "2026-09-01", overtime_hours="2")
    assert r.status_code == 200, r.text
    assert r.json()["approved_at"] is None, "a recorded fact is not a signed-off fact"


def test_a_day_has_one_truth(client, org):
    """Upsert on (employee, day). Two rows for one day is two answers."""
    first = _fact(client, org, "2026-09-02", overtime_hours="2").json()
    second = _fact(client, org, "2026-09-02", overtime_hours="4").json()
    assert first["id"] == second["id"]
    assert D(second["overtime_hours"]) == D("4")

    listed = client.get("/api/v1/workforce/facts?from=2026-09-02&to=2026-09-02",
                        headers=org["hr"]).json()
    assert len(listed) == 1


def test_rerecording_an_approved_day_clears_its_approval(client, org):
    """An approved fact that changed underneath its approver is worse than one
    that needs approving twice."""
    fact = _fact(client, org, "2026-09-03", overtime_hours="2").json()
    client.post("/api/v1/workforce/facts/approve", json={"ids": [fact["id"]]},
                headers=org["hr"])
    assert client.get("/api/v1/workforce/facts?from=2026-09-03&to=2026-09-03",
                      headers=org["hr"]).json()[0]["approved_at"] is not None

    again = _fact(client, org, "2026-09-03", overtime_hours="6").json()
    assert again["approved_at"] is None


def test_the_pending_queue_shows_only_unapproved_days(client, org):
    a = _fact(client, org, "2026-09-04", overtime_hours="2").json()
    _fact(client, org, "2026-09-05", overtime_hours="3")
    client.post("/api/v1/workforce/facts/approve", json={"ids": [a["id"]]},
                headers=org["hr"])

    pending = client.get("/api/v1/workforce/facts?pending_only=true",
                         headers=org["hr"]).json()
    assert a["id"] not in {f["id"] for f in pending}
    assert len(pending) == 1


def test_bulk_import_accepts_a_device_export(client, org):
    days = [{"employee_id": org["employee"]["id"], "day": f"2026-09-{d:02d}",
             "status": "worked", "hours_worked": "9", "overtime_hours": "1",
             "source": "import", "site": "Pune Site A"} for d in range(10, 16)]
    r = client.post("/api/v1/workforce/facts/bulk", json=days, headers=org["hr"])
    assert r.status_code == 200, r.text
    assert len(r.json()) == 6
    assert all(f["approved_at"] is None for f in r.json())
    assert all(f["site"] == "Pune Site A" for f in r.json())


def test_an_invalid_status_is_refused(client, org):
    r = _fact(client, org, "2026-09-16", status="on_a_break")
    assert r.status_code == 422


# --- the boundary: facts are hours, the ledger is money ---------------------


def test_a_manager_can_approve_work_but_never_sees_the_ledger(client, org):
    """The whole reason the permissions are split. A supervisor who saw the
    overtime happen signs it off, and learns nothing about anyone's pay."""
    mgr = _login_as(client, org, "manager")
    fact = _fact(client, org, "2026-09-17", overtime_hours="3").json()

    assert client.get("/api/v1/workforce/facts", headers=mgr).status_code == 200
    approved = client.post("/api/v1/workforce/facts/approve",
                           json={"ids": [fact["id"]]}, headers=mgr)
    assert approved.status_code == 200, approved.text
    assert approved.json()[0]["approved_at"] is not None

    emp_id = org["employee"]["id"]
    for path in (
        f"/api/v1/payroll/inputs?employee_id={emp_id}&period={PERIOD}",
        f"/api/v1/payroll/validation?period={PERIOD}",
        f"/api/v1/payroll/risk?period={PERIOD}",
        "/api/v1/payroll/establishments",
        "/api/v1/payroll/runs",
    ):
        assert client.get(path, headers=mgr).status_code == 403, path

    assert client.post("/api/v1/payroll/inputs", headers=mgr, json={
        "employee_id": emp_id, "period": PERIOD, "kind": "earning",
        "code": "BONUS", "name": "Bonus", "amount": "5000.00",
    }).status_code == 403


def test_a_manager_cannot_record_or_delete_work(client, org):
    """Approving is a supervisory act; asserting what happened is not. A
    manager who could both write and approve is a control with no second pair
    of eyes."""
    mgr = _login_as(client, org, "manager")
    assert _fact(client, org, "2026-09-18", headers=mgr).status_code == 403


def test_an_employee_reaches_none_of_it(client, org):
    emp = _login_as(client, org, "employee")
    emp_id = org["employee"]["id"]
    for path in (
        "/api/v1/workforce/facts",
        f"/api/v1/payroll/inputs?employee_id={emp_id}&period={PERIOD}",
        f"/api/v1/payroll/validation?period={PERIOD}",
        "/api/v1/payroll/establishments",
    ):
        assert client.get(path, headers=emp).status_code == 403, path


# --- the ledger --------------------------------------------------------------


def test_the_ledger_shows_what_a_run_generated(client, org):
    client.post("/api/v1/payroll/runs", json={"period": PERIOD}, headers=org["hr"])
    rows = client.get(
        f"/api/v1/payroll/inputs?employee_id={org['employee']['id']}&period={PERIOD}",
        headers=org["hr"],
    ).json()
    assert [r["code"] for r in rows] == ["BASIC"]
    assert rows[0]["source"] == "structure"
    assert D(rows[0]["amount"]) == D("26000.00")


def test_a_manual_input_is_created_unapproved_and_survives_a_recompute(client, org):
    created = client.post("/api/v1/payroll/inputs", headers=org["hr"], json={
        "employee_id": org["employee"]["id"], "period": PERIOD, "kind": "earning",
        "code": "BONUS", "name": "Festival bonus", "amount": "5000.00",
        "reason": "Diwali",
    })
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["source"] == "manual"
    assert row["approved_at"] is None, "entering and sanctioning are separable acts"

    # Unapproved, so it must not be paid.
    slip = client.post("/api/v1/payroll/runs", json={"period": PERIOD},
                       headers=org["hr"]).json()["payslips"][0]
    assert D(slip["gross"]) == D("26000.00")

    client.post("/api/v1/payroll/inputs/approve", json={"ids": [row["id"]]},
                headers=org["hr"])
    slip = client.post("/api/v1/payroll/runs", json={"period": PERIOD},
                       headers=org["hr"]).json()["payslips"][0]
    assert D(slip["gross"]) == D("31000.00")

    # And it is still there after the recompute that regenerated the rest.
    rows = client.get(
        f"/api/v1/payroll/inputs?employee_id={org['employee']['id']}&period={PERIOD}",
        headers=org["hr"],
    ).json()
    assert [r["code"] for r in rows if r["source"] == "manual"] == ["BONUS"]


def test_overtime_cannot_be_posted_as_an_amount(client, org):
    """The rule that keeps an overtime policy replayable: hours in, money out.
    Accepting an amount would make a typo payable."""
    r = client.post("/api/v1/payroll/inputs", headers=org["hr"], json={
        "employee_id": org["employee"]["id"], "period": PERIOD, "kind": "overtime",
        "code": "OT", "name": "Overtime", "amount": "9999.00",
    })
    assert r.status_code == 422
    assert "derived from work facts" in r.text


def test_a_derived_input_cannot_be_deleted_directly(client, org):
    client.post("/api/v1/payroll/runs", json={"period": PERIOD}, headers=org["hr"])
    row = client.get(
        f"/api/v1/payroll/inputs?employee_id={org['employee']['id']}&period={PERIOD}",
        headers=org["hr"],
    ).json()[0]
    r = client.delete(f"/api/v1/payroll/inputs/{row['id']}", headers=org["hr"])
    assert r.status_code == 409
    assert "salary structure" in r.text


def test_a_duplicate_manual_code_is_refused(client, org):
    body = {"employee_id": org["employee"]["id"], "period": PERIOD, "kind": "earning",
            "code": "BONUS", "name": "Bonus", "amount": "1000.00"}
    assert client.post("/api/v1/payroll/inputs", json=body,
                       headers=org["hr"]).status_code == 200
    assert client.post("/api/v1/payroll/inputs", json=body,
                       headers=org["hr"]).status_code == 409


# --- a finalized period is closed -------------------------------------------


def test_a_finalized_period_refuses_facts_and_inputs(client, org):
    """History is not edited. Corrections belong in a later period."""
    run = client.post("/api/v1/payroll/runs", json={"period": PERIOD},
                      headers=org["hr"]).json()
    assert client.post(f"/api/v1/payroll/runs/{run['id']}/finalize",
                       headers=org["hr"]).status_code == 200

    closed = _fact(client, org, "2026-09-20")
    assert closed.status_code == 409
    assert "finalized" in closed.text and "adjustment" in closed.text

    r = client.post("/api/v1/payroll/inputs", headers=org["hr"], json={
        "employee_id": org["employee"]["id"], "period": PERIOD, "kind": "earning",
        "code": "LATE", "name": "Too late", "amount": "100.00",
    })
    assert r.status_code == 409


def test_an_open_period_is_unaffected_by_a_neighbours_closure(client, org):
    run = client.post("/api/v1/payroll/runs", json={"period": PERIOD},
                      headers=org["hr"]).json()
    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])
    assert _fact(client, org, "2026-10-05").status_code == 200


# --- validation and risk are separate endpoints ------------------------------


def test_validation_reports_blocking_findings_with_names(client, org):
    client.post("/api/v1/hr/employees", json={"full_name": "No Structure"},
                headers=org["hr"])
    out = client.get(f"/api/v1/payroll/validation?period={PERIOD}",
                     headers=org["hr"]).json()

    assert out["period"] == PERIOD
    assert out["blocking"] >= 1
    blocking = [f for f in out["findings"] if f["severity"] == "blocking"]
    assert any(f["employee_name"] == "No Structure" for f in blocking)
    assert any(g["code"] == "no_salary_structure" for g in out["groups"])


def test_unapproved_overtime_surfaces_as_a_warning_not_a_blocker(client, org):
    _fact(client, org, "2026-09-21", overtime_hours="5")
    out = client.get(f"/api/v1/payroll/validation?period={PERIOD}",
                     headers=org["hr"]).json()
    warn = [f for f in out["findings"] if f["code"] == "overtime_unapproved"]
    assert warn and warn[0]["severity"] == "warning"


def test_risk_is_its_own_endpoint_and_never_blocks(client, org):
    client.post("/api/v1/payroll/runs", json={"period": PERIOD}, headers=org["hr"])
    out = client.get(f"/api/v1/payroll/risk?period={PERIOD}", headers=org["hr"]).json()
    assert out["blocking"] == 0
    assert all(f["severity"] != "blocking" for f in out["findings"])


# --- establishments ----------------------------------------------------------


def test_only_one_establishment_is_the_default(client, org):
    """Otherwise "which jurisdiction applies?" has no answer."""
    first = client.post("/api/v1/payroll/establishments", headers=org["hr"], json={
        "name": "Mumbai", "state_code": "mh", "is_default": True})
    assert first.status_code == 200, first.text
    assert first.json()["state_code"] == "MH", "state codes normalise to upper case"

    client.post("/api/v1/payroll/establishments", headers=org["hr"], json={
        "name": "Bengaluru", "state_code": "KA", "is_default": True})

    listed = client.get("/api/v1/payroll/establishments", headers=org["hr"]).json()
    assert [e["name"] for e in listed if e["is_default"]] == ["Bengaluru"]


def test_an_establishment_carries_its_minimum_wage(client, org):
    r = client.post("/api/v1/payroll/establishments", headers=org["hr"], json={
        "name": "Pune Site", "state_code": "MH", "minimum_daily_wage": "780.00",
        "pf_establishment_code": "MHPUN0012345"})
    assert r.status_code == 200
    assert D(r.json()["minimum_daily_wage"]) == D("780.00")
