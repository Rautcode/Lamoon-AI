"""Payroll.

Two halves. The first is pure statutory arithmetic with numbers you can check
against a real payslip by hand — that's the only kind of test worth having for
money. The second is the run lifecycle and the permission boundary, which for
salary data is the tightest one in the product.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.modules.payroll.rules import epf_rule_for, esi_rule_for
from app.modules.payroll.statutory import (
    contribution_period_start,
    esi,
    professional_tax,
    provident_fund,
    rupees,
)

D = Decimal

#: Rates are effective-dated now (rules.py). These tests assert the ARITHMETIC,
#: so they pin a rule explicitly rather than tracking whatever is current.
EPF = epf_rule_for(date(2026, 1, 1))
ESI = esi_rule_for(date(2026, 1, 1))


# --- statutory arithmetic (no database) -------------------------------------


def test_pf_on_a_wage_below_the_ceiling():
    """₹12,000 basic: 12% each side = ₹1,440. Employer's 8.33% pension is
    ₹999.60 -> ₹1,000, leaving ₹440 in EPF."""
    pf = provident_fund(D("12000"), ceiling=D("15000"), rule=EPF)
    assert pf["employee"] == D("1440")
    assert pf["employer_eps"] == D("1000")
    assert pf["employer_epf"] == D("440")
    assert pf["employer_eps"] + pf["employer_epf"] == pf["employee"]


def test_pf_caps_at_the_wage_ceiling_by_default():
    """₹50,000 basic, capped at ₹15,000 -> ₹1,800 (not ₹6,000)."""
    pf = provident_fund(D("50000"), ceiling=D("15000"), rule=EPF)
    assert pf["employee"] == D("1800")
    assert pf["wage"] == D("15000.00")


def test_pf_on_full_wage_when_the_employer_opted_in():
    pf = provident_fund(D("50000"), ceiling=D("15000"), rule=EPF, on_full_wage=True)
    assert pf["employee"] == D("6000")
    # Pension stays capped on ₹15,000 whatever the employer contributes on:
    # 8.33% of 15000 = ₹1,249.50 -> ₹1,250, the statutory EPS maximum.
    assert pf["employer_eps"] == D("1250")
    assert pf["employer_epf"] == D("4750")


def test_employer_and_employee_pf_always_reconcile():
    """Rounding each share separately would let the two sides drift apart."""
    for wage in ("7777", "13333.33", "15000", "9999.99", "1"):
        pf = provident_fund(D(wage), ceiling=D("15000"), rule=EPF)
        assert pf["employer_eps"] + pf["employer_epf"] == pf["employee"], wage


def test_no_pf_wage_means_no_contribution():
    pf = provident_fund(D("0"), ceiling=D("15000"), rule=EPF)
    assert pf == {"employee": D("0"), "employer_epf": D("0"), "employer_eps": D("0"),
                  "employer_edli": D("0"), "employer_admin": D("0"), "wage": D("0")}


def test_esi_applies_below_the_ceiling_and_rounds_up():
    """₹18,000 gross: 0.75% = ₹135 exactly, 3.25% = ₹585."""
    amounts = esi(D("18000"), ceiling=D("21000"), rule=ESI)
    assert amounts["employee"] == D("135")
    assert amounts["employer"] == D("585")


def test_esi_rounds_up_not_half_up():
    """ESIC regulation 40 rounds to the NEXT rupee. 0.75% of 10,001 is
    ₹75.0075 — half-up would give ₹75, ESI wants ₹76."""
    assert esi(D("10001"), ceiling=D("21000"), rule=ESI)["employee"] == D("76")


def test_esi_stops_above_the_ceiling():
    assert esi(D("25000"), ceiling=D("21000"), rule=ESI)["employee"] == D("0")


def test_esi_continues_to_period_end_after_a_mid_period_raise():
    """The rule that catches employers out: crossing the ceiling mid-period
    does NOT stop contributions until the period ends."""
    assert esi(D("25000"), ceiling=D("21000"), rule=ESI, locked_in=True)["employee"] == D("188")


def test_esi_contribution_periods():
    assert contribution_period_start(date(2026, 4, 1)) == date(2026, 4, 1)
    assert contribution_period_start(date(2026, 9, 30)) == date(2026, 4, 1)
    assert contribution_period_start(date(2026, 10, 1)) == date(2026, 10, 1)
    # January belongs to the period that began the PREVIOUS October.
    assert contribution_period_start(date(2027, 1, 15)) == date(2026, 10, 1)
    assert contribution_period_start(date(2027, 3, 31)) == date(2026, 10, 1)


def test_professional_tax_picks_the_first_matching_slab():
    """Maharashtra's schedule: nil to ₹7,500, ₹175 to ₹10,000, ₹200 above."""
    slabs = [(D("7500"), D("0")), (D("10000"), D("175")), (None, D("200"))]
    assert professional_tax(D("7000"), slabs) == D("0.00")
    assert professional_tax(D("7500"), slabs) == D("0.00")  # bound is inclusive
    assert professional_tax(D("9000"), slabs) == D("175.00")
    assert professional_tax(D("50000"), slabs) == D("200.00")


