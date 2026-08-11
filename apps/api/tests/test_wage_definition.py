"""The statutory wage definition, and rule resolution by period.

The Code on Wages definition applies from 21 November 2025: excluded
allowances above half of total remuneration are added back into wages. Before
that date the nominated components stand alone.

Two things must be exactly right and are tested here as such:

  1. the arithmetic of the 50% test, including its boundary
  2. that rules resolve by the PAYROLL PERIOD, not by today — otherwise a
     re-run of a past month silently recomputes under today's law
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.modules.payroll import rules
from app.modules.payroll.rules import (
    LABOUR_CODE_START,
    epf_rule_for,
    statutory_wage,
    wage_definition_for,
)

D = Decimal
OLD = wage_definition_for(date(2025, 11, 20))
NEW = wage_definition_for(LABOUR_CODE_START)


# --- rule resolution --------------------------------------------------------


def test_the_wage_definition_switches_on_21_november_2025():
    assert wage_definition_for(date(2025, 11, 20)).excluded_share_cap is None
    assert wage_definition_for(date(2025, 11, 21)).excluded_share_cap == D("0.5")
    assert wage_definition_for(date(2026, 8, 1)).excluded_share_cap == D("0.5")


def test_rules_resolve_by_period_not_by_today():
    """The property that makes a corrected re-run defensible: ask for March
    2020 and you get March 2020's rules, forever."""
    assert wage_definition_for(date(2020, 3, 1)).version == "wages-nominated-components"
    assert wage_definition_for(date(2026, 3, 1)).version == "wages-code-on-wages-2025-11"
    assert epf_rule_for(date(2020, 3, 1)).version == epf_rule_for(date(2026, 3, 1)).version


def test_a_period_before_any_rule_is_an_error_not_a_silent_default():
    with pytest.raises(ValueError):
        epf_rule_for(date(1990, 1, 1))


def test_every_rule_family_is_ordered_and_versioned():
    for family in (rules.WAGE_DEFINITIONS, rules.EPF_RULES, rules.ESI_RULES):
        versions = [r.version for r in family]
        assert len(set(versions)) == len(versions), "duplicate rule version"
        assert all(r.effective_from for r in family)


# --- the 50% test -----------------------------------------------------------


def test_the_reviewers_worked_example():
    """Basic 12,000 + HRA 8,000 + Special 10,000 + Other 5,000 + OT 6,000.
    Remuneration 41,000, half is 20,500, excluded is 29,000, so 8,500 comes
    back and the statutory wage is 20,500 — not the 12,000 basic."""
    lines = [
        (D("12000"), "wages"), (D("8000"), "excluded"), (D("10000"), "excluded"),
        (D("5000"), "excluded"), (D("6000"), "excluded"),
    ]
    b = statutory_wage(lines, NEW)
    assert b.remuneration == D("41000")
    assert b.excluded == D("29000")
    assert b.added_back == D("8500")
    assert b.statutory_wage == D("20500")


def test_the_same_structure_before_the_labour_code_uses_basic_alone():
    lines = [
        (D("12000"), "wages"), (D("8000"), "excluded"), (D("10000"), "excluded"),
        (D("5000"), "excluded"), (D("6000"), "excluded"),
    ]
    b = statutory_wage(lines, OLD)
    assert b.added_back == D("0")
    assert b.statutory_wage == D("12000")


def test_exactly_half_excluded_adds_nothing_back():
    """The boundary. At exactly 50% there is no excess."""
    b = statutory_wage([(D("20000"), "wages"), (D("20000"), "excluded")], NEW)
    assert b.added_back == D("0")
    assert b.statutory_wage == D("20000")


def test_one_rupee_over_half_adds_exactly_one_rupee_back():
    b = statutory_wage([(D("19999"), "wages"), (D("20001"), "excluded")], NEW)
    assert b.added_back == D("1")
    assert b.statutory_wage == D("20000")


def test_a_structure_below_the_threshold_is_untouched():
    b = statutory_wage([(D("30000"), "wages"), (D("10000"), "excluded")], NEW)
    assert b.added_back == D("0")
    assert b.statutory_wage == D("30000")


def test_an_all_allowance_structure_lands_at_half():
    """The abuse the rule exists to stop: nominate almost nothing as wages and
    the definition pulls it back to half of remuneration regardless."""
    b = statutory_wage([(D("1"), "wages"), (D("39999"), "excluded")], NEW)
    assert b.statutory_wage == D("20000")


def test_outside_remuneration_is_in_neither_side_of_the_test():
    """A reimbursement of actual expense is not remuneration, so it must not
    inflate the denominator and dilute the add-back."""
    with_reimbursement = statutory_wage(
        [(D("12000"), "wages"), (D("29000"), "excluded"), (D("50000"), "outside")], NEW
    )
    without = statutory_wage([(D("12000"), "wages"), (D("29000"), "excluded")], NEW)
    assert with_reimbursement.statutory_wage == without.statutory_wage == D("20500")


def test_the_statutory_wage_never_exceeds_remuneration():
    """Invariant across a spread: you cannot owe PF on more than was paid."""
    for wages, excluded in [
        ("0", "10000"), ("10000", "0"), ("1", "99999"), ("50000", "50000"), ("7", "13"),
    ]:
        b = statutory_wage([(D(wages), "wages"), (D(excluded), "excluded")], NEW)
        assert b.statutory_wage <= b.remuneration
        assert b.statutory_wage >= D(wages)


