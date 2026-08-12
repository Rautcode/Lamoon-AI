"""Period-on-period movement and the bridge that explains it.

Almost every test here asserts the same property: the bridge sums EXACTLY to
the change in gross. A decomposition that doesn't close is worse than none —
it invites somebody to trust four lines out of five — so `unexplained` is
checked in every scenario and in every combination of them.
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
AUG = "2026-08-01"
SEP = "2026-09-01"


@pytest.fixture
def org(client):
    sub = f"mov-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Move Co", "subdomain": sub, "email": admin,
                      "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": admin, "password": "pw123456"}).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    client.put("/api/v1/payroll/settings",
               json={"pf_enabled": True, "esi_enabled": True}, headers=hr)
    basic = client.post("/api/v1/payroll/components",
                        json={"code": "BASIC", "name": "Basic", "wage_basis": "wages"},
                        headers=hr).json()
    return {"sub": sub, "hr": hr, "basic": basic}


def _employee(client, org, name, amount):
    e = client.post("/api/v1/hr/employees", json={"full_name": name},
                    headers=org["hr"]).json()
    _salary(client, org, e["id"], amount)
    return e


def _salary(client, org, emp_id, amount):
    client.put(f"/api/v1/payroll/employees/{emp_id}/salary",
               json={"components": [{"component_id": org["basic"]["id"],
                                     "amount": amount}]}, headers=org["hr"])


def _run(client, org, period):
    r = client.post("/api/v1/payroll/runs", json={"period": period}, headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


def _movement(client, org, period=SEP):
    r = client.get(f"/api/v1/payroll/movement?period={period}", headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


def _bridge(m):
    return {b["code"]: D(b["amount"]) for b in m["bridge"]}


def _assert_closes(m):
    """THE property. The bridge explains the whole change, to the paisa."""
    delta = D(m["current"]["gross"]) - D(m["previous"]["gross"])
    explained = sum((D(b["amount"]) for b in m["bridge"]), start=D("0"))
    assert explained == delta, f"bridge {explained} != delta {delta}"
    assert D(m["unexplained"]) == D("0")


# --- the empty case ---------------------------------------------------------


def test_a_first_payroll_has_nothing_to_compare_with(client, org):
    """Reporting a baseline of zero would call a first payroll infinite growth."""
    _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, SEP)

    m = _movement(client, org)
    assert m["comparable"] is False
    assert m["bridge"] == []
    assert m["previous_period"] == AUG
    assert D(m["current"]["gross"]) == D("30000.00")


# --- one cause at a time ----------------------------------------------------


def test_a_quiet_month_moves_nothing(client, org):
    _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)
    _run(client, org, SEP)

    m = _movement(client, org)
    assert m["comparable"] is True
    assert D(m["current"]["gross"]) == D(m["previous"]["gross"])
    assert _bridge(m) == {} or all(v == D("0") for v in _bridge(m).values())
    _assert_closes(m)


def test_a_joiner_is_attributed_to_new_employees(client, org):
    _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)
    _employee(client, org, "Priya Shah", "40000.00")
    _run(client, org, SEP)

    m = _movement(client, org)
    b = _bridge(m)
    assert b["joiners"] == D("40000.00")
    assert m["current"]["employees"] - m["previous"]["employees"] == 1
    assert next(x for x in m["bridge"] if x["code"] == "joiners")["count"] == 1
    _assert_closes(m)


def test_an_exit_is_attributed_to_exits_and_is_negative(client, org):
    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    _employee(client, org, "Priya Shah", "40000.00")
    _run(client, org, AUG)

    client.patch(f"/api/v1/hr/employees/{ravi['id']}", json={"status": "exited"},
                 headers=org["hr"])
    _run(client, org, SEP)

    b = _bridge(_movement(client, org))
    assert b["leavers"] == D("-30000.00")
    _assert_closes(_movement(client, org))


def test_a_raise_is_attributed_to_salary_revisions(client, org):
    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)
    _salary(client, org, ravi["id"], "36000.00")
    _run(client, org, SEP)

    b = _bridge(_movement(client, org))
    assert b["revision"] == D("6000.00")
    assert "attendance" not in b or b["attendance"] == D("0")
    _assert_closes(_movement(client, org))


def test_unpaid_days_are_attributed_to_attendance_not_to_a_raise(client, org):
    """The split that matters: nobody's rate changed, so `revision` must stay
    at zero even though their pay fell."""
    _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)
    run = _run(client, org, SEP)
    slip = run["payslips"][0]
    client.patch(f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
                 json={"lop_days": 5}, headers=org["hr"])

    m = _movement(client, org)
    b = _bridge(m)
    assert b["attendance"] < D("0")
    assert b.get("revision", D("0")) == D("0"), "no rate changed"
    _assert_closes(m)


def test_a_bonus_is_attributed_to_adjustments(client, org):
    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)

    row = client.post("/api/v1/payroll/inputs", headers=org["hr"], json={
        "employee_id": ravi["id"], "period": SEP, "kind": "earning",
        "code": "BONUS", "name": "Festival bonus", "amount": "9000.00"}).json()
    client.post("/api/v1/payroll/inputs/approve", json={"ids": [row["id"]]},
                headers=org["hr"])
    _run(client, org, SEP)

    b = _bridge(_movement(client, org))
    assert b["entered"] == D("9000.00")
    _assert_closes(_movement(client, org))


def test_overtime_is_attributed_to_overtime(client, org):
    from datetime import date, datetime

    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.modules.payroll.workforce import WorkFact

    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)

    s = get_settings()
    cid = pyjwt.decode(org["hr"]["Authorization"].split()[1], s.jwt_secret,
                       algorithms=[s.jwt_alg])["cid"]
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})
    for d in (1, 2, 3, 4):
        db.add(WorkFact(company_id=uuid.UUID(cid), employee_id=uuid.UUID(ravi["id"]),
                        day=date(2026, 9, d), status="worked", hours_worked=D("12"),
                        overtime_hours=D("4"),
                        approved_at=datetime.now(tz=None).astimezone()))
    db.commit()
    db.close()
    _run(client, org, SEP)

    m = _movement(client, org)
    b = _bridge(m)
    assert b["overtime"] > D("0")
    assert b.get("revision", D("0")) == D("0"), "overtime is not a raise"
    _assert_closes(m)


# --- everything at once -----------------------------------------------------


def test_the_bridge_closes_with_every_cause_at_once(client, org):
    """A raise, an absence, an exit, a joiner and a bonus in one month. This is
    the case where a decomposition that isn't exact by construction drifts."""
    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    priya = _employee(client, org, "Priya Shah", "45000.00")
    amit = _employee(client, org, "Amit Patil", "22000.00")
    _run(client, org, AUG)

    _salary(client, org, ravi["id"], "33000.00")                 # raise
    client.patch(f"/api/v1/hr/employees/{amit['id']}", json={"status": "exited"},
                 headers=org["hr"])                              # exit
    _employee(client, org, "Sunita Rao", "28000.00")             # joiner

    run = _run(client, org, SEP)
    slip = next(p for p in run["payslips"] if p["employee_id"] == priya["id"])
    client.patch(f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
                 json={"lop_days": 3}, headers=org["hr"])        # absence

    row = client.post("/api/v1/payroll/inputs", headers=org["hr"], json={
        "employee_id": ravi["id"], "period": SEP, "kind": "earning",
        "code": "BONUS", "name": "Bonus", "amount": "5000.00"}).json()
    client.post("/api/v1/payroll/inputs/approve", json={"ids": [row["id"]]},
                headers=org["hr"])
    _run(client, org, SEP)

    m = _movement(client, org)
    b = _bridge(m)
    assert b["joiners"] == D("28000.00")
    assert b["leavers"] == D("-22000.00")
    assert b["revision"] == D("3000.00")
    assert b["entered"] == D("5000.00")
    assert b["attendance"] < D("0")
    _assert_closes(m)