def test_professional_tax_is_zero_where_it_is_not_levied():
    """Delhi, Haryana, UP and others don't levy PT. No slabs => no deduction."""
    assert professional_tax(D("100000"), []) == D("0")


def test_professional_tax_ignores_slab_input_order():
    slabs = [(None, D("200")), (D("10000"), D("175")), (D("7500"), D("0"))]
    assert professional_tax(D("9000"), slabs) == D("175.00")


def test_rupees_rounds_half_up_not_bankers():
    """Python's default rounding is half-to-even; ₹0.50 must go up, not to the
    nearest even rupee."""
    assert rupees(D("10.5")) == D("11")
    assert rupees(D("11.5")) == D("12")


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
    """A company with PF+ESI on, one employee on ₹30,000 (₹15,000 basic)."""
    sub = f"pay-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(
        "/api/v1/auth/bootstrap",
        json={"company_name": "Pay Co", "subdomain": sub, "email": admin,
              "password": "pw123456"},
    )
    tok = client.post(
        "/api/v1/auth/login",
        json={"company": sub, "email": admin, "password": "pw123456"},
    ).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}

    client.put(
        "/api/v1/payroll/settings",
        json={"pf_enabled": True, "esi_enabled": True}, headers=hr,
    )
    basic = client.post(
        "/api/v1/payroll/components",
        json={"code": "BASIC", "name": "Basic", "pf_wage": True, "sequence": 10},
        headers=hr,
    ).json()
    hra = client.post(
        "/api/v1/payroll/components",
        json={"code": "HRA", "name": "House Rent Allowance", "sequence": 20},
        headers=hr,
    ).json()
    emp = client.post(
        "/api/v1/hr/employees",
        json={"full_name": "Asha Rao", "email": f"asha@{sub}.test"}, headers=hr,
    ).json()
    client.put(
        f"/api/v1/payroll/employees/{emp['id']}/salary",
        json={"components": [
            {"component_id": basic["id"], "amount": "15000.00"},
            {"component_id": hra["id"], "amount": "15000.00"},
        ]},
        headers=hr,
    )
    return {"sub": sub, "hr": hr, "employee": emp, "basic": basic, "hra": hra}


