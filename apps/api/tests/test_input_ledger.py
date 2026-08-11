"""The payroll input ledger, work facts, and validation.

The pivot these test: payroll no longer asks "what is this employee paid?" but
"what was approved for this period?". The properties that make that worth
having are (a) regeneration never destroys a human's entry, (b) money is
always derived from facts and rules rather than accepted as an amount, and
(c) an unapproved fact cannot reach a payslip.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text

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
PERIOD = date(2026, 9, 1)


@pytest.fixture
def org(client):
    sub = f"led-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Ledger Co", "subdomain": sub, "email": admin,
                      "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": admin, "password": "pw123456"}).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    client.put("/api/v1/payroll/settings",
               json={"pf_enabled": True, "esi_enabled": True}, headers=hr)
    basic = client.post("/api/v1/payroll/components",
                        json={"code": "BASIC", "name": "Basic", "wage_basis": "wages",
                              "sequence": 10}, headers=hr).json()
    hra = client.post("/api/v1/payroll/components",
                      json={"code": "HRA", "name": "HRA", "wage_basis": "excluded",
                            "sequence": 20}, headers=hr).json()
    emp = client.post("/api/v1/hr/employees",
                      json={"full_name": "Ravi Kumar"}, headers=hr).json()
    client.put(f"/api/v1/payroll/employees/{emp['id']}/salary",
               json={"components": [{"component_id": basic["id"], "amount": "20000.00"},
                                    {"component_id": hra["id"], "amount": "10000.00"}]},
               headers=hr)
    return {"sub": sub, "hr": hr, "employee": emp, "basic": basic, "hra": hra}


def _session(client, org):
    """A DB session scoped to this test's tenant, for touching the ledger
    directly — there are no HTTP routes for work facts yet."""
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal

    s = get_settings()
    cid = pyjwt.decode(org["hr"]["Authorization"].split()[1], s.jwt_secret,
                       algorithms=[s.jwt_alg])["cid"]
    db = SessionLocal()

    def arm() -> None:
        db.execute(text("SELECT set_config('app.company_id', :c, true)"), {"c": cid})

    arm()
    # `SET LOCAL` is transaction-scoped, so committing drops it and the next
    # query tries to cast '' to uuid — which errors loudly rather than
    # silently returning nothing. Re-arm after every commit.
    original_commit = db.commit

    def commit_and_rearm() -> None:
        original_commit()
        arm()

    db.commit = commit_and_rearm  # type: ignore[method-assign]
    return db, uuid.UUID(cid)


def _run(client, org, period="2026-09-01"):
    r = client.post("/api/v1/payroll/runs", json={"period": period}, headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


# --- the ledger is the source ----------------------------------------------


def test_a_run_generates_the_ledger_from_the_salary_structure(client, org):
    from app.modules.payroll import ledger

    _run(client, org)
    db, _ = _session(client, org)
    rows = ledger.inputs_for(db, uuid.UUID(org["employee"]["id"]), PERIOD)
    db.close()

    assert {r.code for r in rows} == {"BASIC", "HRA"}
    assert all(r.source == "structure" for r in rows)
    assert sum(r.amount for r in rows) == D("30000.00")


def test_regenerating_keeps_a_manual_input_and_replaces_the_derived_ones(client, org):
    """The asymmetry the whole design rests on. Recomputing must not destroy
    what a person entered, and a person must not have to re-enter what the
    system derives."""
    from app.modules.payroll import ledger
    from app.modules.payroll.workforce import PayrollInput

    _run(client, org)
    db, cid = _session(client, org)
    emp_id = uuid.UUID(org["employee"]["id"])

    db.add(PayrollInput(
        company_id=cid, employee_id=emp_id, period=PERIOD, kind="earning",
        code="BONUS", name="Festival bonus", amount=D("5000.00"),
        wage_basis="excluded", source="manual", reason="Diwali",
        approved_at=datetime.now(tz=None).astimezone(), sequence=300,
    ))
    db.commit()
    db.close()

    _run(client, org)  # recompute

    db, _ = _session(client, org)
    rows = ledger.inputs_for(db, emp_id, PERIOD)
    structure = [r for r in rows if r.source == "structure"]
    manual = [r for r in rows if r.source == "manual"]
    db.close()

    assert len(manual) == 1 and manual[0].code == "BONUS"
    assert len(structure) == 2, "structure rows should be regenerated, not duplicated"


def test_a_manual_input_reaches_the_payslip(client, org):
    from app.modules.payroll.workforce import PayrollInput

    db, cid = _session(client, org)
    db.add(PayrollInput(
        company_id=cid, employee_id=uuid.UUID(org["employee"]["id"]), period=PERIOD,
        kind="earning", code="BONUS", name="Festival bonus", amount=D("5000.00"),
        wage_basis="excluded", source="manual",
        approved_at=datetime.now(tz=None).astimezone(), sequence=300,
    ))
    db.commit()
    db.close()

    slip = _run(client, org)["payslips"][0]
    codes = {line["code"] for line in slip["breakdown"]["earnings"]}
    assert "BONUS" in codes
    assert D(slip["gross"]) == D("35000.00")


def test_an_unapproved_input_is_not_paid(client, org):
    """An input nobody signed off is a claim, not a cost."""
    from app.modules.payroll.workforce import PayrollInput

    db, cid = _session(client, org)
    db.add(PayrollInput(
        company_id=cid, employee_id=uuid.UUID(org["employee"]["id"]), period=PERIOD,
        kind="earning", code="CLAIM", name="Unapproved claim", amount=D("9000.00"),
        wage_basis="excluded", source="manual", sequence=300,
    ))
    db.commit()
    db.close()

    slip = _run(client, org)["payslips"][0]
    assert "CLAIM" not in {line["code"] for line in slip["breakdown"]["earnings"]}
    assert D(slip["gross"]) == D("30000.00")


# --- work facts become money through a rule, never as an amount -------------


def _fact(db, cid, emp_id, day, **kw):
    from app.modules.payroll.workforce import WorkFact

    f = WorkFact(company_id=cid, employee_id=emp_id, day=day, status="worked", **kw)
    db.add(f)
    return f


def test_approved_overtime_becomes_a_derived_input(client, org):
    """The engine takes HOURS and applies a multiplier. It never accepts an
    overtime amount, which is what makes the policy replayable."""
    from app.modules.payroll import ledger

    db, cid = _session(client, org)
    emp_id = uuid.UUID(org["employee"]["id"])
    for d in (1, 2, 3):
        _fact(db, cid, emp_id, date(2026, 9, d), hours_worked=D("10"),
              overtime_hours=D("2"), approved_at=datetime.now(tz=None).astimezone())
    db.commit()
    db.close()

    slip = _run(client, org)["payslips"][0]
    ot = next((line for line in slip["breakdown"]["earnings"] if line["code"] == "OT"), None)
    assert ot is not None, "approved overtime should be paid"
    assert D(ot["amount"]) > D("0")

    db, _ = _session(client, org)
    row = next(r for r in ledger.inputs_for(db, emp_id, PERIOD) if r.code == "OT")
    db.close()
    assert row.quantity == D("6.00")          # 3 days x 2 hours
    assert row.source == "work_facts"
    # 20,000 ordinary wage / (26 working days x 8h) x 2.0 multiplier x 6h
    assert row.amount == D(ot["amount"])


def test_unapproved_overtime_is_not_paid_but_is_reported(client, org):
    from app.modules.payroll import validation

    db, cid = _session(client, org)
    emp_id = uuid.UUID(org["employee"]["id"])
    _fact(db, cid, emp_id, date(2026, 9, 4), hours_worked=D("12"), overtime_hours=D("4"))
    db.commit()

    slip = _run(client, org)["payslips"][0]
    assert not any(line["code"] == "OT" for line in slip["breakdown"]["earnings"])

    db2, company = _session(client, org)
    findings = validation.validate(db2, company_id=company, period=PERIOD)
    db2.close()
    db.close()
    codes = {f.code for f in findings}
    assert "overtime_unapproved" in codes


def test_overtime_counts_toward_the_fifty_percent_wage_test(client, org):
    """The Code is explicit that overtime forms part of the calculation. It is
    not itself wages, but it enlarges the remuneration the test measures
    against — so it must appear in the denominator."""
    from app.modules.payroll import ledger

    db, cid = _session(client, org)
    emp_id = uuid.UUID(org["employee"]["id"])
    for d in range(1, 11):
        _fact(db, cid, emp_id, date(2026, 9, d), hours_worked=D("12"),
              overtime_hours=D("4"), approved_at=datetime.now(tz=None).astimezone())
    db.commit()
    db.close()

    _run(client, org)
    db, _ = _session(client, org)
    rows = ledger.inputs_for(db, emp_id, PERIOD)
    basis = ledger.statutory_wage_from_inputs(rows, PERIOD)
    db.close()

    ot = next(r for r in rows if r.code == "OT")
    assert ot.wage_basis == "excluded"
    assert basis.remuneration == D("30000.00") + ot.amount


def test_premium_day_work_is_its_own_fact(client, org):
    db, cid = _session(client, org)
    emp_id = uuid.UUID(org["employee"]["id"])
    _fact(db, cid, emp_id, date(2026, 9, 6), hours_worked=D("8"), premium_day=True,
          approved_at=datetime.now(tz=None).astimezone())
    db.commit()
    db.close()

    slip = _run(client, org)["payslips"][0]
    prem = next((line for line in slip["breakdown"]["earnings"]
                 if line["code"] == "PREMIUM"), None)
    assert prem is not None and D(prem["amount"]) > D("0")


# --- validation is separate from readiness and from risk --------------------


def test_an_employee_with_no_inputs_is_blocked_and_named(client, org):
    """Blocking means the payslip would be wrong, not merely surprising — so
    they are excluded from the run rather than paid an indefensible number."""
    from app.modules.payroll import validation

    nobody = client.post("/api/v1/hr/employees",
                         json={"full_name": "No Structure"}, headers=org["hr"]).json()
    run = _run(client, org)
    assert all(p["employee_id"] != nobody["id"] for p in run["payslips"])

    db, cid = _session(client, org)
    findings = validation.validate(db, company_id=cid, period=PERIOD)
    db.close()
    blocking = [f for f in findings if f.severity == validation.BLOCKING]
    assert any(f.employee_name == "No Structure" for f in blocking)
    assert uuid.UUID(nobody["id"]) in validation.blocking_employee_ids(findings)


def test_a_working_employee_produces_no_blocking_findings(client, org):
    from app.modules.payroll import validation

    _run(client, org)
    db, cid = _session(client, org)
    findings = validation.validate(db, company_id=cid, period=PERIOD)
    db.close()
    mine = [f for f in findings if f.employee_name == "Ravi Kumar"]
    assert not [f for f in mine if f.severity == validation.BLOCKING]


def test_pay_below_the_configured_floor_warns_and_never_adjusts(client, org):
    """Minimum wage is a warning. The system knows the floor it was told, not
    whether that floor is the right one for this worker's skill grade."""
    from app.modules.payroll import validation
    from app.modules.payroll.workforce import Establishment

    db, cid = _session(client, org)
    emp_id = uuid.UUID(org["employee"]["id"])
    # 30,000 over 26 worked days is about 1,154/day, so a 1,500 floor bites.
    db.add(Establishment(company_id=cid, name="Pune Site", state_code="MH",
                         minimum_daily_wage=D("1500.00"), is_default=True))
    for d in range(1, 27):
        _fact(db, cid, emp_id, date(2026, 9, d), hours_worked=D("8"),
              approved_at=datetime.now(tz=None).astimezone())
    db.commit()

    run = _run(client, org)
    slip = next(p for p in run["payslips"] if p["employee_id"] == org["employee"]["id"])
    gross_before = D(slip["gross"])

    db2, company = _session(client, org)
    findings = validation.validate(db2, company_id=company, period=PERIOD)
    db2.close()
    db.close()

    low = [f for f in findings if f.code == "below_minimum_wage"]
    assert low, "pay below the configured floor should be flagged"
    assert all(f.severity == validation.WARNING for f in low)
    assert low[0].impact is not None and low[0].impact > D("0")
    # Nothing was topped up.
    assert D(_run(client, org)["payslips"][0]["gross"]) == gross_before