def test_a_raise_and_an_absence_for_the_same_person_stay_separate(client, org):
    """Both moved at once. Attributing the whole delta to either would be
    wrong, and the two halves must still sum to it."""
    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)
    _salary(client, org, ravi["id"], "60000.00")
    run = _run(client, org, SEP)
    slip = run["payslips"][0]
    client.patch(f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
                 json={"lop_days": 11}, headers=org["hr"])

    m = _movement(client, org)
    b = _bridge(m)
    assert b["revision"] > D("0"), "the raise is real even though pay fell"
    assert b["attendance"] < D("0")
    _assert_closes(m)


# --- the totals table --------------------------------------------------------


def test_statutory_lines_are_compared_too(client, org):
    _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)
    _employee(client, org, "Priya Shah", "40000.00")
    _run(client, org, SEP)

    m = _movement(client, org)
    codes = {ln["code"] for ln in m["lines"]}
    assert {"gross", "pf", "esi", "pt", "deductions", "employer_cost", "net"} <= codes
    for ln in m["lines"]:
        assert D(ln["change"]) == D(ln["current"]) - D(ln["previous"]), ln["code"]

    pf = next(ln for ln in m["lines"] if ln["code"] == "pf")
    assert D(pf["current"]) > D(pf["previous"]), "a second employee raises PF"


