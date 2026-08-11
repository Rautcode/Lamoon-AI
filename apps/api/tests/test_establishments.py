"""Establishment assignment.

The point of assignment is not the foreign key. It is that a Delhi employee
stops paying Maharashtra's professional tax. These tests assert the computed
figure, not the column.
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
    sub = f"est-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Est Co", "subdomain": sub, "email": admin,
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
    return {"sub": sub, "hr": hr, "employee": emp}


def _manager(client, org):
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
    return {"Authorization": f"Bearer {tok['access_token']}"}


def _est(client, org, name, state, **kw):
    r = client.post("/api/v1/payroll/establishments", headers=org["hr"],
                    json={"name": name, "state_code": state, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _assign(client, org, est_id, employee_ids, headers=None):
    return client.post(f"/api/v1/payroll/establishments/{est_id}/employees",
                       json={"employee_ids": employee_ids}, headers=headers or org["hr"])


def _pt(client, org):
    slip = client.post("/api/v1/payroll/runs", json={"period": PERIOD},
                       headers=org["hr"]).json()["payslips"][0]
    return {d["code"]: D(d["amount"]) for d in slip["breakdown"]["deductions"]}["PT"]


# --- assignment changes what is computed ------------------------------------


def test_assignment_moves_someone_onto_their_states_professional_tax(client, org):
    """Maharashtra levies PT, Delhi does not. Same salary, two answers."""
    mh = _est(client, org, "Mumbai", "MH")
    dl = _est(client, org, "Delhi", "DL")
    client.put(f"/api/v1/payroll/pt-slabs?establishment_id={mh['id']}", headers=org["hr"],
               json=[{"up_to": None, "amount": "200"}])
    # Delhi is given no schedule at all, which IS the answer there.

    emp_id = org["employee"]["id"]
    assert _assign(client, org, mh["id"], [emp_id]).status_code == 200
    assert _pt(client, org) == D("200.00")

    assert _assign(client, org, dl["id"], [emp_id]).status_code == 200
    assert _pt(client, org) == D("0"), "Delhi does not levy professional tax"


def test_two_establishments_never_share_a_schedule(client, org):
    """Replacing Maharashtra's slabs leaves Karnataka's alone, and nobody is
    charged a blend of the two."""
    mh = _est(client, org, "Mumbai", "MH")
    ka = _est(client, org, "Bengaluru", "KA")
    client.put(f"/api/v1/payroll/pt-slabs?establishment_id={mh['id']}", headers=org["hr"],
               json=[{"up_to": None, "amount": "200"}])
    client.put(f"/api/v1/payroll/pt-slabs?establishment_id={ka['id']}", headers=org["hr"],
               json=[{"up_to": "25000", "amount": "0"}, {"up_to": None, "amount": "200"}])

    assert len(client.get(f"/api/v1/payroll/pt-slabs?establishment_id={mh['id']}",
                          headers=org["hr"]).json()) == 1
    assert len(client.get(f"/api/v1/payroll/pt-slabs?establishment_id={ka['id']}",
                          headers=org["hr"]).json()) == 2


def test_an_unassigned_employee_uses_the_company_wide_schedule(client, org):
    """The single-state arrangement keeps working untouched."""
    client.put("/api/v1/payroll/pt-slabs", headers=org["hr"],
               json=[{"up_to": None, "amount": "175"}])
    assert _pt(client, org) == D("175.00")


def test_an_assigned_employee_ignores_the_company_wide_schedule(client, org):
    """Resolution is not a fallback chain. An explicit establishment is an
    explicit answer, and an empty schedule there means no PT rather than
    'use the other one' — which would deduct Maharashtra's tax in Delhi."""
    client.put("/api/v1/payroll/pt-slabs", headers=org["hr"],
               json=[{"up_to": None, "amount": "175"}])
    dl = _est(client, org, "Delhi", "DL")
    _assign(client, org, dl["id"], [org["employee"]["id"]])
    assert _pt(client, org) == D("0")


def test_assignment_states_what_it_changed(client, org):
    mh = _est(client, org, "Mumbai", "MH")
    out = _assign(client, org, mh["id"], [org["employee"]["id"]]).json()
    assert out["assigned"] == 1
    assert "Mumbai" in out["note"] and "MH" in out["note"]
    assert "Finalized periods are unchanged" in out["note"]


def test_assignment_does_not_disturb_a_finalized_period(client, org):
    """A frozen payslip was correct under the jurisdiction in force when it was
    paid, and stays that way."""
    client.put("/api/v1/payroll/pt-slabs", headers=org["hr"],
               json=[{"up_to": None, "amount": "175"}])
    run = client.post("/api/v1/payroll/runs", json={"period": PERIOD},
                      headers=org["hr"]).json()
    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])
    before = run["net_total"]

    dl = _est(client, org, "Delhi", "DL")
    assert _assign(client, org, dl["id"], [org["employee"]["id"]]).status_code == 200
    after = client.get(f"/api/v1/payroll/runs/{run['id']}", headers=org["hr"]).json()
    assert after["net_total"] == before


