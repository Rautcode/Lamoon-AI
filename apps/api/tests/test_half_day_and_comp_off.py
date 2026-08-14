"""Half-days and compensatory off — the last two R2 gate scenarios.

Both exist to prove the same thing from opposite directions: that a day is not
always a whole number, and that the modules agree about it. A half-day of
unpaid leave must be half a day of loss of pay in payroll, and a Sunday worked
must become a day owed back — an attendance fact, measured against a calendar
rule, becoming a leave credit.

If payroll rounded either of those, the feature would look present and be
wrong, which is worse than absent.
"""
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.modules.leave.comp_off import credit_for_hours

API = "/api/v1"


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


endpoint = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


# --- what a day off worked is worth, pure ------------------------------------


def test_a_full_day_worked_earns_a_full_day_back():
    assert credit_for_hours(D("8")) == D("1.0")


def test_a_half_day_worked_earns_half_a_day_back():
    assert credit_for_hours(D("4")) == D("0.5")


def test_a_short_call_out_earns_nothing():
    """Two hours on a Sunday is overtime, not a day owed back. Crediting it
    would hand out a day of leave for a phone call."""
    assert credit_for_hours(D("2")) == D("0")


# --- endpoints ----------------------------------------------------------------


@pytest.fixture
def org(client):
    sub = f"hd-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Half Co", "subdomain": sub,
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

    unpaid = client.post(f"{API}/leave/types", json={
        "name": "Unpaid", "annual_quota": 30, "paid": False,
    }, headers=hr).json()
    return {"hr": hr, "sub": sub, "employee": emp, "unpaid": unpaid}


def file_leave(client, org, *, start, end, half_day=False, type_id=None):
    return client.post(f"{API}/leave/requests", json={
        "employee_id": org["employee"]["id"],
        "leave_type_id": type_id or org["unpaid"]["id"],
        "start_date": start, "end_date": end, "half_day": half_day,
    }, headers=org["hr"])


@endpoint
def test_a_half_day_is_billed_as_half_a_day(client, org):
    r = file_leave(client, org, start="2026-08-05", end="2026-08-05", half_day=True)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 0.5


@endpoint
def test_a_half_day_over_a_range_is_refused(client, org):
    """Half of a five-day absence is not a thing anybody means, and accepting
    it would bill 2.5 days for a week away."""
    r = file_leave(client, org, start="2026-08-03", end="2026-08-07", half_day=True)
    assert r.status_code == 422
    assert "same day" in r.json()["detail"]


@endpoint
def test_half_a_day_unpaid_is_half_a_day_of_lop(client, org):
    """The whole reason half-days needed a payroll migration. If this rounds,
    the feature is present and wrong."""
    req = file_leave(client, org, start="2026-08-05", end="2026-08-05",
                     half_day=True).json()
    client.post(f"{API}/leave/requests/{req['id']}/approve", headers=org["hr"])

    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    slip = run["payslips"][0]
    assert slip["lop_days"] == 0.5
    assert slip["paid_days"] == slip["working_days"] - 0.5


@endpoint
def test_half_a_day_costs_half_of_a_full_days_pay(client, org):
    """0.5 days of LOP must cost exactly half what 1.0 day costs — the
    arithmetic somebody will check by hand on a payslip."""
    half = file_leave(client, org, start="2026-08-05", end="2026-08-05",
                      half_day=True).json()
    client.post(f"{API}/leave/requests/{half['id']}/approve", headers=org["hr"])
    with_half = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                            headers=org["hr"]).json()["payslips"][0]

    full = file_leave(client, org, start="2026-08-06", end="2026-08-06").json()
    client.post(f"{API}/leave/requests/{full['id']}/approve", headers=org["hr"])
    with_both = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                            headers=org["hr"]).json()["payslips"][0]

    assert with_both["lop_days"] == 1.5
    lost_to_half = D(with_half["gross"]) - D(with_both["gross"])  # the extra full day
    lost_to_full = D("44000") - D(with_half["gross"])  # the original half
    assert lost_to_half == lost_to_full * 2


@endpoint
def test_working_a_weekly_off_earns_a_day_back(client, org):
    """An attendance fact, measured against a calendar rule, becoming a leave
    credit. No single module can decide this."""
    comp = client.post(f"{API}/leave/types", json={
        "name": "Comp off", "annual_quota": 0, "paid": True, "comp_off": True,
    }, headers=org["hr"]).json()

    # A Sunday inside the current leave year.
    sunday = date(2026, 8, 9)
    assert sunday.weekday() == 6
    fact = client.post(f"{API}/workforce/facts", json={
        "employee_id": org["employee"]["id"], "day": sunday.isoformat(),
        "status": "worked", "hours_worked": "8",
    }, headers=org["hr"]).json()
    client.post(f"{API}/workforce/facts/approve", json={"ids": [fact["id"]]},
                headers=org["hr"])

    balances = client.get(f"{API}/leave/balances/{org['employee']['id']}",
                          headers=org["hr"]).json()
    credit = next(b for b in balances if b["leave_type_id"] == comp["id"])
    assert credit["allocated"] == 1.0
    assert credit["remaining"] == 1.0


