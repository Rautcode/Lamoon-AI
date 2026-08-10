"""Attendance.

`pair_events` is pure, so the messy cases (double punches, missing check-out,
out-of-order corrections, clock skew) are tested directly with no DB — that's
where the real logic lives. The endpoint tests then check the wiring, the
permission boundary, and the timezone behaviour that made this module
non-trivial in the first place.
"""
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.modules.attendance.service import Punch, pair_events

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 8, 10)


def _pair(punches, *, now=None, tz=IST, start=time(9, 30), expected=480, grace=15):
    return pair_events(
        punches, day=DAY, tz=tz, workday_start=start,
        expected_minutes=expected, grace_minutes=grace, now=now,
    )


def ist(h, m=0, d=10):
    return datetime(2026, 8, d, h, m, tzinfo=IST)


# --- pure pairing logic -----------------------------------------------------


def test_simple_full_day():
    s = _pair([Punch("in", ist(9, 25)), Punch("out", ist(18, 0))])
    assert s.worked_minutes == 515
    assert s.first_in == ist(9, 25) and s.last_out == ist(18, 0)
    assert not s.open and not s.late and not s.short


def test_multiple_punches_sum_only_worked_intervals():
    """Out for lunch — the gap must not count as worked time."""
    s = _pair(
        [
            Punch("in", ist(9, 0)), Punch("out", ist(13, 0)),
            Punch("in", ist(14, 0)), Punch("out", ist(18, 0)),
        ]
    )
    assert s.worked_minutes == 480  # 4h + 4h, lunch excluded
    assert s.first_in == ist(9, 0)
    assert s.last_out == ist(18, 0)
    assert not s.short


def test_open_day_accrues_to_now():
    s = _pair([Punch("in", ist(9, 0))], now=ist(11, 30))
    assert s.open is True
    assert s.worked_minutes == 150
    # Mid-shift is not a short day — you're just not finished.
    assert s.short is False


def test_double_check_in_keeps_the_earlier_one():
    """A double tap must not restart the clock and swallow the earlier minutes."""
    s = _pair([Punch("in", ist(9, 0)), Punch("in", ist(9, 1)), Punch("out", ist(17, 0))])
    assert s.first_in == ist(9, 0)
    assert s.worked_minutes == 480
    assert any("duplicate check-in" in a for a in s.anomalies)


def test_checkout_without_checkin_is_an_anomaly_not_a_crash():
    s = _pair([Punch("out", ist(17, 0))])
    assert s.worked_minutes == 0
    assert s.first_in is None
    assert any("no check-in" in a for a in s.anomalies)


def test_events_are_sorted_before_pairing():
    """HR inserting a correction later must still produce the right total."""
    s = _pair([Punch("out", ist(17, 0)), Punch("in", ist(9, 0))])
    assert s.worked_minutes == 480
    assert s.anomalies == []


def test_clock_skew_cannot_produce_negative_time():
    s = _pair([Punch("in", ist(12, 0))], now=ist(11, 0))  # "now" before the punch
    assert s.worked_minutes == 0
    assert s.open is True


def test_late_uses_local_time_and_grace():
    assert _pair([Punch("in", ist(9, 44)), Punch("out", ist(18, 0))]).late is False  # inside grace
    assert _pair([Punch("in", ist(9, 46)), Punch("out", ist(18, 0))]).late is True


def test_short_day_only_when_closed():
    closed = _pair([Punch("in", ist(9, 0)), Punch("out", ist(12, 0))])
    assert closed.short is True and closed.worked_minutes == 180
    still_working = _pair([Punch("in", ist(9, 0))], now=ist(12, 0))
    assert still_working.short is False


def test_empty_day():
    s = _pair([])
    assert s.worked_minutes == 0 and s.first_in is None and not s.late and not s.short


# --- endpoints --------------------------------------------------------------


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
    from app.core.notify.base import outbox

    sub = f"att-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(
        "/api/v1/auth/bootstrap",
        json={"company_name": "Att Co", "subdomain": sub, "email": admin, "password": "pw123456"},
    )
    hr_tok = client.post(
        "/api/v1/auth/login", json={"company": sub, "email": admin, "password": "pw123456"}
    ).json()
    hr = {"Authorization": f"Bearer {hr_tok['access_token']}"}

    emp = client.post(
        "/api/v1/hr/employees",
        json={"full_name": "Asha Rao", "email": f"asha@{sub}.test"},
        headers=hr,
    ).json()

    outbox.clear()
    client.post(f"/api/v1/hr/employees/{emp['id']}/invite", headers=hr)
    mail = next(m for m in outbox if m["template"] == "access_granted")
    pw = next(
        line.split("Password:")[1].strip()
        for line in mail["body"].splitlines()
        if "Password:" in line
    )
    emp_tok = client.post(
        "/api/v1/auth/login", json={"company": sub, "email": f"asha@{sub}.test", "password": pw}
    ).json()
    return {
        "hr": hr,
        "emp": {"Authorization": f"Bearer {emp_tok['access_token']}"},
        "employee": emp,
        "sub": sub,
    }


@endpoint
def test_policy_defaults_are_created_on_first_read(client, org):
    p = client.get("/api/v1/attendance/policy", headers=org["hr"]).json()
    assert p["timezone"] == "Asia/Kolkata"
    assert p["expected_minutes"] == 480


