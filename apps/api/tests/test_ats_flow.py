"""End-to-end ATS flow against a real (RLS-enabled) Postgres, including the
spec's automatic email actions (dev outbox, no real SMTP needed).

Requires the compose Postgres up and migrated:
    docker compose up -d db && (cd apps/api && alembic upgrade head)
Skips cleanly if no DB is reachable, so `pytest` still works without Docker.
"""
import pytest
from sqlalchemy import text

from app.core.db import engine
from app.core.notify.base import outbox
from tests.conftest import make_pdf


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


def test_full_flow(client, headers):
    outbox.clear()

    # 1. create a job
    r = client.post(
        "/api/v1/ats/jobs",
        json={"title": "Backend Engineer", "required_skills": ["python", "react"],
              "preferred_skills": ["aws"], "min_experience_years": 3},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]

    # 2. apply (webhook front door) with a resume PDF + candidate email
    pdf = make_pdf("Python React AWS engineer, 6 years experience. B.Tech IIT.")
    r = client.post(
        "/api/v1/ats/apply",
        files={"file": ("resume.pdf", pdf, "application/pdf")},
        data={"job_id": job_id, "full_name": "Asha Rao", "email": "asha@example.com"},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    app_id = r.json()["application_id"]
    candidate_id = r.json()["candidate_id"]
    assert r.json()["status"] == "received"

    # 2a. "Application Received" email fired automatically (spec: every candidate).
    received = [m for m in outbox if m["template"] == "application_received"]
    assert len(received) == 1
    assert received[0]["to"] == "asha@example.com"

    # 3. screen → deterministic Tier A for a strong match
    r = client.post(f"/api/v1/ats/applications/{app_id}/screen", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "shortlisted"
    assert body["tier"] == "A"
    assert body["recommended_action"] == "Immediate Interview"

    # 3a. Tier A → HR gets a heads-up now (spec). The candidate's scheduling
    # link goes out once HR actually proposes slots — see test_interview_scheduling.py.
    hr_alerts = [m for m in outbox if m["template"] == "hr_alert"]
    assert len(hr_alerts) == 1 and "Tier A" in hr_alerts[0]["subject"]
    assert not any(m["template"] == "interview_invite" for m in outbox)

    # 4. read it back
    r = client.get(f"/api/v1/ats/applications/{app_id}", headers=headers)
    assert r.json()["tier"] == "A"

    # 5. dedup — same resume returns the SAME candidate (no re-upload, no
    # re-OCR/re-screen — the cost lever), even though this is a new application
    # and so still gets its own "received" email.
    r = client.post(
        "/api/v1/ats/apply",
        files={"file": ("resume.pdf", pdf, "application/pdf")},
        data={"job_id": job_id, "email": "asha@example.com"},
        headers=headers,
    )
    assert r.json()["candidate_id"] == candidate_id
    assert len([m for m in outbox if m["template"] == "application_received"]) == 2


def test_bad_resume_alerts_hr(client, headers):
    outbox.clear()
    # An empty PDF (no text layer) → extraction yields nothing → "bad resume" path.
    empty_pdf = make_pdf("")
    r = client.post(
        "/api/v1/ats/apply",
        files={"file": ("blank.pdf", empty_pdf, "application/pdf")},
        data={"email": "candidate@example.com"},
        headers=headers,
    )
    assert r.status_code == 202
    assert r.json()["status"] == "needs_review"
    # HR alerted, candidate NOT told "received" for a resume we can't screen.
    assert any(m["template"] == "hr_alert" for m in outbox)
    assert not any(m["template"] == "application_received" for m in outbox)
