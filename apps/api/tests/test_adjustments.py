"""Correcting a finalized period.

Every "corrections belong in a later period" refusal elsewhere in the product
points at this flow. These test that it is a genuine redirection rather than a
dead end, and that it cannot be used to edit a closed month by another name.
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
OCT = "2026-10-01"


@pytest.fixture
def org(client):
    """August finalized and September open — the state a correction needs."""
    sub = f"adj-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Adj Co", "subdomain": sub, "email": admin,
                      "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": admin, "password": "pw123456"}).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    basic = client.post("/api/v1/payroll/components",
                        json={"code": "BASIC", "name": "Basic", "wage_basis": "wages"},
                        headers=hr).json()
    emp = client.post("/api/v1/hr/employees", json={"full_name": "Ravi Kumar"},
                      headers=hr).json()
    client.put(f"/api/v1/payroll/employees/{emp['id']}/salary",
               json={"components": [{"component_id": basic["id"], "amount": "42000.00"}]},
               headers=hr)

    aug = client.post("/api/v1/payroll/runs", json={"period": AUG}, headers=hr).json()
    client.post(f"/api/v1/payroll/runs/{aug['id']}/finalize", headers=hr)
    return {"sub": sub, "hr": hr, "employee": emp, "basic": basic}


def _raise(client, org, **kw):
    body = {"employee_id": org["employee"]["id"], "source_period": AUG,
            "target_period": SEP, "kind": "arrear", "amount": "2400.00",
            "reason": "2 days unpaid deducted in error", **kw}
    return client.post("/api/v1/payroll/adjustments", json=body, headers=org["hr"])


def _run(client, org, period=SEP):
    r = client.post("/api/v1/payroll/runs", json={"period": period}, headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


def _slip(client, org, period=SEP):
    return _run(client, org, period)["payslips"][0]


# --- the happy path ---------------------------------------------------------


def test_an_approved_arrear_appears_on_the_next_payslip(client, org):
    """The whole flow, end to end: April's mistake settled in May."""
    adj = _raise(client, org)
    assert adj.status_code == 200, adj.text
    assert adj.json()["applied_input_id"] is None, "raising it moves no money"

    before = _slip(client, org)
    assert D(before["gross"]) == D("42000.00")

    approved = client.post(
        f"/api/v1/payroll/adjustments/{adj.json()['id']}/approve", headers=org["hr"]
    ).json()
    assert approved["applied_input_id"] is not None

    after = _slip(client, org)
    assert D(after["gross"]) == D("44400.00")
    line = next(x for x in after["breakdown"]["earnings"] if x["code"].startswith("ADJ-"))
    assert line["name"] == "August 2026 arrear"
    assert line["source"] == "adjustment"


def test_a_recovery_deducts_instead(client, org):
    adj = _raise(client, org, kind="recovery", amount="1500.00",
                 reason="overpaid two days in August").json()
    client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])

    slip = _slip(client, org)
    assert D(slip["gross"]) == D("42000.00"), "a recovery is not negative earnings"
    line = next(x for x in slip["breakdown"]["deductions"] if x["code"].startswith("ADJ-"))
    assert line["name"] == "August 2026 recovery"
    assert D(slip["net"]) == D(slip["gross"]) - D(slip["deductions"])


def test_the_line_names_the_month_it_came_from(client, org):
    """"arrear 2,400" with no month is a figure nobody can place."""
    adj = _raise(client, org).json()
    assert adj["name"] == "August 2026 arrear"
    assert adj["code"] == "ADJ-202608"


def test_an_arrear_survives_a_recompute(client, org):
    """It is a correction, not a derived figure — regenerating the ledger must
    not wash it away."""
    adj = _raise(client, org).json()
    client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])
    assert D(_slip(client, org)["gross"]) == D("44400.00")

    client.post("/api/v1/payroll/inputs/rebuild", json={"period": SEP}, headers=org["hr"])
    assert D(_slip(client, org)["gross"]) == D("44400.00")


def test_two_corrections_from_different_months_coexist(client, org):
    """Their codes carry the source month, so they cannot collide on the
    ledger's one-row-per-code slot."""
    first = _raise(client, org).json()
    client.post(f"/api/v1/payroll/adjustments/{first['id']}/approve", headers=org["hr"])

    sep = _run(client, org)
    client.post(f"/api/v1/payroll/runs/{sep['id']}/finalize", headers=org["hr"])

    second = _raise(client, org, source_period=SEP, target_period=OCT,
                    amount="900.00", reason="shift allowance missed").json()
    assert second["code"] == "ADJ-202609"
    client.post(f"/api/v1/payroll/adjustments/{second['id']}/approve", headers=org["hr"])

    oct_slip = _slip(client, org, OCT)
    codes = {x["code"] for x in oct_slip["breakdown"]["earnings"]}
    assert "ADJ-202609" in codes


# --- it cannot become an edit by another name -------------------------------


def test_correcting_an_open_period_is_refused(client, org):
    """If the month is still open, change it there. An adjustment against a
    draft would be a second way to do the same thing, with worse lineage."""
    r = _raise(client, org, source_period=SEP, target_period=OCT)
    assert r.status_code == 422
    assert "not finalized" in r.text