# --- the awkward cases -------------------------------------------------------


def test_an_unknown_id_fails_the_whole_assignment(client, org):
    """Partial success on a jurisdiction change is worse than none — you would
    not know which half applied."""
    mh = _est(client, org, "Mumbai", "MH")
    r = _assign(client, org, mh["id"], [org["employee"]["id"], str(uuid.uuid4())])
    assert r.status_code == 404
    emp = client.get(f"/api/v1/hr/employees/{org['employee']['id']}",
                     headers=org["hr"]).json()
    assert emp["establishment_id"] is None


def test_an_establishment_with_people_cannot_be_deleted(client, org):
    """Deleting it would silently move them onto a different state's tax."""
    mh = _est(client, org, "Mumbai", "MH")
    _assign(client, org, mh["id"], [org["employee"]["id"]])

    r = client.delete(f"/api/v1/payroll/establishments/{mh['id']}", headers=org["hr"])
    assert r.status_code == 409
    assert "still attached" in r.text

    empty = _est(client, org, "Unused", "GJ")
    assert client.delete(f"/api/v1/payroll/establishments/{empty['id']}",
                         headers=org["hr"]).status_code == 204


def test_a_single_employee_can_be_assigned_on_their_own_record(client, org):
    mh = _est(client, org, "Mumbai", "MH")
    r = client.patch(f"/api/v1/hr/employees/{org['employee']['id']}",
                     json={"establishment_id": mh["id"]}, headers=org["hr"])
    assert r.status_code == 200, r.text
    assert r.json()["establishment_id"] == mh["id"]
    assert r.json()["full_name"] == "Ravi Kumar", "PATCH must not disturb other fields"


def test_moving_the_default_takes_it_off_the_previous_one(client, org):
    a = _est(client, org, "First", "MH", is_default=True)
    b = _est(client, org, "Second", "KA")
    client.patch(f"/api/v1/payroll/establishments/{b['id']}", headers=org["hr"],
                 json={"name": "Second", "state_code": "KA", "is_default": True})

    listed = client.get("/api/v1/payroll/establishments", headers=org["hr"]).json()
    defaults = [e["id"] for e in listed if e["is_default"]]
    assert defaults == [b["id"]]
    assert a["id"] not in defaults


def test_state_codes_normalise(client, org):
    assert _est(client, org, "Mumbai", "mh")["state_code"] == "MH"


def test_a_manager_cannot_assign_jurisdiction(client, org):
    """It decides somebody's statutory deductions, so it is payroll's call —
    even though a manager may approve the work that person did."""
    mgr = _manager(client, org)
    mh = _est(client, org, "Mumbai", "MH")
    assert _assign(client, org, mh["id"], [org["employee"]["id"]],
                   headers=mgr).status_code == 403
    assert client.patch(f"/api/v1/payroll/establishments/{mh['id']}", headers=mgr,
                        json={"name": "X", "state_code": "KA"}).status_code == 403


# --- minimum wage follows the establishment too ------------------------------


def test_each_employee_is_measured_against_their_own_floor(client, org):
    """Taking the lowest floor across the company would clear a Mumbai worker
    against a rate set for somewhere cheaper."""
    from datetime import date, datetime

    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.modules.payroll import validation
    from app.modules.payroll.workforce import WorkFact

    expensive = _est(client, org, "Mumbai", "MH", minimum_daily_wage="2000.00")
    _est(client, org, "Nagpur", "MH", minimum_daily_wage="400.00")
    _assign(client, org, expensive["id"], [org["employee"]["id"]])

    s = get_settings()
    cid = pyjwt.decode(org["hr"]["Authorization"].split()[1], s.jwt_secret,
                       algorithms=[s.jwt_alg])["cid"]
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})
    for d in range(1, 27):
        db.add(WorkFact(company_id=uuid.UUID(cid),
                        employee_id=uuid.UUID(org["employee"]["id"]),
                        day=date(2026, 9, d), status="worked",
                        hours_worked=D("8"),
                        approved_at=datetime.now(tz=None).astimezone()))
    db.commit()
    db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})

    client.post("/api/v1/payroll/runs", json={"period": PERIOD}, headers=org["hr"])
    findings = validation.validate(db, company_id=uuid.UUID(cid), period=date(2026, 9, 1))
    db.close()

    # 26,000 over 26 days is 1,000/day — under Mumbai's 2,000, over Nagpur's 400.
    low = [f for f in findings if f.code == "below_minimum_wage"]
    assert low, "should be measured against Mumbai's floor, not Nagpur's"
    assert low[0].severity == validation.WARNING
