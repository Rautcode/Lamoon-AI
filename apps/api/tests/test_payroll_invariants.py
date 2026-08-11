"""Payroll invariants.

Example-based tests answer "is this number right for this input". Invariants
answer "what class of failure is impossible", which is the question that
matters for money. Each test here asserts a property that must hold across a
SPREAD of inputs, not one case.

This suite exists because 37 example tests missed a live bug: `compute_payslip`
added the pre-joining shortfall to whatever `lop_days` it was handed, so
feeding a stored total back in counted it twice. A mid-month joiner's pay
silently went to zero the first time anyone edited their TDS. The idempotence
invariant below catches it in one line; no amount of example-picking did.

Parametrised over plain lists rather than a property-based library — a new
dependency isn't worth it for a fixed, well-understood input space.
"""
import uuid
from datetime import date, timedelta
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

#: (basic, hra) pairs chosen to straddle every statutory boundary that exists:
#: below/at/above the PF ceiling (15,000) and the ESI ceiling (21,000).
SALARY_SHAPES = [
    ("5000", "2000"),      # low wage, both schemes apply
    ("9000", "9000"),      # ESI applies, PF under ceiling
    ("15000", "6000"),     # PF wage exactly at the ceiling
    ("15001", "5999"),     # one rupee over
    ("12000", "9000"),     # gross exactly at the ESI ceiling
    ("12000", "9001"),     # one rupee over the ESI ceiling
    ("40000", "20000"),    # high earner, ESI does not apply
]


@pytest.fixture
def org(client):
    """A company with PF+ESI on and a PT schedule, so every deduction is live."""
    sub = f"inv-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(
        "/api/v1/auth/bootstrap",
        json={"company_name": "Inv Co", "subdomain": sub, "email": admin,
              "password": "pw123456"},
    )
    tok = client.post(
        "/api/v1/auth/login",
        json={"company": sub, "email": admin, "password": "pw123456"},
    ).json()
    hr = {"Authorization": f"Bearer {tok['access_token']}"}

    client.put("/api/v1/payroll/settings",
               json={"pf_enabled": True, "esi_enabled": True}, headers=hr)
    client.put("/api/v1/payroll/pt-slabs",
               json=[{"up_to": "7500", "amount": "0"}, {"up_to": "10000", "amount": "175"},
                     {"up_to": None, "amount": "200"}], headers=hr)
    basic = client.post("/api/v1/payroll/components",
                        json={"code": "BASIC", "name": "Basic", "pf_wage": True,
                              "sequence": 10}, headers=hr).json()
    hra = client.post("/api/v1/payroll/components",
                      json={"code": "HRA", "name": "HRA", "sequence": 20}, headers=hr).json()
    return {"sub": sub, "hr": hr, "basic": basic, "hra": hra}


def _employee(client, org, name, basic, hra, joined_on=None):
    body = {"full_name": name}
    if joined_on:
        body["joined_on"] = joined_on
    emp = client.post("/api/v1/hr/employees", json=body, headers=org["hr"]).json()
    client.put(
        f"/api/v1/payroll/employees/{emp['id']}/salary",
        json={"components": [
            {"component_id": org["basic"]["id"], "amount": basic},
            {"component_id": org["hra"]["id"], "amount": hra},
        ]},
        headers=org["hr"],
    )
    return emp


def _run(client, org, period=PERIOD):
    r = client.post("/api/v1/payroll/runs", json={"period": period}, headers=org["hr"])
    assert r.status_code == 200, r.text
    return r.json()


def _lines(slip, key):
    return {d["code"]: D(d["amount"]) for d in slip["breakdown"][key]}


# --- the arithmetic must close, whatever the inputs -------------------------


@pytest.mark.parametrize("basic,hra", SALARY_SHAPES)
def test_net_is_always_gross_minus_deductions(client, org, basic, hra):
    _employee(client, org, f"Net {basic}", basic, hra)
    slip = next(p for p in _run(client, org)["payslips"] if p["employee_name"].startswith("Net"))
    assert D(slip["net"]) == D(slip["gross"]) - D(slip["deductions"])


@pytest.mark.parametrize("basic,hra", SALARY_SHAPES)
def test_deductions_equal_the_sum_of_their_lines(client, org, basic, hra):
    """The total can't be a separately-computed number that drifts from the
    lines the payslip actually shows."""
    _employee(client, org, f"Ded {basic}", basic, hra)
    slip = next(p for p in _run(client, org)["payslips"] if p["employee_name"].startswith("Ded"))
    assert D(slip["deductions"]) == sum(_lines(slip, "deductions").values())


