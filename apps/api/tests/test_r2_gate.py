"""The R2 gate. Nine scenarios, end to end, on one tenant.

This file IS the release gate from `tasks/plan.md`. Not "the modules exist" —
this chain works, on a real tenant, with money coming out correct:

    employee joins → establishment → holiday calendar → shift → leave policy
                  → attendance → leave → work facts → payroll → payslip

Every other test in this suite checks one module. This one checks that they
agree. An HRMS whose attendance, leave, calendar and payroll do not truly meet
is what every incumbent already ships; the joins are the product.

If a scenario here fails, the correct response is to fix the chain, not to
weaken the assertion.
"""
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import text

from app.core.db import engine

API = "/api/v1"

# August 2026: 1st is a Saturday, so 21 weekdays. Chosen because it contains a
# clean two-week run with no month-boundary effects to argue about.
PERIOD = "2026-08-01"
SALARY = "42000"


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


gate = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


@pytest.fixture
def co(client):
    """A company with two establishments, two calendars and a leave policy —
    the minimum shape in which all nine scenarios are expressible."""
    sub = f"gate-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Gate Co", "subdomain": sub,
        "email": admin, "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": sub, "email": admin, "password": "pw123456",
    }).json()["access_token"]
    hr = {"Authorization": f"Bearer {tok}"}

    mumbai = client.post(f"{API}/payroll/establishments", json={
        "name": "Mumbai", "state_code": "MH"}, headers=hr).json()
    bengaluru = client.post(f"{API}/payroll/establishments", json={
        "name": "Bengaluru", "state_code": "KA"}, headers=hr).json()

    # Mumbai observes a local holiday Bengaluru does not.
    mh = client.post(f"{API}/calendar/calendars", json={
        "name": "Maharashtra", "working_days": "1111100"}, headers=hr).json()
    client.post(f"{API}/calendar/calendars/{mh['id']}/holidays", json={
        "day": "2026-08-19", "name": "Local holiday"}, headers=hr)
    client.post(f"{API}/calendar/assignments", json={
        "calendar_id": mh["id"], "scope_type": "establishment",
        "scope_id": mumbai["id"], "effective_from": "2026-01-01"}, headers=hr)

    basic = client.post(f"{API}/payroll/components", json={
        "code": "BASIC", "name": "Basic", "kind": "earning",
        "wage_basis": "wages", "esi_wage": True, "taxable": True,
        "sequence": 10}, headers=hr).json()

    annual = client.post(f"{API}/leave/types", json={
        "name": "Annual", "annual_quota": 12, "paid": True}, headers=hr).json()
    unpaid = client.post(f"{API}/leave/types", json={
        "name": "Unpaid", "annual_quota": 30, "paid": False}, headers=hr).json()
    client.post(f"{API}/leave/policies", json={
        "leave_type_id": annual["id"], "scope_type": "company",
        "annual_days": "12", "accrual_method": "annual",
        "prorate_on_joining": True, "prorate_on_exit": True,
        "effective_from": "2026-01-01"}, headers=hr)

    return {
        "hr": hr, "sub": sub, "mumbai": mumbai, "bengaluru": bengaluru,
        "basic": basic["id"], "annual": annual["id"], "unpaid": unpaid["id"],
        "calendar": mh["id"],
    }


def hire(client, co, name, *, est=None, joined="2026-01-01", exited=None):
    body = {
        "full_name": name, "joined_on": joined,
        "date_of_birth": "1992-05-05", "pf_first_joined_on": "2013-01-01",
    }
    if est:
        body["establishment_id"] = est["id"]
    if exited:
        body["exited_on"] = exited
        body["status"] = "exited"
    emp = client.post(f"{API}/hr/employees", json=body, headers=co["hr"]).json()
    client.put(f"{API}/payroll/employees/{emp['id']}/salary", json={
        "components": [{"component_id": co["basic"], "amount": SALARY}],
    }, headers=co["hr"])
    return emp


def run(client, co, period=PERIOD):
    return client.post(f"{API}/payroll/runs", json={"period": period},
                       headers=co["hr"]).json()


