"""Contractor reconciliation, and the blue-collar work-fact path behind it.

The variance between what attendance says a contractor is owed and what they
billed is the entire point. Billing for days nobody worked is the most common
leak in site payroll, and it is invisible until those two figures sit next to
each other — so these tests are mostly about that gap being visible and
enforced.
"""
import uuid
from datetime import date, datetime
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
PERIOD = "2026-09-01"


@pytest.fixture
def org(client):
    sub = f"con-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Site Co", "subdomain": sub, "email": admin,
                      "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": admin, "password": "pw123456"}).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    basic = client.post("/api/v1/payroll/components",
                        json={"code": "BASIC", "name": "Basic", "wage_basis": "wages"},
                        headers=hr).json()
    con = client.post("/api/v1/payroll/contractors",
                      json={"name": "ABC Services", "code": "ABC",
                            "licence_number": "CLRA/MH/2019/0042"}, headers=hr).json()
    return {"sub": sub, "hr": hr, "basic": basic, "contractor": con}


def _session(client, org):
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal

    s = get_settings()
    cid = pyjwt.decode(org["hr"]["Authorization"].split()[1], s.jwt_secret,
                       algorithms=[s.jwt_alg])["cid"]
    db = SessionLocal()

    def arm():
        db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})

    arm()
    original = db.commit

    def commit_and_rearm():
        original()
        arm()

    db.commit = commit_and_rearm  # type: ignore[method-assign]
    return db, uuid.UUID(cid)


def _worker(client, org, name, amount, deployed=True):
    body = {"full_name": name, "worker_type": "blue_collar"}
    if deployed:
        body["contractor_id"] = org["contractor"]["id"]
    e = client.post("/api/v1/hr/employees", json=body, headers=org["hr"]).json()
    if amount:
        client.put(f"/api/v1/payroll/employees/{e['id']}/salary",
                   json={"components": [{"component_id": org["basic"]["id"],
                                         "amount": amount}]}, headers=org["hr"])
    return e


def _facts(client, org, employee_id, days, *, approved=True, site="Pune Site A", ot="0"):
    db, cid = _session(client, org)
    from app.modules.payroll.workforce import WorkFact

    for d in days:
        db.add(WorkFact(
            company_id=cid, employee_id=uuid.UUID(employee_id), day=date(2026, 9, d),
            status="worked", hours_worked=D("8"), overtime_hours=D(ot), site=site,
            shift="08:00-18:00",
            approved_at=datetime.now(tz=None).astimezone() if approved else None,
        ))
    db.commit()
    db.close()