def _run(client, org, period="2026-06-01"):
    r = client.post("/api/v1/payroll/runs", json={"period": period}, headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


@endpoint
def test_a_run_computes_gross_statutory_and_net(client, org):
    run = _run(client, org)
    slip = next(p for p in run["payslips"] if p["employee_name"] == "Asha Rao")

    assert Decimal(slip["gross"]) == D("30000.00")
    deductions = {d["code"]: Decimal(d["amount"]) for d in slip["breakdown"]["deductions"]}
    assert deductions["EPF"] == D("1800")          # 12% of ₹15,000 basic
    assert deductions["ESI"] == D("0")             # ₹30,000 gross is over the ceiling
    assert Decimal(slip["net"]) == D("30000.00") - D("1800")

    employer = {d["code"]: Decimal(d["amount"]) for d in
                slip["breakdown"]["employer_contributions"]}
    assert employer["EPS_ER"] == D("1250")
    assert employer["EPF_ER"] == D("550")
    # Employer-only charges on top of the 12%: EDLI and admin, each 0.5% of the
    # ₹15,000 PF wage. Leaving them out understated cost-to-company by ~1%.
    assert employer["EDLI_ER"] == D("75")
    assert employer["ADMIN_ER"] == D("75")
    assert Decimal(slip["employer_cost"]) == D("30000") + D("1800") + D("75") + D("75")


@endpoint
def test_period_is_normalized_to_the_first_of_the_month(client, org):
    """Any date in June must mean June, not a second run for the 17th."""
    first = _run(client, org, "2026-06-17")
    assert first["period"] == "2026-06-01"
    again = _run(client, org, "2026-06-02")
    assert again["id"] == first["id"]


@endpoint
def test_rerunning_a_month_does_not_duplicate_payslips(client, org):
    first = _run(client, org)
    second = _run(client, org)
    assert len(second["payslips"]) == len(first["payslips"])
    assert second["net_total"] == first["net_total"]


@endpoint
def test_manual_lop_survives_a_recompute(client, org):
    """HR types an LOP figure the system can't know (a mid-month exit). A
    later recompute must not silently throw it away."""
    run = _run(client, org)
    slip = run["payslips"][0]
    adjusted = client.patch(
        f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
        json={"lop_days": 5}, headers=org["hr"],
    ).json()
    assert adjusted["lop_days"] == 5
    assert adjusted["lop_overridden"] is True
    assert Decimal(adjusted["gross"]) < D("30000")

    again = _run(client, org)
    kept = next(p for p in again["payslips"] if p["id"] == slip["id"])
    assert kept["lop_days"] == 5, "recompute discarded a human's correction"


@endpoint
def test_lop_prorates_by_working_days(client, org):
    run = _run(client, org)
    slip = run["payslips"][0]
    working = slip["working_days"]
    adjusted = client.patch(
        f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
        json={"lop_days": 1}, headers=org["hr"],
    ).json()
    assert adjusted["paid_days"] == working - 1
    expected = (D("30000") * Decimal(working - 1) / Decimal(working)).quantize(D("0.01"))
    # Component-wise rounding can differ from whole-gross rounding by a paisa.
    assert abs(Decimal(adjusted["gross"]) - expected) <= D("0.02")


@endpoint
def test_run_totals_match_the_sum_of_payslips_after_an_adjustment(client, org):
    run = _run(client, org)
    slip = run["payslips"][0]
    client.patch(
        f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
        json={"tds": "2500.00"}, headers=org["hr"],
    )
    detail = client.get(f"/api/v1/payroll/runs/{run['id']}", headers=org["hr"]).json()
    assert Decimal(detail["net_total"]) == sum(
        Decimal(p["net"]) for p in detail["payslips"]
    )


@endpoint
def test_tds_is_an_input_and_lands_in_the_deductions(client, org):
    run = _run(client, org)
    slip = run["payslips"][0]
    before = Decimal(slip["net"])
    adjusted = client.patch(
        f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
        json={"tds": "3000.00"}, headers=org["hr"],
    ).json()
    assert Decimal(adjusted["net"]) == before - D("3000")


@endpoint
def test_unpaid_leave_becomes_loss_of_pay(client, org):
    hr = org["hr"]
    unpaid = client.post(
        "/api/v1/leave/types",
        json={"name": "Leave Without Pay", "annual_quota": 30, "paid": False}, headers=hr,
    ).json()
    assert unpaid["paid"] is False

    monday = date(2026, 6, 1)  # a Monday
    filed = client.post(
        "/api/v1/leave/requests",
        json={"employee_id": org["employee"]["id"], "leave_type_id": unpaid["id"],
              "start_date": str(monday), "end_date": str(monday + timedelta(days=2))},
        headers=hr,
    ).json()
    client.post(f"/api/v1/leave/requests/{filed['id']}/approve", headers=hr)

    slip = _run(client, org)["payslips"][0]
    assert slip["lop_days"] == 3
    assert Decimal(slip["gross"]) < D("30000")


@endpoint
def test_paid_leave_is_not_loss_of_pay(client, org):
    hr = org["hr"]
    annual = client.post(
        "/api/v1/leave/types", json={"name": "Annual", "annual_quota": 20}, headers=hr
    ).json()
    assert annual["paid"] is True  # the default, so existing types keep working

    monday = date(2026, 6, 1)
    filed = client.post(
        "/api/v1/leave/requests",
        json={"employee_id": org["employee"]["id"], "leave_type_id": annual["id"],
              "start_date": str(monday), "end_date": str(monday + timedelta(days=2))},
        headers=hr,
    ).json()
    client.post(f"/api/v1/leave/requests/{filed['id']}/approve", headers=hr)

    slip = _run(client, org)["payslips"][0]
    assert slip["lop_days"] == 0
    assert Decimal(slip["gross"]) == D("30000.00")


@endpoint
def test_a_mid_month_joiner_is_paid_only_from_their_joining_date(client, org):
    joiner = client.post(
        "/api/v1/hr/employees",
        json={"full_name": "Ben Ford", "joined_on": "2026-06-16"}, headers=org["hr"],
    ).json()
    client.put(
        f"/api/v1/payroll/employees/{joiner['id']}/salary",
        json={"components": [{"component_id": org["basic"]["id"], "amount": "30000.00"}]},
        headers=org["hr"],
    )
    slip = next(p for p in _run(client, org)["payslips"] if p["employee_id"] == joiner["id"])
    assert slip["paid_days"] < slip["working_days"]
    assert Decimal(slip["gross"]) < D("30000")


@endpoint
def test_someone_who_joins_after_the_month_is_not_on_the_payroll(client, org):
    later = client.post(
        "/api/v1/hr/employees",
        json={"full_name": "Future Hire", "joined_on": "2026-09-01"}, headers=org["hr"],
    ).json()
    run = _run(client, org)
    assert all(p["employee_id"] != later["id"] for p in run["payslips"])


# --- the finalize boundary --------------------------------------------------


@endpoint
def test_a_finalized_run_cannot_be_recomputed_or_adjusted(client, org):
    run = _run(client, org)
    slip = run["payslips"][0]
    assert client.post(
        f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"]
    ).status_code == 200

    # Recompute, adjust, and re-finalize must all refuse.
    assert client.post(
        "/api/v1/payroll/runs", json={"period": "2026-06-01"}, headers=org["hr"]
    ).status_code == 409
    assert client.patch(
        f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
        json={"tds": "9999.00"}, headers=org["hr"],
    ).status_code == 409
    assert client.post(
        f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"]
    ).status_code == 409


@endpoint
def test_finalizing_freezes_the_numbers_against_a_later_salary_change(client, org):
    run = _run(client, org)
    net_before = run["net_total"]
    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])

    # Double everyone's salary afterwards.
    client.put(
        f"/api/v1/payroll/employees/{org['employee']['id']}/salary",
        json={"components": [{"component_id": org["basic"]["id"], "amount": "60000.00"}]},
        headers=org["hr"],
    )
    after = client.get(f"/api/v1/payroll/runs/{run['id']}", headers=org["hr"]).json()
    assert after["net_total"] == net_before