# --- through the engine -----------------------------------------------------


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
    sub = f"wage-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Wage Co", "subdomain": sub, "email": admin,
                      "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": admin, "password": "pw123456"}).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}
    client.put("/api/v1/payroll/settings",
               json={"pf_enabled": True, "esi_enabled": False}, headers=hr)

    comps = {}
    for code, name, basis, seq in [
        ("BASIC", "Basic", "wages", 10),
        ("HRA", "HRA", "excluded", 20),
        ("SPECIAL", "Special Allowance", "excluded", 30),
        ("REIMB", "Expense Reimbursement", "outside", 40),
    ]:
        comps[code] = client.post(
            "/api/v1/payroll/components",
            json={"code": code, "name": name, "wage_basis": basis, "sequence": seq},
            headers=hr,
        ).json()
    return {"hr": hr, "comps": comps}


def _employee_with(client, org, name, amounts: dict[str, str]):
    emp = client.post("/api/v1/hr/employees", json={"full_name": name},
                      headers=org["hr"]).json()
    client.put(
        f"/api/v1/payroll/employees/{emp['id']}/salary",
        json={"components": [
            {"component_id": org["comps"][code]["id"], "amount": amt}
            for code, amt in amounts.items()
        ]},
        headers=org["hr"],
    )
    return emp


@endpoint
def test_pf_is_computed_on_the_derived_wage_not_the_nominated_basic(client, org):
    """End to end: the ₹360/month under-remittance this engine exists to fix.

    Basic 12,000 inside a 41,000 package. The old basis gave PF on 12,000
    (₹1,440). The derived wage is 20,500, which the ceiling caps at 15,000, so
    PF is ₹1,800.
    """
    _employee_with(client, org, "Derived",
                   {"BASIC": "12000.00", "HRA": "8000.00", "SPECIAL": "21000.00"})
    run = client.post("/api/v1/payroll/runs", json={"period": "2026-09-01"},
                      headers=org["hr"]).json()
    slip = next(p for p in run["payslips"] if p["employee_name"] == "Derived")

    basis = slip["breakdown"]["basis"]
    assert basis["nominated_wages"] == "12000.00"
    assert basis["remuneration"] == "41000.00"
    assert basis["added_back"] == "8500.00"
    assert basis["statutory_wage"] == "20500.00"

    epf = next(d for d in slip["breakdown"]["deductions"] if d["code"] == "EPF")
    assert D(epf["amount"]) == D("1800")


@endpoint
def test_a_reimbursement_does_not_dilute_the_wage_test(client, org):
    """"outside" money must not enlarge remuneration and shrink the add-back —
    that would be the loophole reopened."""
    _employee_with(client, org, "Reimbursed",
                   {"BASIC": "12000.00", "HRA": "8000.00", "SPECIAL": "21000.00",
                    "REIMB": "40000.00"})
    run = client.post("/api/v1/payroll/runs", json={"period": "2026-09-01"},
                      headers=org["hr"]).json()
    slip = next(p for p in run["payslips"] if p["employee_name"] == "Reimbursed")
    assert slip["breakdown"]["basis"]["statutory_wage"] == "20500.00"


@endpoint
def test_the_payslip_records_the_rules_it_was_computed_under(client, org):
    _employee_with(client, org, "Versioned", {"BASIC": "20000.00", "HRA": "5000.00"})
    run = client.post("/api/v1/payroll/runs", json={"period": "2026-09-01"},
                      headers=org["hr"]).json()
    versions = next(
        p for p in run["payslips"] if p["employee_name"] == "Versioned"
    )["breakdown"]["rule_versions"]
    assert versions["wage_definition"] == "wages-code-on-wages-2025-11"
    assert versions["epf"] and versions["esi"]


@endpoint
def test_a_pre_labour_code_period_uses_the_old_wage_basis(client, org):
    """The reason resolution is by period: running an October 2025 payroll must
    not apply a definition that didn't exist yet."""
    _employee_with(client, org, "Historic",
                   {"BASIC": "12000.00", "HRA": "8000.00", "SPECIAL": "21000.00"})
    run = client.post("/api/v1/payroll/runs", json={"period": "2025-10-01"},
                      headers=org["hr"]).json()
    slip = next(p for p in run["payslips"] if p["employee_name"] == "Historic")

    assert slip["breakdown"]["rule_versions"]["wage_definition"] == "wages-nominated-components"
    assert D(slip["breakdown"]["basis"]["added_back"]) == D("0")
    epf = next(d for d in slip["breakdown"]["deductions"] if d["code"] == "EPF")
    assert D(epf["amount"]) == D("1440")  # 12% of the nominated 12,000


@endpoint
def test_the_legacy_pf_wage_flag_still_classifies_a_component(client, org):
    """Integrations predating `wage_basis` send `pf_wage: true`. That has to
    keep meaning "this is wages" rather than silently becoming excluded."""
    r = client.post("/api/v1/payroll/components",
                    json={"code": "DA", "name": "Dearness Allowance", "pf_wage": True},
                    headers=org["hr"])
    assert r.status_code == 200, r.text
    assert r.json()["wage_basis"] == "wages"


@endpoint
def test_an_unknown_wage_basis_is_rejected(client, org):
    r = client.post("/api/v1/payroll/components",
                    json={"code": "JUNK", "name": "Junk", "wage_basis": "whatever"},
                    headers=org["hr"])
    assert r.status_code == 422