def slip_for(run_body, employee_id):
    return next((p for p in run_body["payslips"] if p["employee_id"] == employee_id), None)


# --- 1. two establishments ----------------------------------------------------


@gate
def test_1_two_establishments_have_different_working_days(client, co):
    """Different calendars → different denominators → different pay the moment
    anybody has an unpaid day."""
    mira = hire(client, co, "Mira Mumbai", est=co["mumbai"])
    bala = hire(client, co, "Bala Bengaluru", est=co["bengaluru"])

    body = run(client, co)
    assert slip_for(body, mira["id"])["working_days"] == 20
    assert slip_for(body, bala["id"])["working_days"] == 21


# --- 2. joiner ----------------------------------------------------------------


@gate
def test_2_a_mid_month_joiner_is_prorated_everywhere(client, co):
    """Attendance starts at joining, salary prorates, leave entitlement
    prorates, and PF/ESI follow the reduced pay."""
    joiner = hire(client, co, "Late Joiner", est=co["bengaluru"], joined="2026-08-17")
    body = run(client, co)
    slip = slip_for(body, joiner["id"])

    assert slip["working_days"] == 21
    assert slip["lop_days"] > 0, "days before joining are not owed"
    assert D(slip["gross"]) < D(SALARY), "a part month is not a full month's pay"

    balances = client.get(f"{API}/leave/balances/{joiner['id']}",
                          headers=co["hr"]).json()
    annual = next(b for b in balances if b["leave_type_id"] == co["annual"])
    assert annual["allocated"] < 12, "half a year served is not a full year's leave"


# --- 3. leaver ----------------------------------------------------------------


@gate
def test_3_a_leaver_stops_being_paid_and_stops_accruing(client, co):
    """Exit date, not exit status. Leave stops accruing at the last working
    day, which is what F&F settles against."""
    leaver = hire(client, co, "Early Leaver", est=co["bengaluru"],
                  joined="2020-01-01", exited="2026-06-30")

    body = run(client, co)
    assert slip_for(body, leaver["id"]) is None, "an exited employee is not paid"

    balances = client.get(f"{API}/leave/balances/{leaver['id']}",
                          headers=co["hr"]).json()
    annual = next(b for b in balances if b["leave_type_id"] == co["annual"])
    assert annual["allocated"] == 6.0, "six months served, half the year's leave"


# --- 4. holiday ---------------------------------------------------------------


@gate
def test_4_a_holiday_is_not_an_absence_and_changes_the_denominator(client, co):
    """The two things a holiday must do, and the second is the one products
    forget."""
    mira = hire(client, co, "Mira Mumbai", est=co["mumbai"])
    client.post(f"{API}/workforce/facts/derive?period={PERIOD}", headers=co["hr"])

    facts = client.get(
        f"{API}/workforce/facts?employee_id={mira['id']}&from=2026-08-19&to=2026-08-19",
        headers=co["hr"]).json()
    assert facts == [], "the calendar accounts for it; a fact would be a second opinion"

    body = run(client, co)
    assert slip_for(body, mira["id"])["working_days"] == 20  # 21 less the holiday


# --- 5. weekly off worked -----------------------------------------------------


@gate
def test_5_working_a_weekly_off_earns_premium_pay_and_comp_off(client, co):
    """Both halves. The premium reaches the payslip AND the day is owed back."""
    comp = client.post(f"{API}/leave/types", json={
        "name": "Comp off", "annual_quota": 0, "paid": True, "comp_off": True,
    }, headers=co["hr"]).json()
    worker = hire(client, co, "Sunday Worker", est=co["bengaluru"])

    sunday = "2026-08-09"
    assert date.fromisoformat(sunday).weekday() == 6
    fact = client.post(f"{API}/workforce/facts", json={
        "employee_id": worker["id"], "day": sunday,
        "status": "worked", "hours_worked": "8", "premium_day": True,
    }, headers=co["hr"]).json()
    client.post(f"{API}/workforce/facts/approve", json={"ids": [fact["id"]]},
                headers=co["hr"])

    body = run(client, co)
    slip = slip_for(body, worker["id"])
    codes = [line["code"] for line in slip["breakdown"]["earnings"]]
    assert "PREMIUM" in codes, "working a day off is paid at a premium"
    assert D(slip["gross"]) > D(SALARY)

    balances = client.get(f"{API}/leave/balances/{worker['id']}",
                          headers=co["hr"]).json()
    credit = next(b for b in balances if b["leave_type_id"] == comp["id"])
    assert credit["allocated"] == 1.0, "and the day is owed back"


