"""Artifacts — generated files as records.

The thing being protected here is not "can we write a file". It is that a file
attached to a finalized payroll run is EVIDENCE: it must not change behind a
link somebody already holds, it must be provably the same bytes when fetched
in two years, and a failed render must be visible rather than absent.
"""
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import text

from app.core.artifacts.service import storage_key
from app.core.db import engine
from app.core.storage.base import LocalBlobStore, checksum
from app.modules.payroll.render import DEDUCTION_CODES

API = "/api/v1"


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


endpoint = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


# --- pure: keys, checksums, the local store ---------------------------------


def test_storage_key_is_deterministic():
    """Same inputs, same place. A key that drifts makes an artifact row point
    at nothing after a redeploy."""
    args = dict(
        company_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        kind="payroll_register", period=date(2026, 8, 1),
        scope_key="run:abc", version=1, extension="csv",
    )
    assert storage_key(**args) == storage_key(**args)
    assert "/payroll_register/2026-08/" in storage_key(**args)
    assert storage_key(**args).endswith("-v1.csv")


def test_version_changes_the_key():
    """v2 must not land on v1's bytes — that is the whole immutability story."""
    base = dict(
        company_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        kind="payroll_register", period=date(2026, 8, 1),
        scope_key="run:abc", extension="csv",
    )
    assert storage_key(**base, version=1) != storage_key(**base, version=2)


def test_different_scopes_do_not_collide():
    base = dict(
        company_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        kind="payroll_register", period=date(2026, 8, 1), version=1, extension="csv",
    )
    assert storage_key(**base, scope_key="run:a") != storage_key(**base, scope_key="run:b")


def test_checksum_detects_a_single_changed_byte():
    assert checksum(b"gross,41000.00") != checksum(b"gross,41000.01")


@pytest.mark.asyncio
async def test_local_store_round_trip(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    assert await store.exists("a/b.csv") is False
    await store.put("a/b.csv", b"hello", content_type="text/csv")
    assert await store.exists("a/b.csv") is True
    assert await store.get("a/b.csv") == b"hello"
    await store.delete("a/b.csv")
    assert await store.exists("a/b.csv") is False


@pytest.mark.asyncio
async def test_local_store_refuses_to_escape_its_root(tmp_path):
    """Keys are built by this application, never by a user — but a traversal
    here would write outside the storage root, which is cheap to rule out."""
    store = LocalBlobStore(str(tmp_path))
    with pytest.raises(ValueError, match="escapes the storage root"):
        await store.put("../outside.csv", b"x", content_type="text/csv")


# --- the register renderer is pure too ---------------------------------------


def test_register_has_a_header_and_a_total_row():
    from types import SimpleNamespace as NS

    from app.modules.payroll.render import payroll_register

    slips = [
        NS(employee_name="Bala", working_days=22, paid_days=22, lop_days=0,
           gross=D("40000.00"), deductions=D("2000.00"), net=D("38000.00"),
           employer_cost=D("42000.00"),
           breakdown={"deductions": [{"code": "EPF", "amount": "1800.00"}]}),
        NS(employee_name="Asha", working_days=22, paid_days=20, lop_days=2,
           gross=D("30000.00"), deductions=D("1500.00"), net=D("28500.00"),
           employer_cost=D("31500.00"), breakdown={"deductions": []}),
    ]
    data, content_type, ext = payroll_register(slips, period_label="Aug 2026")
    text_out = data.decode("utf-8-sig")

    assert content_type == "text/csv" and ext == "csv"
    assert text_out.splitlines()[0].startswith("Employee,Period,Working days")
    # Sorted by name, so the register reads the way a reviewer scans it.
    assert text_out.splitlines()[1].startswith("Asha")
    assert "2 employees" in text_out
    # Totals, because the first thing anybody does is check against the bank.
    assert "70000.00" in text_out and "66500.00" in text_out
    # A deduction pulled out of the JSONB breakdown by code.
    assert "1800.00" in text_out


def test_register_writes_a_bom_for_excel():
    """Excel on Windows reads a plain UTF-8 CSV as the ANSI codepage and
    mangles every non-ASCII name in the file."""
    from types import SimpleNamespace as NS

    from app.modules.payroll.render import payroll_register

    data, _, _ = payroll_register(
        [NS(employee_name="Lakshmī Devī", working_days=22, paid_days=22, lop_days=0,
            gross=D("1.00"), deductions=D("0.00"), net=D("1.00"),
            employer_cost=D("1.00"), breakdown={"deductions": []})],
        period_label="Aug 2026",
    )
    assert data.startswith(b"\xef\xbb\xbf")
    assert "Lakshmī Devī" in data.decode("utf-8-sig")


# --- endpoints ---------------------------------------------------------------


@pytest.fixture
def org(client):
    sub = f"art-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Art Co", "subdomain": sub,
        "email": admin, "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": sub, "email": admin, "password": "pw123456",
    }).json()["access_token"]
    hr = {"Authorization": f"Bearer {tok}"}

    # PF defaults OFF (deducting from somebody not covered is worse than not
    # deducting), so a register test that wants a real PF column has to ask
    # for one. Without this the PF column is legitimately zero and the
    # code-mismatch guard below would pass while proving nothing.
    settings = client.get(f"{API}/payroll/settings", headers=hr).json()
    client.put(f"{API}/payroll/settings", json={**settings, "pf_enabled": True}, headers=hr)

    basic = client.post(f"{API}/payroll/components", json={
        "code": "BASIC", "name": "Basic", "kind": "earning",
        "wage_basis": "wages", "esi_wage": True, "taxable": True, "sequence": 10,
    }, headers=hr).json()
    emp = client.post(f"{API}/hr/employees", json={
        "full_name": "Asha Rao", "joined_on": "2026-01-01",
        "date_of_birth": "1992-05-05", "pf_first_joined_on": "2013-01-01",
    }, headers=hr).json()
    client.put(f"{API}/payroll/employees/{emp['id']}/salary", json={
        "components": [{"component_id": basic["id"], "amount": "40000"}],
    }, headers=hr)
    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=hr).json()
    return {"hr": hr, "sub": sub, "run": run, "employee": emp}