def _run(client, org):
    r = client.post("/api/v1/payroll/runs", json={"period": PERIOD}, headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


def _recon(client, org):
    r = client.get(
        f"/api/v1/payroll/contractors/{org['contractor']['id']}/reconciliation"
        f"?period={PERIOD}", headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


def _invoice(client, org, amount, **kw):
    return client.post("/api/v1/payroll/contractors/invoices", headers=org["hr"], json={
        "contractor_id": org["contractor"]["id"], "period": PERIOD,
        "amount": amount, **kw})


# --- the variance -----------------------------------------------------------


def test_an_invoice_matching_attendance_has_no_variance(client, org):
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _worker(client, org, "Suresh Yadav", "18000.00")
    _run(client, org)

    computed = _recon(client, org)["computed"]
    assert D(computed) == D("38000.00")

    _invoice(client, org, computed)
    r = _recon(client, org)
    assert D(r["variance"]) == D("0")
    assert r["workers"] == 2


def test_billing_above_attendance_shows_the_gap(client, org):
    """The leak this exists to catch."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    _invoice(client, org, "23100.00")

    r = _recon(client, org)
    assert D(r["computed"]) == D("20000.00")
    assert D(r["invoiced"]) == D("23100.00")
    assert D(r["variance"]) == D("3100.00")


def test_billing_below_attendance_is_a_negative_variance(client, org):
    """Under-billing matters too — it usually means somebody's days were
    missed, and the worker is the one who loses."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    _invoice(client, org, "18000.00")
    assert D(_recon(client, org)["variance"]) == D("-2000.00")


def test_no_invoice_is_null_not_zero(client, org):
    """Zero would read as "they invoiced nothing", which is a different and
    much worse claim than "nothing has been billed yet"."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)

    r = _recon(client, org)
    assert r["invoiced"] is None
    assert r["variance"] is None
    assert D(r["computed"]) == D("20000.00")


# --- the detail behind the gap ----------------------------------------------


def test_the_breakdown_names_the_workers_and_their_days(client, org):
    """What somebody actually takes back to the contractor."""
    ramesh = _worker(client, org, "Ramesh Kumar", "20000.00")
    _facts(client, org, ramesh["id"], [1, 2, 3, 4, 5], ot="2")
    _run(client, org)

    line = next(ln for ln in _recon(client, org)["lines"] if ln["name"] == "Ramesh Kumar")
    assert line["days_approved"] == 5
    assert D(line["overtime_hours"]) == D("10.00")
    assert line["site"] == "Pune Site A"
    assert line["has_payslip"] is True


def test_a_deployed_worker_with_no_pay_is_flagged(client, org):
    """Deployed but unpaid — usually a missing salary structure, and always
    worth naming before an invoice is agreed."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _worker(client, org, "Unpaid Worker", None)
    _run(client, org)

    r = _recon(client, org)
    assert r["workers_without_pay"] == 1
    assert next(ln for ln in r["lines"] if ln["name"] == "Unpaid Worker")["has_payslip"] is False


def test_unapproved_days_are_counted_separately(client, org):
    """They are not in the computed figure, so an invoice covering them looks
    like a variance until somebody signs the work off."""
    ramesh = _worker(client, org, "Ramesh Kumar", "20000.00")
    _facts(client, org, ramesh["id"], [1, 2], approved=True)
    _facts(client, org, ramesh["id"], [8, 9], approved=False)
    _run(client, org)

    r = _recon(client, org)
    assert r["days_awaiting_approval"] == 2
    line = r["lines"][0]
    assert line["days_approved"] == 2 and line["days_pending"] == 2


def test_only_this_contractors_workers_are_counted(client, org):
    """A direct employee is not a contractor's cost."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _worker(client, org, "Direct Employee", "50000.00", deployed=False)
    _run(client, org)

    r = _recon(client, org)
    assert r["workers"] == 1
    assert D(r["computed"]) == D("20000.00")
    assert [ln["name"] for ln in r["lines"]] == ["Ramesh Kumar"]


# --- recording versus agreeing ----------------------------------------------


def test_a_disputed_invoice_can_still_be_recorded(client, org):
    """Refusing to record it would leave the disagreement nowhere, which is
    the opposite of what the reconciliation is for."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    r = _invoice(client, org, "99000.00")
    assert r.status_code == 200
    assert r.json()["status"] == "received"


def test_approving_an_invoice_that_disagrees_is_refused(client, org):
    """Approving past a variance silently would make the comparison
    decorative."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    inv = _invoice(client, org, "23100.00").json()

    r = client.post(f"/api/v1/payroll/contractors/invoices/{inv['id']}/approve",
                    headers=org["hr"])
    assert r.status_code == 409
    assert "differs from attendance" in r.text


def test_a_matching_invoice_approves(client, org):
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    inv = _invoice(client, org, "20000.00", reference="ABC/2026/09").json()

    approved = client.post(f"/api/v1/payroll/contractors/invoices/{inv['id']}/approve",
                           headers=org["hr"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_at"] is not None
    assert _recon(client, org)["invoice_status"] == "approved"


def test_a_corrected_invoice_supersedes_rather_than_accompanies(client, org):
    """One invoice per contractor per period, or the variance has no single
    answer."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    first = _invoice(client, org, "23100.00").json()
    second = _invoice(client, org, "20000.00").json()

    assert first["id"] == second["id"]
    assert D(_recon(client, org)["invoiced"]) == D("20000.00")


def test_an_approved_invoice_cannot_be_quietly_edited(client, org):
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    inv = _invoice(client, org, "20000.00").json()
    client.post(f"/api/v1/payroll/contractors/invoices/{inv['id']}/approve",
                headers=org["hr"])

    r = _invoice(client, org, "25000.00")
    assert r.status_code == 409
    assert "credit note" in r.text


def test_disputing_keeps_the_figure_on_record(client, org):
    """Deleting it would destroy the evidence of what was claimed."""
    _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    inv = _invoice(client, org, "23100.00").json()

    disputed = client.post(f"/api/v1/payroll/contractors/invoices/{inv['id']}/dispute",
                           headers=org["hr"]).json()
    assert disputed["status"] == "disputed"
    assert D(_recon(client, org)["invoiced"]) == D("23100.00")


# --- the summary view --------------------------------------------------------


def test_the_summary_puts_the_worst_disagreement_first(client, org):
    """Triage by the size of the gap, not alphabetically."""
    quiet = client.post("/api/v1/payroll/contractors", json={"name": "Zeta Quiet"},
                        headers=org["hr"]).json()
    _worker(client, org, "Ramesh Kumar", "20000.00")

    z = client.post("/api/v1/hr/employees",
                    json={"full_name": "Zeta Worker", "worker_type": "blue_collar",
                          "contractor_id": quiet["id"]}, headers=org["hr"]).json()
    client.put(f"/api/v1/payroll/employees/{z['id']}/salary",
               json={"components": [{"component_id": org["basic"]["id"],
                                     "amount": "10000.00"}]}, headers=org["hr"])
    _run(client, org)

    _invoice(client, org, "40000.00")                       # ABC: +20,000
    client.post("/api/v1/payroll/contractors/invoices", headers=org["hr"], json={
        "contractor_id": quiet["id"], "period": PERIOD, "amount": "10100.00"})  # +100

    rows = client.get(f"/api/v1/payroll/contractors/reconciliation?period={PERIOD}",
                      headers=org["hr"]).json()
    assert [r["contractor_name"] for r in rows] == ["ABC Services", "Zeta Quiet"]


# --- blue collar -------------------------------------------------------------


def test_worker_type_is_recorded_and_validated(client, org):
    w = _worker(client, org, "Ramesh Kumar", "20000.00")
    assert w["worker_type"] == "blue_collar"
    assert w["contractor_id"] == org["contractor"]["id"]

    r = client.post("/api/v1/hr/employees",
                    json={"full_name": "Nope", "worker_type": "gig"}, headers=org["hr"])
    assert r.status_code == 422


def test_employees_default_to_white_collar(client, org):
    """The overwhelmingly common case stays the default, so nothing changes
    for a company that has no site workers at all."""
    e = client.post("/api/v1/hr/employees", json={"full_name": "Priya Shah"},
                    headers=org["hr"]).json()
    assert e["worker_type"] == "white_collar"
    assert e["contractor_id"] is None


def test_a_worker_can_be_moved_between_contractors(client, org):
    other = client.post("/api/v1/payroll/contractors", json={"name": "Sunrise Manpower"},
                        headers=org["hr"]).json()
    w = _worker(client, org, "Ramesh Kumar", "20000.00")
    _run(client, org)
    assert _recon(client, org)["workers"] == 1

    moved = client.patch(f"/api/v1/hr/employees/{w['id']}",
                         json={"contractor_id": other["id"]}, headers=org["hr"])
    assert moved.status_code == 200
    assert moved.json()["worker_type"] == "blue_collar", "PATCH left the rest alone"
    assert _recon(client, org)["workers"] == 0


def test_only_payroll_can_see_contractor_spend(client, org):
    """Contractor cost is finance data. A manager approves the work behind it
    and still cannot see what it costs."""
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

    for path in (
        "/api/v1/payroll/contractors",
        f"/api/v1/payroll/contractors/reconciliation?period={PERIOD}",
        f"/api/v1/payroll/contractors/{org['contractor']['id']}/reconciliation?period={PERIOD}",
    ):
        assert client.get(path, headers=mgr).status_code == 403, path
