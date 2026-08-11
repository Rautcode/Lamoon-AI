"""Readiness — can payroll run at all?

The distinction these defend: readiness is per COMPANY, validation is per
EMPLOYEE. "Nobody has a salary structure" belongs here; "Meera has no salary
structure" belongs there. And a check the system cannot evaluate must never
report as passing.
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

PERIOD = "2026-09-01"


@pytest.fixture
def bare(client):
    """A company with nothing set up. The genuine starting point."""
    sub = f"rdy-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Ready Co", "subdomain": sub, "email": admin,
                      "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": admin, "password": "pw123456"}).json()
    return {"sub": sub, "hr": {"Authorization": f"Bearer {tok['access_token']}"}}


def _readiness(client, org):
    r = client.get(f"/api/v1/payroll/readiness?period={PERIOD}", headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


def _check(report, code):
    return next(c for c in report["checks"] if c["code"] == code)


def _ready(client, org):
    """Take a bare company all the way to configured."""
    client.put("/api/v1/payroll/settings",
               json={"pf_enabled": True, "esi_enabled": True}, headers=org["hr"])
    basic = client.post("/api/v1/payroll/components",
                        json={"code": "BASIC", "name": "Basic", "wage_basis": "wages"},
                        headers=org["hr"]).json()
    emp = client.post("/api/v1/hr/employees",
                      json={"full_name": "Ravi Kumar", "date_of_birth": "1990-01-01",
                            "pf_first_joined_on": "2012-04-01"},
                      headers=org["hr"]).json()
    client.put(f"/api/v1/payroll/employees/{emp['id']}/salary",
               json={"components": [{"component_id": basic["id"], "amount": "30000.00"}]},
               headers=org["hr"])
    client.put("/api/v1/payroll/pt-slabs", headers=org["hr"],
               json=[{"up_to": None, "amount": "200"}])
    client.post("/api/v1/payroll/inputs/rebuild", json={"period": PERIOD},
                headers=org["hr"])
    return {"employee": emp, "basic": basic}


# --- the empty case ---------------------------------------------------------


def test_a_brand_new_company_is_blocked_not_ready(client, bare):
    """100% with nobody to pay would be the classic false green."""
    r = _readiness(client, bare)
    assert r["blocking"] >= 2
    assert _check(r, "employees")["status"] == "blocking"
    assert _check(r, "pay_components")["status"] == "blocking"
    assert r["percent"] < 100


def test_headcount_alone_does_not_make_it_ready(client, bare):
    client.post("/api/v1/hr/employees", json={"full_name": "Ravi Kumar"},
                headers=bare["hr"])
    r = _readiness(client, bare)
    assert _check(r, "employees")["status"] == "ok"
    assert _check(r, "pay_components")["status"] == "blocking"
    assert r["blocking"] >= 1


# --- a configured company ---------------------------------------------------


def test_a_configured_company_reaches_a_hundred(client, bare):
    _ready(client, bare)
    r = _readiness(client, bare)
    assert r["blocking"] == 0
    assert r["warnings"] == 0
    assert r["percent"] == 100
    assert _check(r, "salary_coverage")["status"] == "ok"
    assert _check(r, "work_calendar")["count"] > 0


def test_partial_salary_coverage_warns_and_names_the_gap(client, bare):
    """A company-level gap, distinct from one person's validation error."""
    _ready(client, bare)
    client.post("/api/v1/hr/employees", json={"full_name": "Meera Iyer"},
                headers=bare["hr"])

    r = _readiness(client, bare)
    coverage = _check(r, "salary_coverage")
    assert coverage["status"] == "warning"
    assert coverage["count"] == 1
    assert "excluded from the run" in coverage["detail"]
    assert r["blocking"] == 0, "a partial gap does not block the whole run"


def test_nobody_having_a_structure_blocks(client, bare):
    """The company-level version of the same fact is blocking, not a warning."""
    client.post("/api/v1/payroll/components",
                json={"code": "BASIC", "name": "Basic", "wage_basis": "wages"},
                headers=bare["hr"])
    client.post("/api/v1/hr/employees", json={"full_name": "Ravi Kumar"},
                headers=bare["hr"])
    r = _readiness(client, bare)
    assert _check(r, "salary_coverage")["status"] == "blocking"


# --- the rule that matters most ---------------------------------------------


def test_an_unanswerable_check_is_unknown_never_passing(client, bare):
    """No PT schedule is correct in Delhi and identical to having forgotten
    one. Reporting a tick would be a lie of exactly the kind payroll cannot
    afford."""
    _ready(client, bare)
    ok = _check(_readiness(client, bare), "professional_tax")
    assert ok["status"] == "ok" and ok["count"] == 1

    client.put("/api/v1/payroll/pt-slabs", json=[], headers=bare["hr"])
    unknown = _check(_readiness(client, bare), "professional_tax")
    assert unknown["status"] == "unknown"
    assert "do not levy" in unknown["detail"]


def test_unknown_checks_are_excluded_from_the_percentage(client, bare):
    """Not counted as a failure either — an unanswerable question is not a
    problem, but it is not a pass."""
    _ready(client, bare)
    client.put("/api/v1/payroll/pt-slabs", json=[], headers=bare["hr"])

    r = _readiness(client, bare)
    assert r["unknown"] == 1
    assert r["percent"] == 100, "the remaining checks all pass"
    assert r["blocking"] == 0