# --- 6. half day --------------------------------------------------------------


@gate
def test_6_a_half_day_of_unpaid_leave_costs_half_a_day(client, co):
    """If this rounds, the feature is present and wrong."""
    worker = hire(client, co, "Half Dayer", est=co["bengaluru"])
    req = client.post(f"{API}/leave/requests", json={
        "employee_id": worker["id"], "leave_type_id": co["unpaid"],
        "start_date": "2026-08-05", "end_date": "2026-08-05", "half_day": True,
    }, headers=co["hr"]).json()
    client.post(f"{API}/leave/requests/{req['id']}/approve", headers=co["hr"])

    slip = slip_for(run(client, co), worker["id"])
    assert slip["lop_days"] == 0.5
    assert slip["paid_days"] == 20.5


# --- 7. LOP -------------------------------------------------------------------


@gate
def test_7_lop_comes_from_leave_and_confirmed_absence_never_a_missing_punch(client, co):
    """The safety property. An unexplained day is a question until a human
    answers it."""
    worker = hire(client, co, "No Show", est=co["bengaluru"])
    client.post(f"{API}/workforce/facts/derive?period={PERIOD}", headers=co["hr"])

    before = slip_for(run(client, co), worker["id"])
    assert before["lop_days"] == 0, "a missing punch is not yet a deduction"

    findings = client.get(f"{API}/payroll/validation?period={PERIOD}",
                          headers=co["hr"]).json()["findings"]
    assert any(f["code"] == "attendance_unexplained" for f in findings)

    facts = client.get(
        f"{API}/workforce/facts?employee_id={worker['id']}"
        f"&from=2026-08-01&to=2026-08-31", headers=co["hr"]).json()
    absences = [f["id"] for f in facts if f["status"] == "absent"][:3]
    client.post(f"{API}/workforce/facts/approve", json={"ids": absences},
                headers=co["hr"])

    after = slip_for(run(client, co), worker["id"])
    assert after["lop_days"] == 3, "confirming is what makes it cost money"
    assert D(after["gross"]) < D(before["gross"])


# --- 8. overtime --------------------------------------------------------------


@gate
def test_8_approved_overtime_reaches_the_payslip(client, co):
    """Hours × a rule → an amount → a line somebody can point at."""
    worker = hire(client, co, "Overtimer", est=co["bengaluru"])
    fact = client.post(f"{API}/workforce/facts", json={
        "employee_id": worker["id"], "day": "2026-08-05",
        "status": "worked", "hours_worked": "8", "overtime_hours": "6",
    }, headers=co["hr"]).json()

    unapproved = slip_for(run(client, co), worker["id"])
    assert all(x["code"] != "OT" for x in unapproved["breakdown"]["earnings"]), (
        "unapproved overtime is a claim, not a cost"
    )

    client.post(f"{API}/workforce/facts/approve", json={"ids": [fact["id"]]},
                headers=co["hr"])
    approved = slip_for(run(client, co), worker["id"])
    ot = next(x for x in approved["breakdown"]["earnings"] if x["code"] == "OT")
    assert D(ot["amount"]) > 0
    assert D(approved["gross"]) > D(unapproved["gross"])


# --- 9. mid-month salary revision --------------------------------------------