@endpoint
def test_generating_a_register_records_provenance(client, org):
    r = client.post(f"{API}/payroll/runs/{org['run']['id']}/register", headers=org["hr"])
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "ready"
    assert a["version"] == 1
    assert a["size_bytes"] > 0
    assert len(a["checksum_sha256"]) == 64
    assert a["filename"] == "payroll-register-2026-08.csv"


@endpoint
def test_regenerating_makes_v2_and_leaves_v1_alone(client, org):
    """Evidence whose bytes can change behind a link somebody already holds is
    not evidence."""
    first = client.post(f"{API}/payroll/runs/{org['run']['id']}/register",
                        headers=org["hr"]).json()
    second = client.post(f"{API}/payroll/runs/{org['run']['id']}/register",
                         headers=org["hr"]).json()

    assert second["version"] == first["version"] + 1
    assert second["id"] != first["id"]

    still_there = client.get(f"{API}/artifacts/{first['id']}", headers=org["hr"])
    assert still_there.status_code == 200
    assert still_there.json()["checksum_sha256"] == first["checksum_sha256"]


@endpoint
def test_register_uses_the_codes_the_engine_actually_writes(client, org):
    """The register shipped once with "PF" where the engine writes "EPF", so
    the PF column read 0.00 for everybody while ₹1,800 a head was being
    deducted. Nothing failed — a chartered accountant reconciling that column
    against a PF challan would have been the error handler.

    The unit test above missed it because it built its own breakdown and
    encoded the same mistake. This one renders a REAL payslip, so the two
    sides cannot drift apart in agreement.
    """
    a = client.post(f"{API}/payroll/runs/{org['run']['id']}/register",
                    headers=org["hr"]).json()
    body = client.get(f"{API}/artifacts/{a['id']}/download",
                      headers=org["hr"]).content.decode("utf-8-sig")

    header, row = body.splitlines()[0].split(","), body.splitlines()[1].split(",")
    cells = dict(zip(header, row, strict=False))

    assert D(cells["Gross"]) > 0
    assert D(cells["PF"]) > 0, (
        "PF is zero on a ₹40,000 salary — the register is reading a code the "
        "engine does not write"
    )
    # Every itemised deduction must be accounted for in the total.
    itemised = sum(D(cells[label]) for _, label in DEDUCTION_CODES)
    assert itemised <= D(cells["Total deductions"])
    assert D(cells["Gross"]) - D(cells["Total deductions"]) == D(cells["Net"])


@endpoint
def test_download_returns_the_bytes_and_verifies_the_checksum(client, org):
    a = client.post(f"{API}/payroll/runs/{org['run']['id']}/register",
                    headers=org["hr"]).json()
    got = client.get(f"{API}/artifacts/{a['id']}/download", headers=org["hr"])
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("text/csv")
    assert "payroll-register-2026-08.csv" in got.headers["content-disposition"]
    assert checksum(got.content) == a["checksum_sha256"]
    assert b"Asha Rao" in got.content


@endpoint
def test_a_run_with_no_payslips_is_refused_rather_than_making_an_empty_file(client, org):
    """An empty register that looks successful is worse than an error — it
    gets filed."""
    empty = f"emp-{uuid.uuid4().hex[:8]}"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Empty", "subdomain": empty,
        "email": f"admin@{empty}.test", "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": empty, "email": f"admin@{empty}.test", "password": "pw123456",
    }).json()["access_token"]
    hr = {"Authorization": f"Bearer {tok}"}
    run = client.post(f"{API}/payroll/runs", json={"period": "2026-08-01"},
                      headers=hr).json()

    r = client.post(f"{API}/payroll/runs/{run['id']}/register", headers=hr)
    assert r.status_code == 422


@endpoint
def test_artifacts_are_listed_newest_first_and_filtered_by_run(client, org):
    client.post(f"{API}/payroll/runs/{org['run']['id']}/register", headers=org["hr"])
    client.post(f"{API}/payroll/runs/{org['run']['id']}/register", headers=org["hr"])

    rows = client.get(f"{API}/artifacts?run_id={org['run']['id']}", headers=org["hr"]).json()
    assert len(rows) == 2
    assert [r["version"] for r in rows] == [2, 1]


@endpoint
def test_another_tenant_cannot_read_or_download_an_artifact(client, org):
    """An artifact id is a handle, not a capability."""
    a = client.post(f"{API}/payroll/runs/{org['run']['id']}/register",
                    headers=org["hr"]).json()

    other = f"art-{uuid.uuid4().hex[:8]}"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Other", "subdomain": other,
        "email": f"admin@{other}.test", "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": other, "email": f"admin@{other}.test", "password": "pw123456",
    }).json()["access_token"]
    theirs = {"Authorization": f"Bearer {tok}"}

    assert client.get(f"{API}/artifacts/{a['id']}", headers=theirs).status_code == 404
    assert client.get(f"{API}/artifacts/{a['id']}/download", headers=theirs).status_code == 404


@endpoint
def test_manager_cannot_download_payroll_artifacts(client, org):
    from app.core.auth.permissions import ROLE_PERMISSIONS

    assert "payroll.read" not in ROLE_PERMISSIONS["manager"]