@endpoint
def test_a_payslip_survives_the_employee_being_renamed(client, org):
    run = _run(client, org)
    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])
    client.patch(
        f"/api/v1/hr/employees/{org['employee']['id']}",
        json={"full_name": "Asha Rao-Sharma"}, headers=org["hr"],
    )
    after = client.get(f"/api/v1/payroll/runs/{run['id']}", headers=org["hr"]).json()
    assert after["payslips"][0]["employee_name"] == "Asha Rao"


@endpoint
def test_an_empty_run_cannot_be_finalized(client, org):
    """Guards against finalizing a month where the computation found nobody —
    a run with no payslips is a bug, not a payroll."""
    sub = f"empty-{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/v1/auth/bootstrap",
        json={"company_name": "Empty Co", "subdomain": sub,
              "email": f"a@{sub}.test", "password": "pw123456"},
    )
    tok = client.post(
        "/api/v1/auth/login",
        json={"company": sub, "email": f"a@{sub}.test", "password": "pw123456"},
    ).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    run = client.post(
        "/api/v1/payroll/runs", json={"period": "2026-06-01"}, headers=hr
    ).json()
    assert run["payslips"] == []
    assert client.post(
        f"/api/v1/payroll/runs/{run['id']}/finalize", headers=hr
    ).status_code == 422


# --- the permission boundary ------------------------------------------------


def _employee_headers(client, org, name="Ben Ford"):
    from app.core.notify.base import outbox

    email = f"{uuid.uuid4().hex[:6]}@{org['sub']}.test"
    emp = client.post(
        "/api/v1/hr/employees", json={"full_name": name, "email": email}, headers=org["hr"]
    ).json()
    outbox.clear()
    client.post(f"/api/v1/hr/employees/{emp['id']}/invite", headers=org["hr"])
    mail = next(m for m in outbox if m["template"] == "access_granted")
    pw = next(
        ln.split("Password:")[1].strip() for ln in mail["body"].splitlines()
        if "Password:" in ln
    )
    tok = client.post(
        "/api/v1/auth/login",
        json={"company": org["sub"], "email": email, "password": pw},
    ).json()
    return emp, {"Authorization": f"Bearer {tok['access_token']}"}


@endpoint
def test_an_employee_cannot_reach_any_payroll_admin_route(client, org):
    """Salary is the most confidential data here. Every admin route must
    refuse the employee role — including reading someone else's payslips."""
    _, emp_h = _employee_headers(client, org)
    other = org["employee"]["id"]
    for method, path in [
        ("get", "/api/v1/payroll/settings"),
        ("get", "/api/v1/payroll/components"),
        ("get", "/api/v1/payroll/runs"),
        ("get", f"/api/v1/payroll/employees/{other}/salary"),
        ("get", f"/api/v1/payroll/employees/{other}/payslips"),
        ("get", "/api/v1/payroll/pt-slabs"),
    ]:
        assert getattr(client, method)(path, headers=emp_h).status_code == 403, path
    assert client.post(
        "/api/v1/payroll/runs", json={"period": "2026-06-01"}, headers=emp_h
    ).status_code == 403
    assert client.put(
        f"/api/v1/payroll/employees/{other}/salary",
        json={"components": []}, headers=emp_h,
    ).status_code == 403


