"""Rebuilding the ledger without computing payroll.

Looking at what a period will consist of, and paying against it, are separate
acts. These test that the first is possible, cheap, repeatable, and cannot
quietly destroy anything a person entered.
"""
import uuid
from datetime import date, datetime
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
    sub = f"reb-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Rebuild Co", "subdomain": sub, "email": admin,
                      "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": admin, "password": "pw123456"}).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    basic = client.post("/api/v1/payroll/components",
                        json={"code": "BASIC", "name": "Basic", "wage_basis": "wages"},
                        headers=hr).json()
    emp = client.post("/api/v1/hr/employees",
                      json={"full_name": "Ravi Kumar"}, headers=hr).json()
    client.put(f"/api/v1/payroll/employees/{emp['id']}/salary",
               json={"components": [{"component_id": basic["id"], "amount": "24000.00"}]},
               headers=hr)
    return {"sub": sub, "hr": hr, "employee": emp, "basic": basic}


def _rebuild(client, org, **kw):
    return client.post("/api/v1/payroll/inputs/rebuild",
                       json={"period": PERIOD, **kw}, headers=org["hr"])


def _inputs(client, org, employee_id=None):
    eid = employee_id or org["employee"]["id"]
    return client.get(f"/api/v1/payroll/inputs?employee_id={eid}&period={PERIOD}",
                      headers=org["hr"]).json()


def test_the_ledger_can_be_built_without_running_payroll(client, org):
    """The point of the route: see August before paying it."""
    assert client.get("/api/v1/payroll/runs", headers=org["hr"]).json() == []

    out = _rebuild(client, org)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["employees"] == 1
    assert body["derived"] == 1
    assert body["preserved"] == 0

    rows = _inputs(client, org)
    assert [r["code"] for r in rows] == ["BASIC"]
    assert D(rows[0]["amount"]) == D("24000.00")
    # And still no run was created.
    assert client.get("/api/v1/payroll/runs", headers=org["hr"]).json() == []


def test_rebuilding_is_idempotent(client, org):
    """calculate(calculate(x)) == calculate(x), applied to the ledger."""
    first = _rebuild(client, org).json()
    for _ in range(3):
        again = _rebuild(client, org).json()
        assert again == first
    rows = _inputs(client, org)
    assert len(rows) == 1, "derived rows are replaced, never accumulated"


def test_rebuilding_preserves_a_manual_entry_and_reports_it(client, org):
    """The asymmetry the ledger rests on, now visible in the response."""
    client.post("/api/v1/payroll/inputs", headers=org["hr"], json={
        "employee_id": org["employee"]["id"], "period": PERIOD, "kind": "earning",
        "code": "BONUS", "name": "Festival bonus", "amount": "3000.00",
        "reason": "Diwali",
    })
    body = _rebuild(client, org).json()
    assert body["derived"] == 1
    assert body["preserved"] == 1, "a person's entry survives and is counted"
    assert body["pending"] == 1, "and is reported as still awaiting approval"

    codes = {r["code"] for r in _inputs(client, org)}
    assert codes == {"BASIC", "BONUS"}


def test_approving_an_entry_clears_it_from_pending(client, org):
    row = client.post("/api/v1/payroll/inputs", headers=org["hr"], json={
        "employee_id": org["employee"]["id"], "period": PERIOD, "kind": "earning",
        "code": "BONUS", "name": "Festival bonus", "amount": "3000.00",
    }).json()
    assert _rebuild(client, org).json()["pending"] == 1

    client.post("/api/v1/payroll/inputs/approve", json={"ids": [row["id"]]},
                headers=org["hr"])
    body = _rebuild(client, org).json()
    assert body["preserved"] == 1
    assert body["pending"] == 0


def test_a_salary_change_reaches_the_ledger_only_on_rebuild(client, org):
    """The ledger belongs to the period. Editing a salary structure does not
    reach back into an already-generated month until somebody regenerates it —
    which is the whole reason the ledger exists."""
    _rebuild(client, org)
    assert D(_inputs(client, org)[0]["amount"]) == D("24000.00")

    client.put(f"/api/v1/payroll/employees/{org['employee']['id']}/salary",
               json={"components": [{"component_id": org["basic"]["id"],
                                     "amount": "30000.00"}]}, headers=org["hr"])
    assert D(_inputs(client, org)[0]["amount"]) == D("24000.00"), \
        "the open period is untouched until regenerated"

    _rebuild(client, org)
    assert D(_inputs(client, org)[0]["amount"]) == D("30000.00")


