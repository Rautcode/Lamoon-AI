"""Work calendar: working-day arithmetic, and the leave-billing bug it fixes.

The headline test is `test_friday_to_monday_bills_two_days` — before this
module, that request cost 4 days of someone's balance.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.modules.work_calendar.service import count_working_days, is_working_weekday

MON, TUE, WED, THU, FRI, SAT, SUN = (date(2026, 8, d) for d in range(10, 17))
MON_FRI = "1111100"
MON_SAT = "1111110"


# --- pure arithmetic --------------------------------------------------------


def test_weekday_pattern_is_monday_first():
    assert is_working_weekday(MON, MON_FRI) is True
    assert is_working_weekday(FRI, MON_FRI) is True
    assert is_working_weekday(SAT, MON_FRI) is False
    assert is_working_weekday(SUN, MON_FRI) is False


def test_six_day_week_counts_saturday():
    """Mon–Sat is common in Indian SMEs; assuming Mon–Fri would mis-bill them."""
    assert is_working_weekday(SAT, MON_SAT) is True
    assert is_working_weekday(SUN, MON_SAT) is False


def test_friday_to_monday_is_two_working_days():
    assert count_working_days(FRI, MON + timedelta(days=7), MON_FRI, set()) == 2


def test_holiday_inside_range_is_excluded():
    assert count_working_days(MON, FRI, MON_FRI, set()) == 5
    assert count_working_days(MON, FRI, MON_FRI, {WED}) == 4


def test_range_entirely_on_a_weekend_is_zero():
    assert count_working_days(SAT, SUN, MON_FRI, set()) == 0


def test_single_working_day():
    assert count_working_days(WED, WED, MON_FRI, set()) == 1


def test_reversed_range_is_zero_not_negative():
    assert count_working_days(FRI, MON, MON_FRI, set()) == 0


def test_malformed_pattern_falls_back_to_mon_fri():
    """A bad value must not make every day a holiday and zero out billing."""
    assert is_working_weekday(WED, "nonsense") is True
    assert is_working_weekday(SAT, "") is False


# --- endpoints + the billing fix -------------------------------------------


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


endpoint = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


@pytest.fixture
def org(client):
    sub = f"cal-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(
        "/api/v1/auth/bootstrap",
        json={"company_name": "Cal Co", "subdomain": sub, "email": admin, "password": "pw123456"},
    )
    tok = client.post(
        "/api/v1/auth/login", json={"company": sub, "email": admin, "password": "pw123456"}
    ).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    emp = client.post(
        "/api/v1/hr/employees", json={"full_name": "Asha Rao"}, headers=hr
    ).json()
    lt = client.post(
        "/api/v1/leave/types", json={"name": "Annual", "annual_quota": 20}, headers=hr
    ).json()
    return {"hr": hr, "employee": emp, "leave_type": lt, "sub": sub}


def _next_weekday(target: int) -> date:
    d = date.today() + timedelta(days=1)
    while d.weekday() != target:
        d += timedelta(days=1)
    return d


def _file(client, org, start: date, end: date):
    return client.post(
        "/api/v1/leave/requests",
        json={
            "employee_id": org["employee"]["id"], "leave_type_id": org["leave_type"]["id"],
            "start_date": str(start), "end_date": str(end),
        },
        headers=org["hr"],
    )


@endpoint
def test_friday_to_monday_bills_two_days(client, org):
    """THE bug this module exists to fix: 4 calendar days, 2 working days."""
    friday = _next_weekday(4)
    r = _file(client, org, friday, friday + timedelta(days=3))
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 2


@endpoint
def test_holiday_reduces_the_billed_days(client, org):
    monday = _next_weekday(0)
    wednesday = monday + timedelta(days=2)
    assert _file(client, org, monday, monday + timedelta(days=4)).json()["days"] == 5

    client.post(
        "/api/v1/calendar/holidays",
        json={"day": str(wednesday), "name": "Independence Day"},
        headers=org["hr"],
    )
    assert _file(client, org, monday, monday + timedelta(days=4)).json()["days"] == 4


@endpoint
def test_weekend_only_leave_is_rejected(client, org):
    saturday = _next_weekday(5)
    r = _file(client, org, saturday, saturday + timedelta(days=1))
    assert r.status_code == 422
    assert "no working days" in r.json()["detail"]


@endpoint
def test_six_day_week_bills_saturday(client, org):
    saturday = _next_weekday(5)
    assert _file(client, org, saturday, saturday).status_code == 422  # Mon–Fri default

    r = client.put(
        "/api/v1/calendar/work-week", json={"working_days": "1111110"}, headers=org["hr"]
    )
    assert r.status_code == 200
    assert _file(client, org, saturday, saturday).json()["days"] == 1


@endpoint
def test_work_week_pattern_is_validated(client, org):
    for bad in ("111110", "1111112", "0000000", "abcdefg"):
        r = client.put(
            "/api/v1/calendar/work-week", json={"working_days": bad}, headers=org["hr"]
        )
        assert r.status_code == 422, f"{bad!r} should be rejected"


@endpoint
def test_holiday_add_is_idempotent(client, org):
    day = str(_next_weekday(0))
    first = client.post(
        "/api/v1/calendar/holidays", json={"day": day, "name": "Diwali"}, headers=org["hr"]
    ).json()
    second = client.post(
        "/api/v1/calendar/holidays", json={"day": day, "name": "Deepavali"}, headers=org["hr"]
    ).json()
    assert first["id"] == second["id"]  # renamed, not duplicated
    assert second["name"] == "Deepavali"


@endpoint
def test_deleted_holiday_stops_discounting(client, org):
    monday = _next_weekday(0)
    h = client.post(
        "/api/v1/calendar/holidays", json={"day": str(monday), "name": "One-off"},
        headers=org["hr"],
    ).json()
    assert _file(client, org, monday, monday + timedelta(days=4)).json()["days"] == 4

    deleted = client.delete(f"/api/v1/calendar/holidays/{h['id']}", headers=org["hr"])
    assert deleted.status_code == 204
    assert _file(client, org, monday, monday + timedelta(days=4)).json()["days"] == 5


@endpoint
def test_existing_requests_keep_their_original_day_counts(client, org):
    """Billing changed; history didn't. A request already agreed at N days
    stays at N — silently re-deriving the past would be worse than the bug."""
    friday = _next_weekday(4)
    req = _file(client, org, friday, friday + timedelta(days=3)).json()
    listed = client.get("/api/v1/leave/requests", headers=org["hr"]).json()
    assert next(r for r in listed if r["id"] == req["id"])["days"] == req["days"]


@endpoint
def test_employees_can_read_holidays_but_not_change_them(client, org):
    from app.core.notify.base import outbox

    emp = client.post(
        "/api/v1/hr/employees",
        json={"full_name": "Ben Ford", "email": f"ben@{org['sub']}.test"},
        headers=org["hr"],
    ).json()
    outbox.clear()
    client.post(f"/api/v1/hr/employees/{emp['id']}/invite", headers=org["hr"])
    mail = next(m for m in outbox if m["template"] == "access_granted")
    pw = next(
        ln.split("Password:")[1].strip() for ln in mail["body"].splitlines() if "Password:" in ln
    )
    tok = client.post(
        "/api/v1/auth/login",
        json={"company": org["sub"], "email": f"ben@{org['sub']}.test", "password": pw},
    ).json()
    emp_h = {"Authorization": f"Bearer {tok['access_token']}"}

    # Holidays aren't sensitive — everyone needs to know when the office shuts.
    assert client.get("/api/v1/calendar/holidays", headers=emp_h).status_code == 200
    assert client.get("/api/v1/calendar/work-week", headers=emp_h).status_code == 200
    # But changing them re-bills everyone's future leave.
    assert (
        client.post(
            "/api/v1/calendar/holidays",
            json={"day": str(_next_weekday(0)), "name": "Nope"},
            headers=emp_h,
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/v1/calendar/work-week", json={"working_days": "1111111"}, headers=emp_h
        ).status_code
        == 403
    )
