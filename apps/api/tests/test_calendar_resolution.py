"""Which calendar applies to THIS employee on THIS date.

The bug this replaces: holidays were unique per `(company, day)`, so a company
had exactly one calendar. A Mumbai establishment and a Bengaluru one could not
differ, and one site working through a day the rest of the company took off
could not be expressed at all.

That is not a UI limitation. Working days are the **denominator** of salary
proration, leave billing and the overtime hourly rate, so a wrong calendar is
wrong pay — quietly, for everybody at one location, every month.

The acceptance criterion is deliberately narrow: for any employee and any date,
the system deterministically knows whether that date is a working day, a weekly
off or a holiday, **and which calendar produced that decision.** This is not a
scheduling engine and must not become one.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.modules.work_calendar.service import (
    Assignment,
    count_working_days,
    is_working_weekday,
    pick_assignment,
)

API = "/api/v1"


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


endpoint = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")

CAL_A, CAL_B, CAL_C = (uuid.uuid4() for _ in range(3))
EST_MUMBAI, EST_BLR = uuid.uuid4(), uuid.uuid4()


def assign(calendar_id, scope_type, scope_id=None, *, frm="2000-01-01", to=None):
    return Assignment(
        calendar_id=calendar_id, scope_type=scope_type, scope_id=scope_id,
        effective_from=date.fromisoformat(frm),
        effective_to=date.fromisoformat(to) if to else None,
    )


# --- precedence and effective dating, pure -----------------------------------


def test_company_calendar_applies_when_nothing_more_specific():
    picked = pick_assignment(
        [assign(CAL_A, "company")], establishment_id=EST_MUMBAI, on=date(2026, 8, 15)
    )
    assert picked and picked.calendar_id == CAL_A


def test_establishment_overrides_company():
    """The whole point. Two establishments, one company calendar, different answers."""
    assignments = [assign(CAL_A, "company"), assign(CAL_B, "establishment", EST_MUMBAI)]
    mumbai = pick_assignment(assignments, establishment_id=EST_MUMBAI, on=date(2026, 8, 15))
    bengaluru = pick_assignment(assignments, establishment_id=EST_BLR, on=date(2026, 8, 15))
    assert mumbai.calendar_id == CAL_B
    assert bengaluru.calendar_id == CAL_A, "no override means inherit, not nothing"


def test_an_employee_with_no_establishment_still_resolves():
    """Establishment is nullable. Falling through to the company calendar is
    correct; returning nothing would divide by zero working days."""
    picked = pick_assignment(
        [assign(CAL_A, "company"), assign(CAL_B, "establishment", EST_MUMBAI)],
        establishment_id=None, on=date(2026, 8, 15),
    )
    assert picked.calendar_id == CAL_A


def test_assignment_is_effective_dated():
    """A site's calendar changes between years. August 2026 must resolve the
    assignment that was in force in August 2026."""
    assignments = [
        assign(CAL_A, "establishment", EST_MUMBAI, frm="2025-01-01", to="2026-03-31"),
        assign(CAL_B, "establishment", EST_MUMBAI, frm="2026-04-01"),
    ]
    assert pick_assignment(
        assignments, establishment_id=EST_MUMBAI, on=date(2026, 1, 10)
    ).calendar_id == CAL_A
    assert pick_assignment(
        assignments, establishment_id=EST_MUMBAI, on=date(2026, 8, 15)
    ).calendar_id == CAL_B


def test_a_future_assignment_does_not_apply_yet():
    """Scheduling next year's calendar must not change this year's payroll."""
    assignments = [
        assign(CAL_A, "establishment", EST_MUMBAI),
        assign(CAL_B, "establishment", EST_MUMBAI, frm="2027-01-01"),
    ]
    assert pick_assignment(
        assignments, establishment_id=EST_MUMBAI, on=date(2026, 8, 15)
    ).calendar_id == CAL_A


def test_more_specific_scope_wins_even_when_broader_one_starts_later():
    """Specificity beats recency. A company-wide calendar introduced in July
    must not silently take over a site that has its own."""
    assignments = [
        assign(CAL_A, "establishment", EST_MUMBAI, frm="2020-01-01"),
        assign(CAL_C, "company", frm="2026-07-01"),
    ]
    assert pick_assignment(
        assignments, establishment_id=EST_MUMBAI, on=date(2026, 8, 15)
    ).calendar_id == CAL_A


def test_no_applicable_assignment_returns_none_rather_than_guessing():
    """A caller must be able to tell "not configured" from "no holidays".
    Guessing a default here is how a site silently gets Mon–Fri."""
    assert pick_assignment(
        [assign(CAL_A, "company", frm="2027-01-01")],
        establishment_id=EST_MUMBAI, on=date(2026, 8, 15),
    ) is None


# --- the arithmetic these feed, already pure and unchanged -------------------


def test_working_day_arithmetic_is_unchanged():
    assert is_working_weekday(date(2026, 8, 15), "1111100") is False  # Saturday
    assert is_working_weekday(date(2026, 8, 15), "1111110") is True  # six-day week
    assert count_working_days(
        date(2026, 8, 1), date(2026, 8, 31), "1111100", {date(2026, 8, 17)}
    ) == 20  # 21 weekdays less one holiday


# --- resolution through the database ----------------------------------------


@pytest.fixture
def org(client):
    """Two establishments in one company, so the whole point is testable."""
    sub = f"cal-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Cal Co", "subdomain": sub,
        "email": admin, "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": sub, "email": admin, "password": "pw123456",
    }).json()["access_token"]
    hr = {"Authorization": f"Bearer {tok}"}

    mumbai = client.post(f"{API}/payroll/establishments", json={
        "name": "Mumbai", "state_code": "MH",
    }, headers=hr).json()
    bengaluru = client.post(f"{API}/payroll/establishments", json={
        "name": "Bengaluru", "state_code": "KA",
    }, headers=hr).json()

    basic = client.post(f"{API}/payroll/components", json={
        "code": "BASIC", "name": "Basic", "kind": "earning",
        "wage_basis": "wages", "esi_wage": True, "taxable": True, "sequence": 10,
    }, headers=hr).json()

    people = {}
    for name, est in (("Mira Mumbai", mumbai), ("Bala Bengaluru", bengaluru)):
        e = client.post(f"{API}/hr/employees", json={
            "full_name": name, "joined_on": "2026-01-01",
            "date_of_birth": "1992-05-05", "pf_first_joined_on": "2013-01-01",
            "establishment_id": est["id"],
        }, headers=hr).json()
        client.put(f"{API}/payroll/employees/{e['id']}/salary", json={
            "components": [{"component_id": basic["id"], "amount": "42000"}],
        }, headers=hr)
        people[name] = e

    return {"hr": hr, "sub": sub, "mumbai": mumbai, "bengaluru": bengaluru,
            "people": people, "basic": basic["id"]}


@endpoint
def test_two_establishments_same_date_different_holiday_status(client, org):
    """The headline scenario. 15 August is a holiday in Mumbai and a working
    day in Bengaluru, in the same company, in the same run."""
    mh = client.post(f"{API}/calendar/calendars", json={
        "name": "Maharashtra", "working_days": "1111100",
    }, headers=org["hr"]).json()
    client.post(f"{API}/calendar/calendars/{mh['id']}/holidays", json={
        "day": "2026-08-17", "name": "Local holiday",
    }, headers=org["hr"])
    client.post(f"{API}/calendar/assignments", json={
        "calendar_id": mh["id"], "scope_type": "establishment",
        "scope_id": org["mumbai"]["id"], "effective_from": "2026-01-01",
    }, headers=org["hr"])

    mira = org["people"]["Mira Mumbai"]["id"]
    bala = org["people"]["Bala Bengaluru"]["id"]

    m = client.get(f"{API}/calendar/resolve?employee_id={mira}&on=2026-08-17",
                   headers=org["hr"]).json()
    b = client.get(f"{API}/calendar/resolve?employee_id={bala}&on=2026-08-17",
                   headers=org["hr"]).json()

    assert m["is_holiday"] is True and m["holiday_name"] == "Local holiday"
    assert b["is_holiday"] is False
    # "which calendar produced that decision" — the acceptance criterion
    assert m["calendar_name"] == "Maharashtra" and m["source"] == "establishment"
    assert b["source"] == "company"


@endpoint
def test_working_day_counts_differ_and_so_does_pay(client, org):
    """Working days are the denominator of proration. A different calendar
    must produce different pay, or the fix is cosmetic."""
    mh = client.post(f"{API}/calendar/calendars", json={
        "name": "Maharashtra", "working_days": "1111100",
    }, headers=org["hr"]).json()
    # Three extra holidays only Mumbai observes.
    for day in ("2026-08-17", "2026-08-18", "2026-08-19"):
        client.post(f"{API}/calendar/calendars/{mh['id']}/holidays",
                    json={"day": day, "name": "Local"}, headers=org["hr"])
    client.post(f"{API}/calendar/assignments", json={
        "calendar_id": mh["id"], "scope_type": "establishment",
        "scope_id": org["mumbai"]["id"], "effective_from": "2026-01-01",
    }, headers=org["hr"])

    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    slips = {p["employee_name"]: p for p in run["payslips"]}

    assert slips["Mira Mumbai"]["working_days"] == 18
    assert slips["Bala Bengaluru"]["working_days"] == 21
    # Same salary, full month, no LOP — so gross is equal. The DENOMINATOR is
    # what differs, and it shows the moment anybody has unpaid days.
    assert slips["Mira Mumbai"]["working_days"] != slips["Bala Bengaluru"]["working_days"]


@endpoint
def test_lop_uses_the_employee_calendar_not_the_company_one(client, org):
    """One unpaid day costs a Mumbai employee 1/18th and a Bengaluru employee
    1/21st. Sharing a denominator overpays one and underpays the other."""
    mh = client.post(f"{API}/calendar/calendars", json={
        "name": "Maharashtra", "working_days": "1111100",
    }, headers=org["hr"]).json()
    for day in ("2026-08-17", "2026-08-18", "2026-08-19"):
        client.post(f"{API}/calendar/calendars/{mh['id']}/holidays",
                    json={"day": day, "name": "Local"}, headers=org["hr"])
    client.post(f"{API}/calendar/assignments", json={
        "calendar_id": mh["id"], "scope_type": "establishment",
        "scope_id": org["mumbai"]["id"], "effective_from": "2026-01-01",
    }, headers=org["hr"])

    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    for slip in run["payslips"]:
        client.patch(
            f"{API}/payroll/runs/{run['id']}/payslips/{slip['id']}",
            json={"lop_days": 1}, headers=org["hr"],
        )
    after = client.get(f"{API}/payroll/runs/{run['id']}", headers=org["hr"]).json()
    slips = {p["employee_name"]: p for p in after["payslips"]}

    from decimal import Decimal as D

    mira, bala = slips["Mira Mumbai"], slips["Bala Bengaluru"]
    assert D(mira["gross"]) < D(bala["gross"]), (
        "a day off an 18-day month costs more than a day off a 21-day month"
    )


@endpoint
def test_historical_period_resolves_the_calendar_of_that_period(client, org):
    """Reassigning a calendar today must not change what June resolved."""
    old = client.post(f"{API}/calendar/calendars", json={
        "name": "Old", "working_days": "1111100",
    }, headers=org["hr"]).json()
    new = client.post(f"{API}/calendar/calendars", json={
        "name": "New", "working_days": "1111110",
    }, headers=org["hr"]).json()

    client.post(f"{API}/calendar/assignments", json={
        "calendar_id": old["id"], "scope_type": "establishment",
        "scope_id": org["mumbai"]["id"],
        "effective_from": "2026-01-01", "effective_to": "2026-06-30",
    }, headers=org["hr"])
    client.post(f"{API}/calendar/assignments", json={
        "calendar_id": new["id"], "scope_type": "establishment",
        "scope_id": org["mumbai"]["id"], "effective_from": "2026-07-01",
    }, headers=org["hr"])

    mira = org["people"]["Mira Mumbai"]["id"]
    june = client.get(f"{API}/calendar/resolve?employee_id={mira}&on=2026-06-15",
                      headers=org["hr"]).json()
    august = client.get(f"{API}/calendar/resolve?employee_id={mira}&on=2026-08-15",
                        headers=org["hr"]).json()

    assert june["calendar_name"] == "Old"
    assert august["calendar_name"] == "New"
    # 15 Aug 2026 is a Saturday: a working day on a six-day week, not on five.
    assert august["is_working_day"] is True
    assert june["working_days"] == "1111100"


@endpoint
def test_a_finalized_run_is_unaffected_by_a_later_calendar_change(client, org):
    """Frozen means frozen, including the denominator it was computed on."""
    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    client.post(f"{API}/payroll/runs/{run['id']}/finalize", headers=org["hr"])
    before = client.get(f"{API}/payroll/runs/{run['id']}", headers=org["hr"]).json()

    six_day = client.post(f"{API}/calendar/calendars", json={
        "name": "Six day", "working_days": "1111110",
    }, headers=org["hr"]).json()
    client.post(f"{API}/calendar/assignments", json={
        "calendar_id": six_day["id"], "scope_type": "company",
        "effective_from": "2026-01-01",
    }, headers=org["hr"])

    after = client.get(f"{API}/payroll/runs/{run['id']}", headers=org["hr"]).json()
    assert after["payslips"] == before["payslips"]
    assert after["status"] == "finalized"


@endpoint
def test_calendars_are_tenant_isolated(client, org):
    """Another company's calendar must not resolve, and must not be visible."""
    mine = client.post(f"{API}/calendar/calendars", json={
        "name": "Mine", "working_days": "1111100",
    }, headers=org["hr"]).json()

    other = f"cal-{uuid.uuid4().hex[:8]}"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Other", "subdomain": other,
        "email": f"admin@{other}.test", "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": other, "email": f"admin@{other}.test", "password": "pw123456",
    }).json()["access_token"]
    theirs = {"Authorization": f"Bearer {tok}"}

    listed = client.get(f"{API}/calendar/calendars", headers=theirs).json()
    assert all(c["id"] != mine["id"] for c in listed)
    assert client.get(f"{API}/calendar/calendars/{mine['id']}",
                      headers=theirs).status_code == 404