def test_rebuilding_one_person_leaves_everybody_else_alone(client, org):
    other = client.post("/api/v1/hr/employees", json={"full_name": "Priya Shah"},
                        headers=org["hr"]).json()
    client.put(f"/api/v1/payroll/employees/{other['id']}/salary",
               json={"components": [{"component_id": org["basic"]["id"],
                                     "amount": "40000.00"}]}, headers=org["hr"])
    _rebuild(client, org)

    # Change both salaries, regenerate only one.
    for emp_id, amount in ((org["employee"]["id"], "26000.00"), (other["id"], "44000.00")):
        client.put(f"/api/v1/payroll/employees/{emp_id}/salary",
                   json={"components": [{"component_id": org["basic"]["id"],
                                         "amount": amount}]}, headers=org["hr"])

    body = _rebuild(client, org, employee_id=org["employee"]["id"]).json()
    assert body["employees"] == 1

    assert D(_inputs(client, org)[0]["amount"]) == D("26000.00")
    assert D(_inputs(client, org, other["id"])[0]["amount"]) == D("40000.00")


def test_approved_overtime_is_priced_the_same_whichever_path_ran(client, org):
    """A rebuild and a payroll run must agree on the overtime rate. They divide
    by the same working-day count now, which they previously did in two
    separate implementations."""
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.modules.payroll.workforce import WorkFact

    s = get_settings()
    cid = pyjwt.decode(org["hr"]["Authorization"].split()[1], s.jwt_secret,
                       algorithms=[s.jwt_alg])["cid"]
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})
    for d in (1, 2, 3):
        db.add(WorkFact(company_id=uuid.UUID(cid),
                        employee_id=uuid.UUID(org["employee"]["id"]),
                        day=date(2026, 9, d), status="worked", hours_worked=D("10"),
                        overtime_hours=D("2"),
                        approved_at=datetime.now(tz=None).astimezone()))
    db.commit()
    db.close()

    _rebuild(client, org)
    from_rebuild = next(r for r in _inputs(client, org) if r["code"] == "OT")

    client.post("/api/v1/payroll/runs", json={"period": PERIOD}, headers=org["hr"])
    from_run = next(r for r in _inputs(client, org) if r["code"] == "OT")

    assert D(from_rebuild["amount"]) == D(from_run["amount"])
    assert D(from_rebuild["rate"]) == D(from_run["rate"])
    assert D(from_rebuild["quantity"]) == D("6.00")


def test_a_finalized_period_cannot_be_rebuilt(client, org):
    """History is not regenerated."""
    run = client.post("/api/v1/payroll/runs", json={"period": PERIOD},
                      headers=org["hr"]).json()
    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])

    r = _rebuild(client, org)
    assert r.status_code == 409
    assert "finalized" in r.text and "adjustment" in r.text


def test_an_unknown_employee_is_a_404(client, org):
    assert _rebuild(client, org, employee_id=str(uuid.uuid4())).status_code == 404


def test_somebody_who_joins_later_gets_no_inputs(client, org):
    """Generating rows for them would be noise in the exception list."""
    later = client.post("/api/v1/hr/employees",
                        json={"full_name": "Future Hire", "joined_on": "2026-12-01"},
                        headers=org["hr"]).json()
    client.put(f"/api/v1/payroll/employees/{later['id']}/salary",
               json={"components": [{"component_id": org["basic"]["id"],
                                     "amount": "50000.00"}]}, headers=org["hr"])

    body = _rebuild(client, org).json()
    assert body["employees"] == 1, "only the person actually employed in September"
    assert _inputs(client, org, later["id"]) == []


def test_only_payroll_can_rebuild(client, org):
    """It decides what people will be paid, so a supervisor cannot trigger it
    even though they can approve the work behind it."""
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import hash_password
    from app.modules.auth.models import User

    s = get_settings()
    cid = pyjwt.decode(org["hr"]["Authorization"].split()[1], s.jwt_secret,
                       algorithms=[s.jwt_alg])["cid"]
    email = f"mgr-{uuid.uuid4().hex[:6]}@{org['sub']}.test"
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})
    db.add(User(company_id=uuid.UUID(cid), email=email, role="manager",
                password_hash=hash_password("pw123456")))
    db.commit()
    db.close()
    tok = client.post("/api/v1/auth/login",
                      json={"company": org["sub"], "email": email,
                            "password": "pw123456"}).json()
    mgr = {"Authorization": f"Bearer {tok['access_token']}"}

    assert client.post("/api/v1/payroll/inputs/rebuild", json={"period": PERIOD},
                       headers=mgr).status_code == 403
