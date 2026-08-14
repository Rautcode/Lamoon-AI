"""Attendance → work facts. The join that closes the biggest correctness hole.

Payroll has never read attendance. LOP came only from approved unpaid leave, so
**an employee who never punched in for a month and filed no leave was paid in
full.** The attendance module was live the whole time, producing data payroll
silently ignored.

The rule that makes the fix safe rather than merely present:

    A MISSING PUNCH NEVER BECOMES LOP.

It becomes an exception a human resolves — regularise, mark leave, or confirm
as unpaid. Silently docking somebody because a biometric reader failed is a
worse error than paying a day too many, and it is the error nobody notices
until payday.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.modules.attendance.bridge import derive

API = "/api/v1"


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


endpoint = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


# --- what a day state becomes, pure ------------------------------------------


def test_a_worked_day_becomes_a_worked_fact():
    assert derive("present").status == "worked"


def test_paid_leave_becomes_a_leave_fact_that_is_pre_approved():
    """Somebody already approved the leave. Asking a manager to approve it
    again is the product forgetting what it was told."""
    got = derive("paid_leave")
    assert got.status == "leave" and got.auto_approve is True


def test_unpaid_leave_becomes_a_leave_fact_too():
    """Unpaid leave is still EXPLAINED absence — payroll gets the LOP from the
    leave record, not from this fact."""
    assert derive("unpaid_leave").status == "leave"


def test_a_holiday_produces_no_fact_at_all():
    """The calendar already accounts for it. A fact would be a second opinion
    about the same day."""
    assert derive("holiday") is None
    assert derive("weekly_off") is None


def test_an_unexplained_absence_becomes_an_UNAPPROVED_fact():
    """The whole point. It exists so somebody must look at it, and it is
    unapproved so payroll cannot act on it."""
    got = derive("absent")
    assert got.status == "absent"
    assert got.auto_approve is False, "an absence nobody explained is not signed off"


def test_a_missing_punch_is_not_an_absence():
    """Somebody worked and the record is incomplete. Treating it as absence
    docks a day's pay for a failed reader."""
    got = derive("missing_punch")
    assert got.status == "worked"
    assert got.auto_approve is False, "the hours are unknown, so a human confirms them"


# --- endpoints ----------------------------------------------------------------


@pytest.fixture
def org(client):
    sub = f"br-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Bridge Co", "subdomain": sub,
        "email": admin, "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": sub, "email": admin, "password": "pw123456",
    }).json()["access_token"]
    hr = {"Authorization": f"Bearer {tok}"}

    basic = client.post(f"{API}/payroll/components", json={
        "code": "BASIC", "name": "Basic", "kind": "earning",
        "wage_basis": "wages", "esi_wage": True, "taxable": True, "sequence": 10,
    }, headers=hr).json()
    emp = client.post(f"{API}/hr/employees", json={
        "full_name": "Asha Rao", "joined_on": "2026-01-01",
        "date_of_birth": "1992-05-05", "pf_first_joined_on": "2013-01-01",
    }, headers=hr).json()
    client.put(f"{API}/payroll/employees/{emp['id']}/salary", json={
        "components": [{"component_id": basic["id"], "amount": "44000"}],
    }, headers=hr)
    return {"hr": hr, "sub": sub, "employee": emp}


def run_bridge(client, org, period="2026-08-01"):
    return client.post(f"{API}/workforce/facts/derive?period={period}", headers=org["hr"])


@endpoint
def test_a_month_of_no_punches_produces_findings_and_deducts_nothing(client, org):
    """The headline. Before this bridge existed, this employee was paid in
    full for a month they never appeared."""
    r = run_bridge(client, org)
    assert r.status_code == 200, r.text
    assert r.json()["absent"] > 0

    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    slip = run["payslips"][0]
    assert slip["lop_days"] == 0, "an unexplained absence is not yet a deduction"

    findings = client.get(f"{API}/payroll/validation?period=2026-08-01",
                          headers=org["hr"]).json()
    codes = [f["code"] for f in findings["findings"]]
    assert "attendance_unexplained" in codes