@endpoint
def test_day_state_still_classifies_from_the_resolved_calendar(client, org):
    """day_state() remains the single vocabulary. It must now be fed by the
    employee's calendar, not the company's — a holiday at one site is not an
    absence, and is not a holiday at the other."""
    mh = client.post(f"{API}/calendar/calendars", json={
        "name": "Maharashtra", "working_days": "1111100",
    }, headers=org["hr"]).json()
    client.post(f"{API}/calendar/calendars/{mh['id']}/holidays", json={
        "day": "2026-08-17", "name": "Local holiday",
    }, headers=org["hr"])
    client.post(f"{API}/calendar/assignments", json={
        "calendar_id": mh["id"], "scope_type": "establishment",
        "scope_id": org["mumbai"]["id"], "effective_from": "2026-01-01",
    }, headers=org["hr"])

    mira = org["people"]["Mira Mumbai"]["id"]
    bala = org["people"]["Bala Bengaluru"]["id"]

    # The attendance range counts back from today, so pick a recent past date.
    when = (date.today() - timedelta(days=3)).isoformat()
    client.post(f"{API}/calendar/calendars/{mh['id']}/holidays", json={
        "day": when, "name": "Site holiday",
    }, headers=org["hr"])

    m = client.get(f"{API}/attendance/{mira}?days=7", headers=org["hr"]).json()
    b = client.get(f"{API}/attendance/{bala}?days=7", headers=org["hr"]).json()
    mine = next(d for d in m if d["day"] == when)
    theirs = next(d for d in b if d["day"] == when)

    assert mine["holiday"] == "Site holiday" and mine["working_day"] is False
    assert theirs["holiday"] is None, "the other site does not observe it"