@endpoint
def test_unapproved_work_earns_no_comp_off(client, org):
    """Unapproved work earns nothing — that is what approval is for."""
    comp = client.post(f"{API}/leave/types", json={
        "name": "Comp off", "annual_quota": 0, "paid": True, "comp_off": True,
    }, headers=org["hr"]).json()

    client.post(f"{API}/workforce/facts", json={
        "employee_id": org["employee"]["id"], "day": "2026-08-09",
        "status": "worked", "hours_worked": "8",
    }, headers=org["hr"])

    balances = client.get(f"{API}/leave/balances/{org['employee']['id']}",
                          headers=org["hr"]).json()
    credit = next(b for b in balances if b["leave_type_id"] == comp["id"])
    assert credit["allocated"] == 0


@endpoint
def test_working_an_ordinary_day_earns_no_comp_off(client, org):
    """An ordinary working day earns pay, not a day back."""
    comp = client.post(f"{API}/leave/types", json={
        "name": "Comp off", "annual_quota": 0, "paid": True, "comp_off": True,
    }, headers=org["hr"]).json()

    wednesday = date(2026, 8, 5)
    assert wednesday.weekday() == 2
    fact = client.post(f"{API}/workforce/facts", json={
        "employee_id": org["employee"]["id"], "day": wednesday.isoformat(),
        "status": "worked", "hours_worked": "8",
    }, headers=org["hr"]).json()
    client.post(f"{API}/workforce/facts/approve", json={"ids": [fact["id"]]},
                headers=org["hr"])

    balances = client.get(f"{API}/leave/balances/{org['employee']['id']}",
                          headers=org["hr"]).json()
    credit = next(b for b in balances if b["leave_type_id"] == comp["id"])
    assert credit["allocated"] == 0


@endpoint
def test_comp_off_is_credited_once_however_often_it_is_asked_for(client, org):
    """A work fact is the unit, so re-reading cannot pay somebody twice for one
    Sunday."""
    comp = client.post(f"{API}/leave/types", json={
        "name": "Comp off", "annual_quota": 0, "paid": True, "comp_off": True,
    }, headers=org["hr"]).json()
    fact = client.post(f"{API}/workforce/facts", json={
        "employee_id": org["employee"]["id"], "day": "2026-08-09",
        "status": "worked", "hours_worked": "8",
    }, headers=org["hr"]).json()
    client.post(f"{API}/workforce/facts/approve", json={"ids": [fact["id"]]},
                headers=org["hr"])

    seen = set()
    for _ in range(3):
        balances = client.get(f"{API}/leave/balances/{org['employee']['id']}",
                              headers=org["hr"]).json()
        seen.add(next(b for b in balances if b["leave_type_id"] == comp["id"])["allocated"])
    assert seen == {1.0}


@endpoint
def test_a_joiner_gets_a_prorated_entitlement(client, org):
    """R2 gate: leave entitlement pro-rated on joining."""
    late = client.post(f"{API}/hr/employees", json={
        "full_name": "Late Joiner", "joined_on": "2026-07-01",
        "date_of_birth": "1992-05-05", "pf_first_joined_on": "2013-01-01",
    }, headers=org["hr"]).json()

    annual = client.post(f"{API}/leave/types", json={
        "name": "Annual", "annual_quota": 12, "paid": True,
    }, headers=org["hr"]).json()
    client.post(f"{API}/leave/policies", json={
        "leave_type_id": annual["id"], "scope_type": "company",
        "annual_days": "12", "accrual_method": "annual",
        "prorate_on_joining": True, "prorate_on_exit": True,
        "effective_from": "2026-01-01",
    }, headers=org["hr"])

    balances = client.get(f"{API}/leave/balances/{late['id']}",
                          headers=org["hr"]).json()
    got = next(b for b in balances if b["leave_type_id"] == annual["id"])
    assert got["allocated"] == 6.0, "half a year served, half the quota"


@endpoint
def test_a_leaver_gets_a_prorated_entitlement(client, org):
    """R2 gate: leave stops accruing at the last working day, which is what
    F&F settles against."""
    leaver = client.post(f"{API}/hr/employees", json={
        "full_name": "Early Leaver", "joined_on": "2020-01-01",
        "date_of_birth": "1992-05-05", "pf_first_joined_on": "2013-01-01",
    }, headers=org["hr"]).json()
    client.patch(f"{API}/hr/employees/{leaver['id']}",
                 json={"status": "exited", "exited_on": "2026-06-30"},
                 headers=org["hr"])

    annual = client.post(f"{API}/leave/types", json={
        "name": "Annual", "annual_quota": 12, "paid": True,
    }, headers=org["hr"]).json()
    client.post(f"{API}/leave/policies", json={
        "leave_type_id": annual["id"], "scope_type": "company",
        "annual_days": "12", "accrual_method": "annual",
        "prorate_on_joining": True, "prorate_on_exit": True,
        "effective_from": "2026-01-01",
    }, headers=org["hr"])

    balances = client.get(f"{API}/leave/balances/{leaver['id']}",
                          headers=org["hr"]).json()
    got = next(b for b in balances if b["leave_type_id"] == annual["id"])
    assert got["allocated"] == 6.0