@endpoint
def test_policy_rejects_unknown_timezone(client, org):
    r = client.put(
        "/api/v1/attendance/policy",
        json={
            "workday_start": "09:00", "expected_minutes": 480,
            "grace_minutes": 10, "timezone": "Mars/Olympus_Mons",
        },
        headers=org["hr"],
    )
    assert r.status_code == 422


@endpoint
def test_employee_punches_own_day(client, org):
    r = client.post("/api/v1/me/attendance/punch", json={"kind": "in"}, headers=org["emp"])
    assert r.status_code == 200, r.text
    assert r.json()["open"] is True

    # Double check-in is refused rather than silently recorded.
    again = client.post("/api/v1/me/attendance/punch", json={"kind": "in"}, headers=org["emp"])
    assert again.status_code == 409

    out = client.post("/api/v1/me/attendance/punch", json={"kind": "out"}, headers=org["emp"])
    assert out.status_code == 200
    assert out.json()["open"] is False


@endpoint
def test_checkout_without_checkin_refused(client, org):
    r = client.post("/api/v1/me/attendance/punch", json={"kind": "out"}, headers=org["emp"])
    assert r.status_code == 409


@endpoint
def test_presence_today_shows_everyone(client, org):
    rows = client.get("/api/v1/attendance/today", headers=org["hr"]).json()
    asha = next(r for r in rows if r["employee_id"] == org["employee"]["id"])
    assert asha["status"] == "absent"  # not punched yet

    client.post("/api/v1/me/attendance/punch", json={"kind": "in"}, headers=org["emp"])
    rows = client.get("/api/v1/attendance/today", headers=org["hr"]).json()
    asha = next(r for r in rows if r["employee_id"] == org["employee"]["id"])
    assert asha["status"] == "in"


@endpoint
def test_hr_can_correct_a_missed_punch(client, org):
    """The ledger is append-only, so a correction is a new event at a past time.

    Both punches are pinned to fixed times on yesterday's LOCAL (IST) day
    rather than offsets from `now` — `now - 1d + 8h` straddles local midnight
    depending on the hour the suite runs, which would split one shift across
    two days and make this test's result depend on the clock."""
    yesterday_ist = (datetime.now(UTC).astimezone(IST) - timedelta(days=1)).date()
    for kind, hour in (("in", 9), ("out", 17)):
        at = datetime.combine(yesterday_ist, time(hour, 0), tzinfo=IST)
        r = client.post(
            "/api/v1/attendance/punch",
            json={
                "employee_id": org["employee"]["id"], "kind": kind,
                "at": at.isoformat(), "note": "missed punch",
            },
            headers=org["hr"],
        )
        assert r.status_code == 200, r.text

    history = client.get(
        f"/api/v1/attendance/{org['employee']['id']}?days=3", headers=org["hr"]
    ).json()
    assert any(d["worked_minutes"] >= 480 for d in history)


@endpoint
def test_company_summary_is_not_shadowed_by_the_employee_route(client, org):
    """/summary must not be parsed as an employee UUID by /{employee_id} —
    route declaration order is load-bearing here."""
    r = client.get("/api/v1/attendance/summary?days=7", headers=org["hr"])
    assert r.status_code == 200, r.text
    rows = r.json()
    assert any(row["employee_id"] == org["employee"]["id"] for row in rows)
    assert all("days" in row for row in rows)


@endpoint
def test_company_summary_serialises_days_with_real_punches(client, org):
    """Regression: the first version of this endpoint 500'd as soon as anyone
    actually had punches. The original test only covered an employee with an
    EMPTY day list, so the nested dataclass->model validation never ran."""
    yesterday_ist = (datetime.now(UTC).astimezone(IST) - timedelta(days=1)).date()
    for kind, hour in (("in", 9), ("out", 17)):
        at = datetime.combine(yesterday_ist, time(hour, 0), tzinfo=IST)
        client.post(
            "/api/v1/attendance/punch",
            json={"employee_id": org["employee"]["id"], "kind": kind, "at": at.isoformat()},
            headers=org["hr"],
        )

    r = client.get("/api/v1/attendance/summary?days=7", headers=org["hr"])
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["employee_id"] == org["employee"]["id"])
    worked = [d for d in row["days"] if d["worked_minutes"] > 0]
    assert worked and worked[0]["worked_minutes"] == 480
    assert worked[0]["first_in"] is not None  # fields actually serialised


@endpoint
def test_employee_cannot_read_company_attendance(client, org):
    """Same boundary as the rest of ESS: no company-wide view, no other people."""
    assert client.get("/api/v1/attendance/today", headers=org["emp"]).status_code == 403
    assert (
        client.get(f"/api/v1/attendance/{org['employee']['id']}", headers=org["emp"]).status_code
        == 403
    )
    assert client.get("/api/v1/attendance/policy", headers=org["emp"]).status_code == 403


@endpoint
def test_employee_cannot_punch_for_someone_else(client, org):
    r = client.post(
        "/api/v1/attendance/punch",
        json={"employee_id": org["employee"]["id"], "kind": "in"},
        headers=org["emp"],
    )
    assert r.status_code == 403


@endpoint
def test_my_attendance_is_only_mine(client, org):
    client.post("/api/v1/me/attendance/punch", json={"kind": "in"}, headers=org["emp"])
    mine = client.get("/api/v1/me/attendance", headers=org["emp"]).json()
    assert len(mine) == 1 and mine[0]["open"] is True
