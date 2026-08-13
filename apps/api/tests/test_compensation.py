"""Effective-dated compensation.

The question every one of these tests is really asking: **what applied to this
person during THIS period?** The model this replaces could not answer it. It
stored one live salary and soft-deleted the rest, so a raise entered on 5
August effective 1 July was indistinguishable from one effective 5 August —
a transaction history, not an effective one.

The dangerous failure here is silent. Nothing crashes when payroll resolves
the wrong salary version; somebody is simply paid the wrong amount, and it is
found months later by the employee, not by the system.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.modules.compensation.service import month_bounds, prorate

API = "/api/v1"


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


endpoint = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


# --- proration is pure, so test it without a database ------------------------


def test_full_period_is_exact_not_rounded():
    """The ordinary case — nobody's pay changed — must never drift by a paisa.
    A ratio of 22/22 has to return the amount itself, not the amount multiplied
    by one and re-rounded."""
    assert prorate(Decimal("41000.00"), segment_working_days=22, period_working_days=22) == (
        Decimal("41000.00")
    )
    assert prorate(Decimal("33333.33"), segment_working_days=22, period_working_days=22) == (
        Decimal("33333.33")
    )


def test_half_period_halves_the_pay():
    assert prorate(Decimal("44000.00"), segment_working_days=11, period_working_days=22) == (
        Decimal("22000.00")
    )


def test_a_shutdown_month_pays_in_full_rather_than_dividing_by_zero():
    """Every day declared a holiday. The guard against dividing by zero must
    not turn into a decision that nobody gets paid — a monthly salary is not
    forfeited because the company closed for the month."""
    assert prorate(Decimal("41000.00"), segment_working_days=0, period_working_days=0) == (
        Decimal("41000.00")
    )


def test_month_bounds_handles_february_and_december():
    assert month_bounds(date(2026, 2, 5)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds(date(2028, 2, 1)) == (date(2028, 2, 1), date(2028, 2, 29))  # leap
    assert month_bounds(date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))


# --- endpoint fixtures -------------------------------------------------------


@pytest.fixture
def org(client):
    """A tenant of its own. Shared tenants leak state between tests and this
    one creates employees, which trips the seat limit on a shared company."""
    sub = f"comp-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Comp Co", "subdomain": sub,
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
    hra = client.post(f"{API}/payroll/components", json={
        "code": "HRA", "name": "HRA", "kind": "earning",
        "wage_basis": "excluded", "esi_wage": True, "taxable": True, "sequence": 20,
    }, headers=hr).json()
    employee = client.post(f"{API}/hr/employees", json={
        "full_name": "Ravi Kumar", "joined_on": "2026-01-01",
        "date_of_birth": "1992-05-05", "pf_first_joined_on": "2013-01-01",
    }, headers=hr).json()
    return {"hr": hr, "sub": sub, "employee": employee,
            "basic": basic["id"], "hra": hra["id"]}


def add_version(client, org, *, on: str, basic: str, reason="revision"):
    return client.post(
        f"{API}/compensation/employees/{org['employee']['id']}/versions",
        json={"effective_from": on, "reason": reason,
              "lines": [{"component_id": org["basic"], "amount": basic}]},
        headers=org["hr"],
    )


def resolve(client, org, on: str):
    return client.get(
        f"{API}/compensation/employees/{org['employee']['id']}/resolve?on={on}",
        headers=org["hr"],
    ).json()


# --- the eleven behaviours ---------------------------------------------------


@endpoint
def test_first_salary_version(client, org):
    r = add_version(client, org, on="2026-01-01", basic="38000", reason="hire")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["effective_from"] == "2026-01-01"
    assert body["effective_to"] is None, "the first version is open-ended"
    assert body["gross"] == "38000.00"


@endpoint
def test_future_dated_version_does_not_apply_yet(client, org):
    add_version(client, org, on="2026-01-01", basic="38000", reason="hire")
    add_version(client, org, on="2027-01-01", basic="50000")
    assert resolve(client, org, "2026-06-01")["gross"] == "38000.00"
    assert resolve(client, org, "2027-06-01")["gross"] == "50000.00"


@endpoint
def test_sequential_revisions_close_the_previous_version(client, org):
    add_version(client, org, on="2026-01-01", basic="38000", reason="hire")
    add_version(client, org, on="2026-07-01", basic="41000")
    add_version(client, org, on="2026-09-01", basic="45000")

    versions = client.get(
        f"{API}/compensation/employees/{org['employee']['id']}/versions",
        headers=org["hr"],
    ).json()
    # Newest first.
    spans = [(v["effective_from"], v["effective_to"]) for v in versions]
    assert spans == [
        ("2026-09-01", None),
        ("2026-07-01", "2026-08-31"),
        ("2026-01-01", "2026-06-30"),
    ], "each version must be closed the day before the next begins"


@endpoint
def test_overlapping_revision_is_refused(client, org):
    add_version(client, org, on="2026-07-01", basic="41000", reason="hire")
    again = add_version(client, org, on="2026-07-01", basic="99000")
    assert again.status_code == 409
    assert "already starts" in again.json()["detail"]


@endpoint
def test_a_version_inserted_between_two_others_is_bounded_by_both(client, org):
    """Backfilling a missed revision must not silently swallow the later one."""
    add_version(client, org, on="2026-01-01", basic="38000", reason="hire")
    add_version(client, org, on="2026-09-01", basic="45000")
    add_version(client, org, on="2026-05-01", basic="41000")

    versions = client.get(
        f"{API}/compensation/employees/{org['employee']['id']}/versions",
        headers=org["hr"],
    ).json()
    spans = {v["effective_from"]: v["effective_to"] for v in versions}
    assert spans["2026-01-01"] == "2026-04-30"
    assert spans["2026-05-01"] == "2026-08-31"
    assert spans["2026-09-01"] is None


@endpoint
def test_historical_payroll_resolves_the_version_of_that_period(client, org):
    add_version(client, org, on="2026-01-01", basic="38000", reason="hire")
    add_version(client, org, on="2026-07-01", basic="41000")
    add_version(client, org, on="2026-09-01", basic="45000")

    assert resolve(client, org, "2026-03-15")["gross"] == "38000.00"
    assert resolve(client, org, "2026-08-31")["gross"] == "41000.00"
    assert resolve(client, org, "2026-09-01")["gross"] == "45000.00"


@endpoint
def test_payroll_uses_the_period_not_today(client, org):
    """The one that matters most. A raise effective September must not change
    what August's payroll pays, even though September is 'current' now."""
    add_version(client, org, on="2026-01-01", basic="40000", reason="hire")
    add_version(client, org, on="2026-09-01", basic="60000")

    august = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                         headers=org["hr"]).json()
    september = client.post(f"{API}/payroll/runs", json={"period": "2026-09-01"},
                            headers=org["hr"]).json()

    def gross(run):
        return Decimal(run["payslips"][0]["gross"])

    assert gross(august) == Decimal("40000.00")
    assert gross(september) == Decimal("60000.00")