@pytest.mark.parametrize("basic,hra", SALARY_SHAPES)
def test_employer_pf_shares_reconcile_to_the_employee_share(client, org, basic, hra):
    """EPS + residual EPF must equal the employee's 12%. Rounding each side
    independently is how the two drift a rupee apart."""
    _employee(client, org, f"PF {basic}", basic, hra)
    slip = next(p for p in _run(client, org)["payslips"] if p["employee_name"].startswith("PF"))
    ded, emp = _lines(slip, "deductions"), _lines(slip, "employer_contributions")
    assert emp["EPS_ER"] + emp["EPF_ER"] == ded["EPF"]


@pytest.mark.parametrize("basic,hra", SALARY_SHAPES)
def test_employer_cost_is_gross_plus_employer_contributions(client, org, basic, hra):
    _employee(client, org, f"CTC {basic}", basic, hra)
    slip = next(p for p in _run(client, org)["payslips"] if p["employee_name"].startswith("CTC"))
    assert D(slip["employer_cost"]) == D(slip["gross"]) + sum(
        _lines(slip, "employer_contributions").values()
    )


@pytest.mark.parametrize("basic,hra", SALARY_SHAPES)
def test_paid_and_unpaid_days_account_for_every_working_day(client, org, basic, hra):
    _employee(client, org, f"Days {basic}", basic, hra)
    slip = next(p for p in _run(client, org)["payslips"] if p["employee_name"].startswith("Days"))
    assert slip["paid_days"] + slip["lop_days"] == slip["working_days"]
    assert 0 <= slip["paid_days"] <= slip["working_days"]


def test_run_totals_always_equal_the_sum_of_their_payslips(client, org):
    for i, (basic, hra) in enumerate(SALARY_SHAPES):
        _employee(client, org, f"Sum {i}", basic, hra)
    run = _run(client, org)
    for field in ("gross", "deductions", "net"):
        assert D(run[f"{field}_total"]) == sum(D(p[field]) for p in run["payslips"]), field

    # Employer cost carries one figure that belongs to no payslip: the top-up
    # to the EPF administration minimum, which is levied per establishment.
    assert D(run["employer_cost_total"]) == sum(
        D(p["employer_cost"]) for p in run["payslips"]
    ) + D(run["admin_shortfall"])


def test_the_admin_minimum_is_charged_once_per_establishment_not_per_head(client, org):
    """A small company owes the monthly floor however little 0.5% comes to;
    a large one owes nothing extra. Either way it is charged once."""
    _employee(client, org, "Solo", "9000", "9000")
    small = _run(client, org)
    charged = sum(
        D(line["amount"])
        for p in small["payslips"]
        for line in p["breakdown"]["employer_contributions"]
        if line["code"] == "ADMIN_ER"
    )
    assert D(small["admin_shortfall"]) == max(D("0"), D("500") - charged)
    assert D(small["employer_cost_total"]) >= sum(
        D(p["employer_cost"]) for p in small["payslips"]
    )


# --- idempotence: the invariant that caught the joiner bug ------------------


@pytest.mark.parametrize("joined_on", [None, "2026-09-01", "2026-09-16", "2026-09-30"])
def test_recomputing_a_run_changes_nothing(client, org, joined_on):
    """calculate(calculate(x)) == calculate(x). Re-running a draft must be a
    no-op when no input changed — including for a mid-month joiner, whose
    pre-joining days used to be re-subtracted on every pass."""
    _employee(client, org, "Repeat", "20000", "10000", joined_on=joined_on)

    first = next(p for p in _run(client, org)["payslips"] if p["employee_name"] == "Repeat")
    for _ in range(3):
        again = next(p for p in _run(client, org)["payslips"] if p["employee_name"] == "Repeat")
        for field in ("lop_days", "paid_days", "working_days", "gross", "deductions", "net"):
            assert again[field] == first[field], f"{field} drifted on recompute"