def test_an_all_zero_work_week_is_refused_before_it_reaches_readiness(client, bare):
    """The work-week pattern validator already stops this, so readiness never
    has to consider a company that works no days at all by policy."""
    assert client.put("/api/v1/calendar/work-week", json={"working_days": "0000000"},
                      headers=bare["hr"]).status_code == 422


def test_a_month_of_declared_holidays_blocks(client, bare):
    """The genuine zero-working-day case: every day shut. Pathological, but it
    means nobody can be prorated, so it blocks rather than paying by accident."""
    from datetime import date, timedelta

    _ready(client, bare)
    day = date(2026, 9, 1)
    while day.month == 9:
        client.post("/api/v1/calendar/holidays",
                    json={"day": str(day), "name": "Shutdown"}, headers=bare["hr"])
        day += timedelta(days=1)

    r = _readiness(client, bare)
    assert _check(r, "work_calendar")["status"] == "blocking"
    assert r["blocking"] == 1
    # The percentage never travels alone — a caller can always lead with the
    # worse of the two.
    assert set(r) >= {"percent", "blocking", "warnings", "unknown"}
    assert r["percent"] < 100


# --- configuration that would silently compute a wrong number ---------------


def test_pf_on_with_nothing_counting_as_wages_blocks(client, bare):
    """PF would compute on zero and nobody would notice until the ECR."""
    client.put("/api/v1/payroll/settings", json={"pf_enabled": True}, headers=bare["hr"])
    client.post("/api/v1/payroll/components",
                json={"code": "HRA", "name": "HRA", "wage_basis": "excluded"},
                headers=bare["hr"])
    client.post("/api/v1/hr/employees", json={"full_name": "Ravi"}, headers=bare["hr"])

    pf = _check(_readiness(client, bare), "provident_fund")
    assert pf["status"] == "blocking"
    assert "compute on zero" in pf["detail"]


def test_pf_off_is_not_a_problem(client, bare):
    client.put("/api/v1/payroll/settings", json={"pf_enabled": False}, headers=bare["hr"])
    assert _check(_readiness(client, bare), "provident_fund")["status"] == "ok"


def test_missing_statutory_dates_warn_because_pension_is_assumed(client, bare):
    _ready(client, bare)
    client.post("/api/v1/hr/employees", json={"full_name": "No Dates"},
                headers=bare["hr"])
    check = _check(_readiness(client, bare), "statutory_identity")
    assert check["status"] == "warning"
    assert "assumed" in check["detail"]


def test_unassigned_employees_warn_only_once_there_are_two_jurisdictions(client, bare):
    """With one establishment there is nothing to get wrong, so no noise."""
    _ready(client, bare)
    assert all(c["code"] != "jurisdiction" for c in _readiness(client, bare)["checks"])

    client.post("/api/v1/payroll/establishments",
                json={"name": "Mumbai", "state_code": "MH"}, headers=bare["hr"])
    client.post("/api/v1/payroll/establishments",
                json={"name": "Delhi", "state_code": "DL"}, headers=bare["hr"])

    j = _check(_readiness(client, bare), "jurisdiction")
    assert j["status"] == "warning"
    assert "wrong state" in j["detail"]


# --- readiness is not validation --------------------------------------------


def test_readiness_and_validation_answer_different_questions(client, bare):
    """One employee missing a structure is a company-level WARNING and a
    person-level BLOCKING finding at the same time. Both are correct."""
    _ready(client, bare)
    client.post("/api/v1/hr/employees", json={"full_name": "Meera Iyer"},
                headers=bare["hr"])

    r = _readiness(client, bare)
    v = client.get(f"/api/v1/payroll/validation?period={PERIOD}",
                   headers=bare["hr"]).json()

    assert _check(r, "salary_coverage")["status"] == "warning"
    assert r["blocking"] == 0
    assert v["blocking"] == 1
    assert any(f["employee_name"] == "Meera Iyer" for f in v["findings"])


def test_readiness_needs_no_payroll_run(client, bare):
    """It answers whether a run is possible, so it cannot require one."""
    _ready(client, bare)
    assert client.get("/api/v1/payroll/runs", headers=bare["hr"]).json() == []
    assert _readiness(client, bare)["percent"] == 100


def test_only_payroll_can_read_readiness(client, bare):
    """It exposes coverage of salary structures, which is payroll data."""
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import hash_password
    from app.modules.auth.models import User

    s = get_settings()
    cid = pyjwt.decode(bare["hr"]["Authorization"].split()[1], s.jwt_secret,
                       algorithms=[s.jwt_alg])["cid"]
    email = f"mgr-{uuid.uuid4().hex[:6]}@{bare['sub']}.test"
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})
    db.add(User(company_id=uuid.UUID(cid), email=email, role="manager",
                password_hash=hash_password("pw123456")))
    db.commit()
    db.close()
    tok = client.post("/api/v1/auth/login",
                      json={"company": bare["sub"], "email": email,
                            "password": "pw123456"}).json()
    mgr = {"Authorization": f"Bearer {tok['access_token']}"}

    assert client.get(f"/api/v1/payroll/readiness?period={PERIOD}",
                      headers=mgr).status_code == 403