@endpoint
def test_mid_month_revision_prorates_both_halves(client, org):
    """Not "whichever is latest". August 2026 has 21 working days (Mon-Fri, no
    holidays configured): 10 up to the 14th, 11 from the 17th."""
    add_version(client, org, on="2026-01-01", basic="21000", reason="hire")
    add_version(client, org, on="2026-08-17", basic="42000")

    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    gross = Decimal(run["payslips"][0]["gross"])

    # Neither version alone, and strictly between them.
    assert Decimal("21000") < gross < Decimal("42000")
    # 10/21 of 21000 + 11/21 of 42000
    expected = (Decimal("21000") * 10 / 21).quantize(Decimal("0.01")) + (
        Decimal("42000") * 11 / 21
    ).quantize(Decimal("0.01"))
    assert gross == expected


@endpoint
def test_mid_month_split_is_explained_on_the_payslip(client, org):
    add_version(client, org, on="2026-01-01", basic="21000", reason="hire")
    add_version(client, org, on="2026-08-17", basic="42000")
    client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"}, headers=org["hr"])

    inputs = client.get(
        f"{API}/payroll/inputs?employee_id={org['employee']['id']}&period=2026-08-01",
        headers=org["hr"],
    ).json()
    reasons = [i["reason"] for i in inputs if i["source"] == "structure"]
    assert reasons and all("changed mid-period" in (r or "") for r in reasons), (
        "a number nobody can explain is a support ticket"
    )


@endpoint
def test_retroactive_change_leaves_finalized_payroll_alone(client, org):
    """History must be able to become correct without rewriting what was paid.
    The difference is arrears — an adjustment in a later period — not a silent
    edit to a frozen payslip."""
    add_version(client, org, on="2026-01-01", basic="40000", reason="hire")
    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    client.post(f"{API}/payroll/runs/{run['id']}/finalize", headers=org["hr"])
    frozen = client.get(f"{API}/payroll/runs/{run['id']}", headers=org["hr"]).json()
    paid = frozen["payslips"][0]["gross"]

    # A backdated raise covering the already-finalized month.
    late = add_version(client, org, on="2026-08-01", basic="46000", reason="correction")
    assert late.status_code == 200, late.text

    after = client.get(f"{API}/payroll/runs/{run['id']}", headers=org["hr"]).json()
    assert after["payslips"][0]["gross"] == paid, "a finalized payslip is immutable"
    assert after["status"] == "finalized"