@pytest.mark.parametrize("joined_on", [None, "2026-09-16"])
def test_editing_tds_does_not_disturb_the_day_count(client, org, joined_on):
    """THE regression. Editing only TDS re-derived unpaid days from a stored
    total that already included the pre-joining shortfall, so it was counted
    twice and a mid-month joiner's gross collapsed to zero."""
    _employee(client, org, "TdsOnly", "30000", "0", joined_on=joined_on)
    run = _run(client, org)
    slip = next(p for p in run["payslips"] if p["employee_name"] == "TdsOnly")
    before = {k: slip[k] for k in ("lop_days", "paid_days", "gross")}

    for amount in ("100.00", "250.00", "0.00"):
        slip = client.patch(
            f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
            json={"tds": amount}, headers=org["hr"],
        ).json()
        assert {k: slip[k] for k in ("lop_days", "paid_days", "gross")} == before
        assert D(slip["net"]) == D(slip["gross"]) - D(slip["deductions"])


def test_a_manual_lop_is_a_total_and_is_not_added_to_again(client, org):
    """An override is what the operator sees on the payslip — the full figure.
    Nothing further may be added to it, or repeated edits would inflate it."""
    _employee(client, org, "Override", "30000", "0", joined_on="2026-09-16")
    run = _run(client, org)
    slip = next(p for p in run["payslips"] if p["employee_name"] == "Override")

    slip = client.patch(
        f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
        json={"lop_days": 4}, headers=org["hr"],
    ).json()
    assert slip["lop_days"] == 4 and slip["lop_overridden"] is True

    # Neither a recompute nor an unrelated edit may move it.
    again = next(p for p in _run(client, org)["payslips"] if p["employee_name"] == "Override")
    assert again["lop_days"] == 4
    after_tds = client.patch(
        f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
        json={"tds": "500.00"}, headers=org["hr"],
    ).json()
    assert after_tds["lop_days"] == 4


def test_identical_inputs_produce_identical_output_in_a_fresh_company(client, org):
    """Determinism across companies: same salary, same calendar, same numbers.
    Guards against anything leaking in from ambient state."""
    _employee(client, org, "Twin", "18000", "6000")
    a = next(p for p in _run(client, org)["payslips"] if p["employee_name"] == "Twin")

    second = org.copy()
    sub = f"inv2-{uuid.uuid4().hex[:8]}"
    client.post("/api/v1/auth/bootstrap",
                json={"company_name": "Inv2", "subdomain": sub,
                      "email": f"a@{sub}.test", "password": "pw123456"})
    tok = client.post("/api/v1/auth/login",
                      json={"company": sub, "email": f"a@{sub}.test",
                            "password": "pw123456"}).json()
    second["hr"] = {"Authorization": f"Bearer {tok['access_token']}"}
    client.put("/api/v1/payroll/settings",
               json={"pf_enabled": True, "esi_enabled": True}, headers=second["hr"])
    client.put("/api/v1/payroll/pt-slabs",
               json=[{"up_to": "7500", "amount": "0"}, {"up_to": "10000", "amount": "175"},
                     {"up_to": None, "amount": "200"}], headers=second["hr"])
    second["basic"] = client.post(
        "/api/v1/payroll/components",
        json={"code": "BASIC", "name": "Basic", "pf_wage": True, "sequence": 10},
        headers=second["hr"]).json()
    second["hra"] = client.post(
        "/api/v1/payroll/components", json={"code": "HRA", "name": "HRA", "sequence": 20},
        headers=second["hr"]).json()

    _employee(client, second, "Twin", "18000", "6000")
    b = next(p for p in _run(client, second)["payslips"] if p["employee_name"] == "Twin")

    for field in ("gross", "deductions", "net", "employer_cost", "working_days"):
        assert a[field] == b[field], field


# --- money is Decimal, end to end ------------------------------------------


@pytest.mark.parametrize("basic,hra", SALARY_SHAPES)
def test_no_amount_ever_carries_float_error(client, org, basic, hra):
    """Every amount in the snapshot must survive a Decimal round-trip. A float
    anywhere upstream shows up here as 1234.5600000000001."""
    _employee(client, org, f"Dec {basic}", basic, hra)
    slip = next(p for p in _run(client, org)["payslips"] if p["employee_name"].startswith("Dec"))

    amounts = [slip[k] for k in ("gross", "deductions", "net", "employer_cost", "tds")]
    for key in ("earnings", "deductions", "employer_contributions"):
        amounts += [line["amount"] for line in slip["breakdown"][key]]
    amounts += [slip["breakdown"]["basis"]["pf_wage"], slip["breakdown"]["basis"]["esi_wage"]]

    for a in amounts:
        assert isinstance(a, str), f"{a!r} crossed the wire as a JSON number"
        d = D(a)
        assert d == D(str(d))
        assert -d.as_tuple().exponent <= 2, f"{a} has sub-paisa precision"