@endpoint
def test_a_manager_cannot_see_payroll(client, org):
    """Managers approve leave and see attendance. Pay is a separate decision
    and the default is no."""
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.core.security import hash_password
    from app.modules.auth.models import User

    s = get_settings()
    cid = pyjwt.decode(
        org["hr"]["Authorization"].split()[1], s.jwt_secret, algorithms=[s.jwt_alg]
    )["cid"]
    email = f"mgr-{uuid.uuid4().hex[:6]}@{org['sub']}.test"
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": cid})
    db.add(User(company_id=uuid.UUID(cid), email=email, role="manager",
                password_hash=hash_password("pw123456")))
    db.commit()
    db.close()

    tok = client.post(
        "/api/v1/auth/login",
        json={"company": org["sub"], "email": email, "password": "pw123456"},
    ).json()
    mgr = {"Authorization": f"Bearer {tok['access_token']}"}
    assert client.get("/api/v1/payroll/runs", headers=mgr).status_code == 403
    assert client.get(
        f"/api/v1/payroll/employees/{org['employee']['id']}/salary", headers=mgr
    ).status_code == 403


@endpoint
def test_an_employee_sees_only_their_own_finalized_payslips(client, org):
    emp, emp_h = _employee_headers(client, org)
    client.put(
        f"/api/v1/payroll/employees/{emp['id']}/salary",
        json={"components": [{"component_id": org["basic"]["id"], "amount": "20000.00"}]},
        headers=org["hr"],
    )
    run = _run(client, org)

    # Draft: nothing to see yet.
    assert client.get("/api/v1/me/payslips", headers=emp_h).json() == []

    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])
    mine = client.get("/api/v1/me/payslips", headers=emp_h).json()
    assert [p["employee_id"] for p in mine] == [emp["id"]]
    assert all(p["employee_name"] == emp["full_name"] for p in mine)


@endpoint
def test_professional_tax_is_deducted_once_slabs_are_configured(client, org):
    client.put(
        "/api/v1/payroll/pt-slabs",
        json=[{"up_to": "7500", "amount": "0"}, {"up_to": "10000", "amount": "175"},
              {"up_to": None, "amount": "200"}],
        headers=org["hr"],
    )
    slip = _run(client, org)["payslips"][0]
    pt = next(d for d in slip["breakdown"]["deductions"] if d["code"] == "PT")
    assert Decimal(pt["amount"]) == D("200.00")  # ₹30,000 gross -> top slab


@endpoint
def test_only_one_unbounded_pt_slab_is_allowed(client, org):
    r = client.put(
        "/api/v1/payroll/pt-slabs",
        json=[{"up_to": None, "amount": "200"}, {"up_to": None, "amount": "300"}],
        headers=org["hr"],
    )
    assert r.status_code == 422


@endpoint
def test_salary_rejects_an_unknown_pay_component(client, org):
    r = client.put(
        f"/api/v1/payroll/employees/{org['employee']['id']}/salary",
        json={"components": [{"component_id": str(uuid.uuid4()), "amount": "1000"}]},
        headers=org["hr"],
    )
    assert r.status_code == 422


@endpoint
def test_duplicate_component_code_is_rejected(client, org):
    r = client.post(
        "/api/v1/payroll/components",
        json={"code": "basic", "name": "Basic Again"}, headers=org["hr"],
    )
    assert r.status_code == 409  # codes are normalized to upper case


@endpoint
def test_setting_a_salary_does_not_write_amounts_to_the_audit_log(client, org):
    """The audit log has a wider readership than the salary does."""
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.modules.audit.models import AuditEvent

    client.put(
        f"/api/v1/payroll/employees/{org['employee']['id']}/salary",
        json={"components": [{"component_id": org["basic"]["id"], "amount": "77777.00"}]},
        headers=org["hr"],
    )
    s = get_settings()
    cid = pyjwt.decode(
        org["hr"]["Authorization"].split()[1], s.jwt_secret, algorithms=[s.jwt_alg]
    )["cid"]
    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": cid})
    rows = db.query(AuditEvent).filter(AuditEvent.entity == "salary_structure").all()
    payloads = [str(r.payload) for r in rows]
    db.close()
    assert rows, "the change should still be recorded"
    assert not any("77777" in p for p in payloads)
