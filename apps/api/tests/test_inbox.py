"""The task inbox — what needs YOU, and does it go away when you fix it.

An inbox item is not a notification and not a log line. It is a durable,
deduplicated claim that one person must act, and it survives until the thing it
describes is no longer true.

Three properties carry the whole design, and each is a way the naive version
fails:

  DEDUPLICATED   re-deriving the same exception every night must not produce
                 thirty rows and thirty emails.
  RECONCILED     fixing the underlying problem ANY way — not just by clicking
                 through the inbox — must close the item. An inbox that only
                 grows is a worse Excel sheet.
  SCOPED         a manager approves hours, never money. A digest that leaks a
                 salary figure to somebody without payroll.read is a security
                 bug, not a formatting one.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.core.inbox.service import Item, order_key

API = "/api/v1"


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


endpoint = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


# --- ordering is pure: what blocks payroll soonest comes first ---------------


def item(severity="review", due_on=None, first_seen=None):
    return Item(
        kind="attendance.unexplained", dedupe_key="x", title="t", detail=None,
        severity=severity, entity="employee", entity_id=None, href=None,
        due_on=due_on,
    )


def test_blocking_outranks_review_whatever_the_date():
    """Severity first. A blocking item due next week still beats a review item
    due tomorrow — one stops payroll, the other does not."""
    blocking = order_key(item("blocking", due_on=date(2026, 9, 30)), today=date(2026, 8, 14))
    review = order_key(item("review", due_on=date(2026, 8, 15)), today=date(2026, 8, 14))
    assert blocking < review


def test_sooner_due_date_comes_first_within_a_severity():
    soon = order_key(item("review", due_on=date(2026, 8, 15)), today=date(2026, 8, 14))
    later = order_key(item("review", due_on=date(2026, 9, 15)), today=date(2026, 8, 14))
    assert soon < later


def test_an_item_with_no_due_date_sorts_after_dated_ones():
    """Undated does not mean urgent. It means nobody said when."""
    dated = order_key(item("review", due_on=date(2026, 12, 31)), today=date(2026, 8, 14))
    undated = order_key(item("review", due_on=None), today=date(2026, 8, 14))
    assert dated < undated


# --- endpoints ---------------------------------------------------------------


@pytest.fixture
def org(client):
    """A company with a manager, and one report whose work needs approving."""
    from app.core.notify.base import outbox

    sub = f"inbox-{uuid.uuid4().hex[:8]}"
    admin = f"admin@{sub}.test"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Inbox Co", "subdomain": sub,
        "email": admin, "password": "pw123456",
    })
    hr_tok = client.post(f"{API}/auth/login", json={
        "company": sub, "email": admin, "password": "pw123456",
    }).json()["access_token"]
    hr = {"Authorization": f"Bearer {hr_tok}"}

    boss = client.post(f"{API}/hr/employees", json={
        "full_name": "Meena Boss", "email": f"boss@{sub}.test",
    }, headers=hr).json()
    worker = client.post(f"{API}/hr/employees", json={
        "full_name": "Ravi Worker", "email": f"ravi@{sub}.test",
        "reporting_manager_id": boss["id"], "joined_on": "2026-01-01",
    }, headers=hr).json()

    # Give the manager a login so the inbox has somebody to belong to.
    outbox.clear()
    client.post(f"{API}/hr/employees/{boss['id']}/invite", headers=hr)
    mail = next(m for m in outbox if m["template"] == "access_granted")
    pw = next(
        line.split("Password:")[1].strip()
        for line in mail["body"].splitlines() if "Password:" in line
    )
    boss_tok = client.post(f"{API}/auth/login", json={
        "company": sub, "email": f"boss@{sub}.test", "password": pw,
    }).json()["access_token"]

    return {
        "hr": hr, "sub": sub, "boss": boss, "worker": worker,
        "boss_headers": {"Authorization": f"Bearer {boss_tok}"},
    }


def raise_fact(client, org, day: str):
    """An unapproved work fact — something the manager must sign off."""
    return client.post(f"{API}/workforce/facts", json={
        "employee_id": org["worker"]["id"], "day": day,
        "status": "worked", "hours_worked": "8", "overtime_hours": "2",
    }, headers=org["hr"]).json()


@endpoint
def test_pending_work_reaches_the_manager_who_can_approve_it(client, org):
    """The routing rule: it goes to the person who can close it, not to a pool."""
    raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])

    mine = client.get(f"{API}/inbox", headers=org["boss_headers"]).json()
    assert len(mine) == 1
    assert mine[0]["kind"] == "workfact.pending"
    assert "Ravi Worker" in mine[0]["title"]
    assert mine[0]["state"] == "open"


@endpoint
def test_the_same_exception_seen_twice_does_not_become_two_items(client, org):
    """Re-deriving every night must not produce thirty rows and thirty emails."""
    raise_fact(client, org, "2026-08-03")
    for _ in range(3):
        client.post(f"{API}/inbox/sync", headers=org["hr"])

    mine = client.get(f"{API}/inbox", headers=org["boss_headers"]).json()
    assert len(mine) == 1
    assert mine[0]["seen_count"] >= 3, "it re-appeared, it did not re-create"


@endpoint
def test_fixing_the_underlying_problem_closes_the_item(client, org):
    """Resolved by approving the work, NOT by touching the inbox. An inbox that
    only grows is a worse Excel sheet."""
    fact = raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])
    assert len(client.get(f"{API}/inbox", headers=org["boss_headers"]).json()) == 1

    client.post(f"{API}/workforce/facts/approve",
                json={"ids": [fact["id"]]}, headers=org["hr"])
    client.post(f"{API}/inbox/sync", headers=org["hr"])

    assert client.get(f"{API}/inbox", headers=org["boss_headers"]).json() == []


@endpoint
def test_a_resolved_item_is_kept_not_deleted(client, org):
    """The trail of what needed attention is worth having."""
    fact = raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])
    client.post(f"{API}/workforce/facts/approve",
                json={"ids": [fact["id"]]}, headers=org["hr"])
    client.post(f"{API}/inbox/sync", headers=org["hr"])

    closed = client.get(f"{API}/inbox?state=resolved", headers=org["boss_headers"]).json()
    assert len(closed) == 1
    assert closed[0]["resolved_at"] is not None


@endpoint
def test_one_persons_inbox_is_not_anothers(client, org):
    """HR did not raise this work and must not be told to approve it."""
    raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])

    hr_inbox = client.get(f"{API}/inbox", headers=org["hr"]).json()
    assert all(i["kind"] != "workfact.pending" for i in hr_inbox)


@endpoint
def test_a_manager_item_carries_no_money(client, org):
    """A manager approves hours, never money. A salary figure in a manager's
    inbox is a security bug, not a formatting one."""
    raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])

    mine = client.get(f"{API}/inbox", headers=org["boss_headers"]).json()
    blob = (mine[0]["title"] + (mine[0]["detail"] or "")).lower()
    for forbidden in ("₹", "salary", "gross", "net pay", "ctc"):
        assert forbidden not in blob


@endpoint
def test_an_item_can_be_dismissed_by_its_owner(client, org):
    """Sometimes the right answer is "not a problem". Dismissal is recorded,
    and dismissing does not resurrect on the next sync."""
    raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])
    mine = client.get(f"{API}/inbox", headers=org["boss_headers"]).json()

    r = client.post(f"{API}/inbox/{mine[0]['id']}/dismiss",
                    json={"reason": "agreed offline"}, headers=org["boss_headers"])
    assert r.status_code == 200

    client.post(f"{API}/inbox/sync", headers=org["hr"])
    assert client.get(f"{API}/inbox", headers=org["boss_headers"]).json() == []


@endpoint
def test_somebody_else_cannot_dismiss_my_item(client, org):
    raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])
    mine = client.get(f"{API}/inbox", headers=org["boss_headers"]).json()

    r = client.post(f"{API}/inbox/{mine[0]['id']}/dismiss", json={}, headers=org["hr"])
    assert r.status_code == 404, "not even visible, let alone actionable"


@endpoint
def test_another_tenant_sees_nothing(client, org):
    raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])

    other = f"inbox-{uuid.uuid4().hex[:8]}"
    client.post(f"{API}/auth/bootstrap", json={
        "company_name": "Other", "subdomain": other,
        "email": f"admin@{other}.test", "password": "pw123456",
    })
    tok = client.post(f"{API}/auth/login", json={
        "company": other, "email": f"admin@{other}.test", "password": "pw123456",
    }).json()["access_token"]

    assert client.get(f"{API}/inbox",
                      headers={"Authorization": f"Bearer {tok}"}).json() == []


@endpoint
def test_ageing_is_visible(client, org):
    """"How long has this been sitting there" is the question that decides
    whether it escalates."""
    raise_fact(client, org, "2026-08-03")
    client.post(f"{API}/inbox/sync", headers=org["hr"])
    mine = client.get(f"{API}/inbox", headers=org["boss_headers"]).json()
    assert mine[0]["age_days"] == 0
    assert mine[0]["first_seen_at"] is not None