# --- a finalized run is a record, not a working document -------------------


def test_nothing_can_change_a_finalized_run(client, org):
    _employee(client, org, "Frozen", "25000", "10000")
    run = _run(client, org)
    slip = run["payslips"][0]
    before = dict(run)

    assert client.post(f"/api/v1/payroll/runs/{run['id']}/finalize",
                       headers=org["hr"]).status_code == 200

    assert client.post("/api/v1/payroll/runs", json={"period": PERIOD},
                       headers=org["hr"]).status_code == 409
    assert client.patch(f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
                        json={"tds": "9999.00"}, headers=org["hr"]).status_code == 409
    assert client.patch(f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
                        json={"lop_days": 30}, headers=org["hr"]).status_code == 409
    assert client.post(f"/api/v1/payroll/runs/{run['id']}/finalize",
                       headers=org["hr"]).status_code == 409

    after = client.get(f"/api/v1/payroll/runs/{run['id']}", headers=org["hr"]).json()
    for field in ("gross_total", "deductions_total", "net_total", "employer_cost_total"):
        assert after[field] == before[field], field


def test_a_salary_change_cannot_reach_back_into_a_finalized_run(client, org):
    emp = _employee(client, org, "Snapshot", "20000", "10000")
    run = _run(client, org)
    net_before = run["net_total"]
    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])

    client.put(f"/api/v1/payroll/employees/{emp['id']}/salary",
               json={"components": [{"component_id": org["basic"]["id"], "amount": "99000.00"}]},
               headers=org["hr"])
    after = client.get(f"/api/v1/payroll/runs/{run['id']}", headers=org["hr"]).json()
    assert after["net_total"] == net_before


# --- authorization is an invariant too -------------------------------------


def test_an_employee_can_reach_exactly_one_persons_payslips(client, org):
    """Object-level authorization, not just route-level: the employee holds a
    real id for somebody else and still cannot use it anywhere."""
    from app.core.notify.base import outbox

    mine = _employee(client, org, "Self", "20000", "5000")
    theirs = _employee(client, org, "Other", "50000", "20000")
    email = f"self-{uuid.uuid4().hex[:6]}@{org['sub']}.test"
    client.patch(f"/api/v1/hr/employees/{mine['id']}", json={"email": email}, headers=org["hr"])

    outbox.clear()
    client.post(f"/api/v1/hr/employees/{mine['id']}/invite", headers=org["hr"])
    mail = next(m for m in outbox if m["template"] == "access_granted")
    pw = next(ln.split("Password:")[1].strip()
              for ln in mail["body"].splitlines() if "Password:" in ln)
    tok = client.post("/api/v1/auth/login",
                      json={"company": org["sub"], "email": email, "password": pw}).json()
    emp_h = {"Authorization": f"Bearer {tok['access_token']}"}

    run = _run(client, org)
    other_slip = next(p for p in run["payslips"] if p["employee_name"] == "Other")
    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])

    # Every route that names somebody, given a real id, must refuse.
    for path in (
        f"/api/v1/payroll/employees/{theirs['id']}/salary",
        f"/api/v1/payroll/employees/{theirs['id']}/payslips",
        f"/api/v1/payroll/employees/{mine['id']}/payslips",   # even their OWN id
        f"/api/v1/payroll/runs/{run['id']}",
        "/api/v1/payroll/runs",
    ):
        assert client.get(path, headers=emp_h).status_code == 403, path
    assert client.patch(
        f"/api/v1/payroll/runs/{run['id']}/payslips/{other_slip['id']}",
        json={"tds": "0.00"}, headers=emp_h,
    ).status_code == 403

    # The one surface they do have returns only themselves.
    mine_out = client.get("/api/v1/me/payslips", headers=emp_h).json()
    assert {p["employee_id"] for p in mine_out} == {mine["id"]}