def test_movement_needs_no_finalized_run(client, org):
    """Finance asks why the number moved while the month is still open, which
    is exactly when the answer is most useful."""
    _employee(client, org, "Ravi Kumar", "30000.00")
    _run(client, org, AUG)
    _run(client, org, SEP)
    assert _movement(client, org)["comparable"] is True


def test_only_payroll_can_read_movement(client, org):
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

    assert client.get(f"/api/v1/payroll/movement?period={SEP}",
                      headers=mgr).status_code == 403


def test_recomputing_removes_a_payslip_for_somebody_no_longer_eligible(client, org):
    """Regression. build_run updated and created payslips but never removed
    one, so exiting somebody mid-draft left their payslip in place at full pay
    and counted it toward the totals — a leaver kept being paid, and the
    movement bridge showed no exit because they never left it."""
    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    _employee(client, org, "Priya Shah", "40000.00")
    before = _run(client, org, SEP)
    assert len(before["payslips"]) == 2
    assert D(before["gross_total"]) == D("70000.00")

    client.patch(f"/api/v1/hr/employees/{ravi['id']}", json={"status": "exited"},
                 headers=org["hr"])
    after = _run(client, org, SEP)

    assert [p["employee_name"] for p in after["payslips"]] == ["Priya Shah"]
    assert D(after["gross_total"]) == D("40000.00")


def test_an_exit_after_a_draft_run_still_shows_in_the_bridge(client, org):
    """The end-to-end version: the missing exit line was the symptom that
    surfaced the bug above."""
    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    _employee(client, org, "Priya Shah", "40000.00")
    _run(client, org, AUG)
    _run(client, org, SEP)          # a draft exists before the exit

    client.patch(f"/api/v1/hr/employees/{ravi['id']}", json={"status": "exited"},
                 headers=org["hr"])
    _run(client, org, SEP)          # recompute

    m = _movement(client, org)
    assert _bridge(m)["leavers"] == D("-30000.00")
    _assert_closes(m)


def test_someone_blocked_by_validation_loses_their_payslip_too(client, org):
    """Blocking means excluded from the run. A payslip left behind from an
    earlier pass would pay somebody the engine has decided it cannot compute."""
    ravi = _employee(client, org, "Ravi Kumar", "30000.00")
    first = _run(client, org, SEP)
    assert len(first["payslips"]) == 1

    # Strip their salary structure entirely.
    client.put(f"/api/v1/payroll/employees/{ravi['id']}/salary",
               json={"components": []}, headers=org["hr"])
    after = _run(client, org, SEP)

    assert after["payslips"] == []
    assert D(after["gross_total"]) == D("0")