@endpoint
def test_confirming_the_absence_is_what_makes_it_cost_money(client, org):
    """A human decides. That is the entire difference between this and
    silently docking somebody."""
    run_bridge(client, org)
    facts = client.get(
        f"{API}/workforce/facts?employee_id={org['employee']['id']}"
        f"&from=2026-08-01&to=2026-08-31", headers=org["hr"],
    ).json()
    absences = [f["id"] for f in facts if f["status"] == "absent"]
    assert absences

    client.post(f"{API}/workforce/facts/approve", json={"ids": absences[:2]},
                headers=org["hr"])
    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    assert run["payslips"][0]["lop_days"] == 2


@endpoint
def test_running_the_bridge_twice_changes_nothing(client, org):
    """It runs nightly. A second pass must not double anybody's days."""
    first = run_bridge(client, org).json()
    second = run_bridge(client, org).json()
    assert first["absent"] == second["absent"]

    facts = client.get(
        f"{API}/workforce/facts?employee_id={org['employee']['id']}"
        f"&from=2026-08-01&to=2026-08-31", headers=org["hr"],
    ).json()
    days = [f["day"] for f in facts]
    assert len(days) == len(set(days)), "one fact per day, however often it runs"


@endpoint
def test_a_hand_entered_fact_is_never_overwritten(client, org):
    """Somebody typed it. Regenerating must not destroy a human's knowledge —
    the same asymmetry the payroll input ledger already has."""
    client.post(f"{API}/workforce/facts", json={
        "employee_id": org["employee"]["id"], "day": "2026-08-05",
        "status": "worked", "hours_worked": "8", "overtime_hours": "3",
        "site": "Pune Project A", "source": "manual",
    }, headers=org["hr"])

    run_bridge(client, org)

    facts = client.get(
        f"{API}/workforce/facts?employee_id={org['employee']['id']}"
        f"&from=2026-08-05&to=2026-08-05", headers=org["hr"],
    ).json()
    assert len(facts) == 1
    assert facts[0]["source"] == "manual"
    assert facts[0]["site"] == "Pune Project A"
    assert float(facts[0]["overtime_hours"]) == 3.0


@endpoint
def test_approved_leave_produces_an_approved_fact_not_an_absence(client, org):
    """Leave is explained. Nobody should be asked to approve it twice."""
    lt = client.post(f"{API}/leave/types", json={
        "name": "Annual", "annual_quota": 12, "paid": True,
    }, headers=org["hr"]).json()
    req = client.post(f"{API}/leave/requests", json={
        "employee_id": org["employee"]["id"], "leave_type_id": lt["id"],
        "start_date": "2026-08-05", "end_date": "2026-08-05",
    }, headers=org["hr"]).json()
    client.post(f"{API}/leave/requests/{req['id']}/approve", headers=org["hr"])

    run_bridge(client, org)
    facts = client.get(
        f"{API}/workforce/facts?employee_id={org['employee']['id']}"
        f"&from=2026-08-05&to=2026-08-05", headers=org["hr"],
    ).json()
    assert facts[0]["status"] == "leave"
    assert facts[0]["approved_at"] is not None


@endpoint
def test_holidays_and_weekends_produce_nothing(client, org):
    """The calendar already accounts for them; a fact would be a second
    opinion about the same day."""
    run_bridge(client, org)
    facts = client.get(
        f"{API}/workforce/facts?employee_id={org['employee']['id']}"
        f"&from=2026-08-01&to=2026-08-31", headers=org["hr"],
    ).json()
    # 2026-08-01 is a Saturday, 08-02 a Sunday.
    days = {f["day"] for f in facts}
    assert "2026-08-01" not in days and "2026-08-02" not in days


@endpoint
def test_the_bridge_does_not_reach_into_a_finalized_month(client, org):
    """Deriving facts for a closed month cannot change the pay and would only
    create work nobody can act on."""
    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    client.post(f"{API}/payroll/runs/{run['id']}/finalize", headers=org["hr"])

    r = run_bridge(client, org)
    assert r.status_code == 409
    assert "finalized" in r.json()["detail"]