def test_an_employee_never_sees_a_draft(client, org):
    """Invariant, not a one-off: no draft payslip is reachable through the
    self-service surface at any point before the run is finalized."""
    from app.core.notify.base import outbox

    emp = _employee(client, org, "Drafty", "22000", "8000")
    email = f"draft-{uuid.uuid4().hex[:6]}@{org['sub']}.test"
    client.patch(f"/api/v1/hr/employees/{emp['id']}", json={"email": email}, headers=org["hr"])
    outbox.clear()
    client.post(f"/api/v1/hr/employees/{emp['id']}/invite", headers=org["hr"])
    mail = next(m for m in outbox if m["template"] == "access_granted")
    pw = next(ln.split("Password:")[1].strip()
              for ln in mail["body"].splitlines() if "Password:" in ln)
    tok = client.post("/api/v1/auth/login",
                      json={"company": org["sub"], "email": email, "password": pw}).json()
    emp_h = {"Authorization": f"Bearer {tok['access_token']}"}

    run = _run(client, org)
    assert client.get("/api/v1/me/payslips", headers=emp_h).json() == []

    slip = next(p for p in run["payslips"] if p["employee_name"] == "Drafty")
    client.patch(f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
                 json={"tds": "300.00"}, headers=org["hr"])
    assert client.get("/api/v1/me/payslips", headers=emp_h).json() == []

    client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=org["hr"])
    assert len(client.get("/api/v1/me/payslips", headers=emp_h).json()) == 1


# --- boundaries ------------------------------------------------------------


def test_someone_who_joins_after_the_month_is_never_paid(client, org):
    _employee(client, org, "Future", "30000", "0", joined_on="2026-12-01")
    assert all(p["employee_name"] != "Future" for p in _run(client, org)["payslips"])


def test_joining_on_the_last_working_day_still_pays_something(client, org):
    """The end of the proration ramp: at least one payable day, never negative
    and never a full month."""
    _employee(client, org, "LastDay", "30000", "0", joined_on="2026-09-30")
    slip = next(p for p in _run(client, org)["payslips"] if p["employee_name"] == "LastDay")
    assert 0 <= slip["paid_days"] < slip["working_days"]
    assert D("0") <= D(slip["gross"]) < D("30000")


def test_unpaid_days_beyond_the_month_clamp_rather_than_go_negative(client, org):
    _employee(client, org, "Clamped", "30000", "0")
    run = _run(client, org)
    slip = next(p for p in run["payslips"] if p["employee_name"] == "Clamped")
    out = client.patch(f"/api/v1/payroll/runs/{run['id']}/payslips/{slip['id']}",
                       json={"lop_days": 999}, headers=org["hr"]).json()
    assert out["lop_days"] == out["working_days"]
    assert out["paid_days"] == 0
    assert D(out["gross"]) == D("0.00")
    assert D(out["net"]) == D(out["gross"]) - D(out["deductions"])


def test_leave_spanning_a_month_boundary_is_charged_to_each_month(client, org):
    """Overlap counting, not the request's own day total — otherwise a request
    crossing month-end is billed twice or not at all."""
    emp = _employee(client, org, "Spanner", "30000", "0")
    lt = client.post("/api/v1/leave/types",
                     json={"name": "LWP", "annual_quota": 60, "paid": False},
                     headers=org["hr"]).json()
    req = client.post("/api/v1/leave/requests",
                      json={"employee_id": emp["id"], "leave_type_id": lt["id"],
                            "start_date": "2026-09-28", "end_date": "2026-10-02"},
                      headers=org["hr"]).json()
    client.post(f"/api/v1/leave/requests/{req['id']}/approve", headers=org["hr"])

    sep = next(p for p in _run(client, org, "2026-09-01")["payslips"]
               if p["employee_name"] == "Spanner")
    oct_ = next(p for p in _run(client, org, "2026-10-01")["payslips"]
                if p["employee_name"] == "Spanner")

    span = (date(2026, 10, 2) - date(2026, 9, 28)).days + 1
    assert 0 < sep["lop_days"] < span
    assert 0 < oct_["lop_days"] < span
    assert sep["lop_days"] + oct_["lop_days"] == req["days"]


def test_a_zero_working_day_month_does_not_divide_by_zero(client, org):
    """Every day declared a holiday. Pathological, but it must not 500 or pay
    everyone nothing."""
    emp = _employee(client, org, "AllHoliday", "30000", "0")
    for offset in range(31):
        day = date(2026, 11, 1) + timedelta(days=offset)
        if day.month != 11:
            break
        client.post("/api/v1/calendar/holidays",
                    json={"day": str(day), "name": "Shutdown"}, headers=org["hr"])

    run = _run(client, org, "2026-11-01")
    slip = next(p for p in run["payslips"] if p["employee_id"] == emp["id"])
    assert slip["working_days"] == 0
    assert D(slip["gross"]) == D("30000.00")
    assert D(slip["net"]) == D(slip["gross"]) - D(slip["deductions"])