def test_the_three_questions_are_answered_separately(client, org):
    """Readiness, validation and risk are not one number. A run can be
    perfectly valid and still carry risk worth a human glance."""
    from app.modules.payroll import validation

    _run(client, org)
    db, cid = _session(client, org)
    findings = validation.validate(db, company_id=cid, period=PERIOD)
    summary = validation.summarise(findings)
    risk = validation.risk(db, company_id=cid, period=PERIOD)
    db.close()

    assert set(summary) == {"blocking", "warnings", "info", "impact", "groups"}
    assert isinstance(risk, list)          # risk is its own list, not folded in
    assert all(f.severity != validation.BLOCKING for f in risk)


def test_findings_carry_money_at_stake_where_it_can_be_estimated(client, org):
    """An operator triages by consequence, not by count."""
    from app.modules.payroll import validation

    db, cid = _session(client, org)
    findings = validation.validate(db, company_id=cid, period=PERIOD)
    summary = validation.summarise(findings)
    db.close()
    assert summary["impact"] >= D("0")
    for group in summary["groups"]:
        assert {"code", "severity", "count", "impact"} <= set(group)


# --- jurisdiction -----------------------------------------------------------


def test_professional_tax_can_be_scoped_to_an_establishment(client, org):
    """A company is not one state. PT slabs carry an establishment, and a NULL
    one remains a company-wide schedule for single-state customers."""
    from app.modules.payroll.models import ProfessionalTaxSlab
    from app.modules.payroll.workforce import Establishment

    db, cid = _session(client, org)
    mh = Establishment(company_id=cid, name="Mumbai", state_code="MH")
    dl = Establishment(company_id=cid, name="Delhi", state_code="DL")
    db.add_all([mh, dl])
    db.flush()
    db.add(ProfessionalTaxSlab(company_id=cid, establishment_id=mh.id,
                               up_to=None, amount=D("200")))
    db.commit()

    slabs = db.scalars(
        select(ProfessionalTaxSlab).where(ProfessionalTaxSlab.deleted_at.is_(None))
    ).all()
    scoped = [s for s in slabs if s.establishment_id == mh.id]
    delhi = [s for s in slabs if s.establishment_id == dl.id]
    db.close()

    assert len(scoped) == 1
    assert delhi == [], "Delhi does not levy professional tax"