@endpoint
def test_an_assignment_can_be_removed(client, org):
    """An assignment made in error must be removable, or a wrong calendar is
    permanent."""
    cal = client.post(f"{API}/calendar/calendars", json={
        "name": "Wrong", "working_days": "1111110",
    }, headers=org["hr"]).json()
    asg = client.post(f"{API}/calendar/assignments", json={
        "calendar_id": cal["id"], "scope_type": "establishment",
        "scope_id": org["mumbai"]["id"], "effective_from": "2026-01-01",
    }, headers=org["hr"]).json()

    mira = org["people"]["Mira Mumbai"]["id"]
    before = client.get(f"{API}/calendar/resolve?employee_id={mira}&on=2026-08-15",
                        headers=org["hr"]).json()
    assert before["calendar_name"] == "Wrong"

    assert client.delete(f"{API}/calendar/assignments/{asg['id']}",
                         headers=org["hr"]).status_code == 204

    after = client.get(f"{API}/calendar/resolve?employee_id={mira}&on=2026-08-15",
                       headers=org["hr"]).json()
    assert after["source"] == "company", "removing the override falls back, not off a cliff"


@endpoint
def test_the_last_company_assignment_cannot_be_removed(client, org):
    """A company that resolves nothing is paid for zero working days."""
    mira = org["people"]["Mira Mumbai"]["id"]
    client.get(f"{API}/calendar/resolve?employee_id={mira}&on=2026-08-15",
               headers=org["hr"])  # forces the default into existence

    company = [
        a for a in client.get(f"{API}/calendar/assignments", headers=org["hr"]).json()
        if a["scope_type"] == "company"
    ]
    assert len(company) == 1
    r = client.delete(f"{API}/calendar/assignments/{company[0]['id']}", headers=org["hr"])
    assert r.status_code == 409
    assert "no working days at all" in r.json()["detail"]