@gate
def test_9_a_mid_month_revision_prorates_both_halves_and_explains_itself(client, co):
    """Neither the old salary nor the new one — both, and the payslip says so."""
    worker = hire(client, co, "Promoted", est=co["bengaluru"])
    client.post(f"{API}/compensation/employees/{worker['id']}/versions", json={
        "effective_from": "2026-08-17", "reason": "promotion",
        "lines": [{"component_id": co["basic"], "amount": "84000"}],
    }, headers=co["hr"])

    slip = slip_for(run(client, co), worker["id"])
    assert D(SALARY) < D(slip["gross"]) < D("84000"), "between, not either"

    inputs = client.get(
        f"{API}/payroll/inputs?employee_id={worker['id']}&period={PERIOD}",
        headers=co["hr"]).json()
    reasons = [i["reason"] for i in inputs if i["source"] == "structure"]
    assert any("changed mid-period" in (r or "") for r in reasons), (
        "a number nobody can explain is a support ticket"
    )


# --- the gate itself ----------------------------------------------------------


@gate
def test_the_whole_chain_holds_for_one_person_at_once(client, co):
    """Every mechanism on one payslip, because they interact.

    Passing the nine scenarios separately is not the same as passing them
    together: LOP, overtime, a premium day and a mid-month raise all divide by
    the same working-day count, and that count comes from the employee's own
    calendar.
    """
    worker = hire(client, co, "Everything", est=co["mumbai"])

    # A raise, unpaid leave, a Sunday worked, and overtime — all in one month.
    client.post(f"{API}/compensation/employees/{worker['id']}/versions", json={
        "effective_from": "2026-08-17", "reason": "revision",
        "lines": [{"component_id": co["basic"], "amount": "63000"}],
    }, headers=co["hr"])
    req = client.post(f"{API}/leave/requests", json={
        "employee_id": worker["id"], "leave_type_id": co["unpaid"],
        "start_date": "2026-08-06", "end_date": "2026-08-06",
    }, headers=co["hr"]).json()
    client.post(f"{API}/leave/requests/{req['id']}/approve", headers=co["hr"])
    for day, ot, premium in (("2026-08-05", "4", False), ("2026-08-09", "0", True)):
        f = client.post(f"{API}/workforce/facts", json={
            "employee_id": worker["id"], "day": day, "status": "worked",
            "hours_worked": "8", "overtime_hours": ot, "premium_day": premium,
        }, headers=co["hr"]).json()
        client.post(f"{API}/workforce/facts/approve", json={"ids": [f["id"]]},
                    headers=co["hr"])

    slip = slip_for(run(client, co), worker["id"])

    # Mumbai's calendar, not the company's.
    assert slip["working_days"] == 20
    # One unpaid day.
    assert slip["lop_days"] == 1
    assert slip["paid_days"] == 19
    # Overtime and premium both reached the payslip.
    codes = {x["code"] for x in slip["breakdown"]["earnings"]}
    assert {"OT", "PREMIUM"} <= codes
    # And the arithmetic still closes.
    assert D(slip["net"]) == D(slip["gross"]) - D(slip["deductions"])


# --- overtime policy is configuration, not a constant ------------------------


@gate
def test_the_overtime_multiplier_is_configurable(client, co):
    """These vary by scheduled employment rather than by statute, so a factory
    on a different agreement is a settings change and not a deploy."""
    worker = hire(client, co, "Overtimer", est=co["bengaluru"])
    fact = client.post(f"{API}/workforce/facts", json={
        "employee_id": worker["id"], "day": "2026-08-05",
        "status": "worked", "hours_worked": "8", "overtime_hours": "10",
    }, headers=co["hr"]).json()
    client.post(f"{API}/workforce/facts/approve", json={"ids": [fact["id"]]},
                headers=co["hr"])

    at_double = slip_for(run(client, co), worker["id"])
    ot_double = next(
        x for x in at_double["breakdown"]["earnings"] if x["code"] == "OT")

    settings = client.get(f"{API}/payroll/settings", headers=co["hr"]).json()
    client.put(f"{API}/payroll/settings",
               json={**settings, "overtime_multiplier": "3.0"}, headers=co["hr"])

    at_triple = slip_for(run(client, co), worker["id"])
    ot_triple = next(
        x for x in at_triple["breakdown"]["earnings"] if x["code"] == "OT")

    assert D(ot_triple["amount"]) == D(ot_double["amount"]) * D("1.5")