def test_targeting_a_finalized_period_is_refused(client, org):
    sep = _run(client, org)
    client.post(f"/api/v1/payroll/runs/{sep['id']}/finalize", headers=org["hr"])
    r = _raise(client, org)
    assert r.status_code == 422
    assert "already finalized" in r.text


def test_the_correction_must_land_after_the_month_it_corrects(client, org):
    """Backdating one into an earlier month would rewrite history through the
    front door."""
    r = _raise(client, org, source_period=AUG, target_period=AUG)
    assert r.status_code == 422
    assert "AFTER" in r.text


def test_a_target_finalized_after_raising_is_caught_at_approval(client, org):
    """The gap that matters: open when raised, closed by the time somebody
    signs it. Paying into a shut month would silently do nothing."""
    adj = _raise(client, org).json()
    sep = _run(client, org)
    client.post(f"/api/v1/payroll/runs/{sep['id']}/finalize", headers=org["hr"])

    r = client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])
    assert r.status_code == 422
    assert "already finalized" in r.text


def test_a_reason_is_required(client, org):
    r = _raise(client, org, reason="")
    assert r.status_code == 422


def test_the_amount_is_a_magnitude_not_a_direction(client, org):
    assert _raise(client, org, amount="-2400.00").status_code == 422
    assert _raise(client, org, amount="0.00").status_code == 422


def test_an_unknown_kind_is_refused(client, org):
    assert _raise(client, org, kind="whatever").status_code == 422


def test_approving_twice_is_refused(client, org):
    adj = _raise(client, org).json()
    assert client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve",
                       headers=org["hr"]).status_code == 200
    assert client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve",
                       headers=org["hr"]).status_code == 422


# --- withdrawing one --------------------------------------------------------


def test_cancelling_takes_the_money_back_too(client, org):
    """Leaving the ledger row behind would keep paying a correction somebody
    has retracted."""
    adj = _raise(client, org).json()
    client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])
    assert D(_slip(client, org)["gross"]) == D("44400.00")

    assert client.delete(f"/api/v1/payroll/adjustments/{adj['id']}",
                         headers=org["hr"]).status_code == 204
    assert D(_slip(client, org)["gross"]) == D("42000.00")
    assert client.get("/api/v1/payroll/adjustments", headers=org["hr"]).json() == []


def test_cancelling_after_it_has_been_paid_is_refused(client, org):
    """Once the target month is finalized the money went out. The remedy is
    another adjustment, not an erasure."""
    adj = _raise(client, org).json()
    client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])
    sep = _run(client, org)
    client.post(f"/api/v1/payroll/runs/{sep['id']}/finalize", headers=org["hr"])

    r = client.delete(f"/api/v1/payroll/adjustments/{adj['id']}", headers=org["hr"])
    assert r.status_code == 409
    assert "raise a further adjustment" in r.text


# --- lineage and reporting ---------------------------------------------------


def test_both_months_stay_explicable(client, org):
    """"Why is September 2,400 higher" is answered by pointing at August."""
    adj = _raise(client, org).json()
    client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])

    listed = client.get(f"/api/v1/payroll/adjustments?target_period={SEP}",
                        headers=org["hr"]).json()
    assert len(listed) == 1
    assert listed[0]["source_period"] == AUG
    assert listed[0]["reason"] == "2 days unpaid deducted in error"


def test_august_is_untouched_by_its_own_correction(client, org):
    """The point of the whole exercise."""
    aug_before = client.get("/api/v1/payroll/runs", headers=org["hr"]).json()
    aug_net = next(r for r in aug_before if r["period"] == AUG)["net_total"]

    adj = _raise(client, org).json()
    client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])
    _run(client, org)

    aug_after = client.get("/api/v1/payroll/runs", headers=org["hr"]).json()
    assert next(r for r in aug_after if r["period"] == AUG)["net_total"] == aug_net


def test_an_arrear_does_not_inflate_this_months_statutory_wage(client, org):
    """It is pay for an earlier month. Counting it as September wages would
    raise September's PF basis on money that was not September's."""
    slip_before = _slip(client, org)
    wage_before = slip_before["breakdown"]["basis"]["statutory_wage"]

    adj = _raise(client, org).json()
    client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])

    slip_after = _slip(client, org)
    assert slip_after["breakdown"]["basis"]["statutory_wage"] == wage_before
    assert D(slip_after["gross"]) > D(slip_before["gross"])


def test_the_movement_bridge_accounts_for_an_adjustment(client, org):
    adj = _raise(client, org).json()
    client.post(f"/api/v1/payroll/adjustments/{adj['id']}/approve", headers=org["hr"])
    _run(client, org)

    m = client.get(f"/api/v1/payroll/movement?period={SEP}", headers=org["hr"]).json()
    bridge = {b["code"]: D(b["amount"]) for b in m["bridge"]}
    assert bridge["entered"] == D("2400.00")
    assert D(m["unexplained"]) == D("0")


def test_only_payroll_can_raise_or_approve(client, org):
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

    assert client.get("/api/v1/payroll/adjustments", headers=mgr).status_code == 403
    assert client.post("/api/v1/payroll/adjustments", headers=mgr, json={
        "employee_id": org["employee"]["id"], "source_period": AUG,
        "target_period": SEP, "kind": "arrear", "amount": "100.00",
        "reason": "nope"}).status_code == 403