@endpoint
def test_recomputing_a_draft_after_a_raise_does_pick_it_up(client, org):
    """The mirror of the previous test: a DRAFT is still being worked on, so it
    must reflect a corrected salary."""
    add_version(client, org, on="2026-01-01", basic="40000", reason="hire")
    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=org["hr"]).json()
    assert Decimal(run["payslips"][0]["gross"]) == Decimal("40000.00")

    add_version(client, org, on="2026-08-01", basic="46000", reason="correction")
    again = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                        headers=org["hr"]).json()
    assert Decimal(again["payslips"][0]["gross"]) == Decimal("46000.00")


@endpoint
def test_history_survives_five_revisions(client, org):
    add_version(client, org, on="2026-01-01", basic="30000", reason="hire")
    for i, amount in enumerate(["32000", "34000", "36000", "38000"], start=1):
        add_version(client, org, on=f"2026-0{i + 1}-01", basic=amount)

    versions = client.get(
        f"{API}/compensation/employees/{org['employee']['id']}/versions",
        headers=org["hr"],
    ).json()
    assert len(versions) == 5, "nothing is ever overwritten"
    assert [v["gross"] for v in versions] == [
        "38000.00", "36000.00", "34000.00", "32000.00", "30000.00",
    ]


# --- invariants --------------------------------------------------------------


@endpoint
def test_versions_never_overlap_after_arbitrary_insert_order(client, org):
    """The database cannot enforce this without btree_gist, which the app role
    cannot create — so the invariant is asserted here instead."""
    for day in ["2026-06-01", "2026-01-01", "2026-11-01", "2026-03-01", "2026-08-01"]:
        add_version(client, org, on=day, basic="40000", reason="revision")

    versions = sorted(
        client.get(
            f"{API}/compensation/employees/{org['employee']['id']}/versions",
            headers=org["hr"],
        ).json(),
        key=lambda v: v["effective_from"],
    )
    for earlier, later in zip(versions, versions[1:], strict=False):
        assert earlier["effective_to"] is not None, "only the last version stays open"
        assert date.fromisoformat(earlier["effective_to"]) < date.fromisoformat(
            later["effective_from"]
        ), f"{earlier['effective_from']} overlaps {later['effective_from']}"
    assert versions[-1]["effective_to"] is None


@endpoint
def test_timeline_has_no_gaps(client, org):
    """A gap is worse than an overlap: a period resolving to no version pays
    the employee nothing at all."""
    add_version(client, org, on="2026-01-01", basic="30000", reason="hire")
    add_version(client, org, on="2026-05-01", basic="35000")
    add_version(client, org, on="2026-09-01", basic="40000")

    versions = sorted(
        client.get(
            f"{API}/compensation/employees/{org['employee']['id']}/versions",
            headers=org["hr"],
        ).json(),
        key=lambda v: v["effective_from"],
    )
    for earlier, later in zip(versions, versions[1:], strict=False):
        assert date.fromisoformat(earlier["effective_to"]) + timedelta(days=1) == (
            date.fromisoformat(later["effective_from"])
        )


# --- security ----------------------------------------------------------------


@endpoint
def test_manager_cannot_read_compensation(client, org):
    """Salary history is salary. A manager approves work facts, never pay."""
    from app.core.auth.permissions import ROLE_PERMISSIONS

    assert "payroll.read" not in ROLE_PERMISSIONS["manager"]
    assert "payroll.write" not in ROLE_PERMISSIONS["manager"]


@endpoint
def test_other_tenant_cannot_read_versions(client, org):
    add_version(client, org, on="2026-01-01", basic="38000", reason="hire")

    other = f"comp-{uuid.uuid4().hex[:8]}"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Other", "subdomain": other,
        "email": f"admin@{other}.test", "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": other, "email": f"admin@{other}.test", "password": "pw123456",
    }).json()["access_token"]

    r = client.get(
        f"{API}/compensation/employees/{org['employee']['id']}/versions",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 404, "another tenant's employee must not even resolve"


@endpoint
def test_unknown_component_is_refused(client, org):
    r = client.post(
        f"{API}/compensation/employees/{org['employee']['id']}/versions",
        json={"effective_from": "2026-01-01",
              "lines": [{"component_id": str(uuid.uuid4()), "amount": "1000"}]},
        headers=org["hr"],
    )
    assert r.status_code == 422


@endpoint
def test_only_the_latest_version_can_be_deleted(client, org):
    first = add_version(client, org, on="2026-01-01", basic="30000", reason="hire").json()
    last = add_version(client, org, on="2026-06-01", basic="35000").json()

    early = client.delete(f"{API}/compensation/versions/{first['id']}", headers=org["hr"])
    assert early.status_code == 409, "deleting a middle version would leave a gap"

    ok = client.delete(f"{API}/compensation/versions/{last['id']}", headers=org["hr"])
    assert ok.status_code == 204

    remaining = client.get(
        f"{API}/compensation/employees/{org['employee']['id']}/versions",
        headers=org["hr"],
    ).json()
    assert len(remaining) == 1
    assert remaining[0]["effective_to"] is None, "the previous version re-opens"
