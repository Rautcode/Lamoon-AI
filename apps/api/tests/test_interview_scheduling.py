"""Interview scheduling: HR proposes slots -> candidate books via a magic
link (no login) -> confirmations fire -> a second booking attempt on the same
slot is rejected -> the reminder workflow sends once, not twice.
"""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.core.notify.base import outbox
from app.modules.ats.tasks import send_interview_reminders_for_company
from tests.conftest import make_pdf


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


def _apply_and_screen(client, headers, job_id: str, email: str) -> str:
    pdf = make_pdf("Python React AWS engineer, 6 years experience. B.Tech IIT.")
    r = client.post(
        "/api/v1/ats/apply",
        files={"file": ("resume.pdf", pdf, "application/pdf")},
        data={"job_id": job_id, "full_name": "Asha Rao", "email": email},
        headers=headers,
    )
    app_id = r.json()["application_id"]
    r = client.post(f"/api/v1/ats/applications/{app_id}/screen", headers=headers)
    assert r.json()["tier"] == "A"
    return app_id


def test_full_scheduling_flow(client, headers):
    outbox.clear()
    r = client.post("/api/v1/ats/jobs", json={"title": "Backend Engineer"}, headers=headers)
    job_id = r.json()["id"]
    app_id = _apply_and_screen(client, headers, job_id, "candidate@example.com")

    # HR proposes two slots.
    slot_a = datetime.now(UTC) + timedelta(hours=2)
    slot_b = datetime.now(UTC) + timedelta(days=3)

    def _iso_pair(start: datetime) -> dict:
        return {"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()}

    r = client.post(
        f"/api/v1/ats/applications/{app_id}/interview-slots",
        json={"slots": [_iso_pair(slot_a), _iso_pair(slot_b)]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    token = r.json()["booking_token"]
    slots = r.json()["slots"]
    assert len(slots) == 2

    # Candidate got the real booking link (this is the moment it's sent, not screening).
    invites = [m for m in outbox if m["template"] == "interview_invite"]
    assert len(invites) == 1 and invites[0]["to"] == "candidate@example.com"
    assert token in invites[0]["body"]

    # Public: no auth header at all — the token IS the auth.
    r = client.get(f"/api/v1/public/interviews/{token}")
    assert r.status_code == 200, r.text
    assert r.json()["job_title"] == "Backend Engineer"
    assert len(r.json()["slots"]) == 2
    winning_slot = r.json()["slots"][0]["id"]

    outbox.clear()
    r = client.post(f"/api/v1/public/interviews/{token}/book", json={"slot_id": winning_slot})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "booked"

    # Confirmation to candidate + HR heads-up.
    assert any(m["template"] == "interview_confirmed" for m in outbox)
    assert any(m["template"] == "hr_alert" and "booked" in m["subject"].lower() for m in outbox)

    # Application reflects the booking; the sibling slot is no longer offered.
    app_state = client.get(f"/api/v1/ats/applications/{app_id}", headers=headers).json()
    assert app_state["status"] == "interview_scheduled"
    remaining = client.get(f"/api/v1/public/interviews/{token}").json()["slots"]
    assert remaining == []  # both slots gone: one booked, the other auto-cancelled

    # Re-booking the SAME slot fails — no double-booking.
    r = client.post(f"/api/v1/public/interviews/{token}/book", json={"slot_id": winning_slot})
    assert r.status_code == 409


def test_invalid_token_404(client):
    assert client.get("/api/v1/public/interviews/not-a-real-token").status_code == 404


def test_reminder_sent_once(client, headers):
    r = client.post("/api/v1/ats/jobs", json={"title": "Reminder Role"}, headers=headers)
    job_id = r.json()["id"]
    app_id = _apply_and_screen(client, headers, job_id, "remind-me@example.com")

    soon = datetime.now(UTC) + timedelta(hours=2)  # inside the 24h reminder window
    soon_slot = {"starts_at": soon.isoformat(), "ends_at": (soon + timedelta(hours=1)).isoformat()}
    r = client.post(
        f"/api/v1/ats/applications/{app_id}/interview-slots",
        json={"slots": [soon_slot]},
        headers=headers,
    )
    token = r.json()["booking_token"]
    slot_id = r.json()["slots"][0]["id"]
    client.post(f"/api/v1/public/interviews/{token}/book", json={"slot_id": slot_id})

    import jwt as pyjwt

    from app.core.config import get_settings

    s = get_settings()
    cid = pyjwt.decode(
        headers["Authorization"].split()[1], s.jwt_secret, algorithms=[s.jwt_alg]
    )["cid"]

    class _Notifier:
        async def send(self, *, to, template, ctx, channel="email"):
            outbox.append({"to": to, "template": template})

    db = SessionLocal()
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": cid})
    try:
        outbox.clear()
        # >=1, not ==1: a shared dev DB may carry other due interviews from
        # earlier tests in this same run — assert on OUR candidate specifically.
        n1 = asyncio.run(send_interview_reminders_for_company(db, cid, _Notifier()))
        assert n1 >= 1
        assert any(
            m["to"] == "remind-me@example.com" and m["template"] == "interview_reminder"
            for m in outbox
        )

        outbox.clear()
        n2 = asyncio.run(send_interview_reminders_for_company(db, cid, _Notifier()))
        assert n2 == 0  # reminder_sent_at now guards the second sweep, company-wide
        assert outbox == []
        db.commit()
    finally:
        db.close()
