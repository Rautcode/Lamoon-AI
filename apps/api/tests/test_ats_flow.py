"""End-to-end ATS flow against a real (RLS-enabled) Postgres.

Requires the compose Postgres up and migrated:
    docker compose up -d db && (cd apps/api && alembic upgrade head)
Skips cleanly if no DB is reachable, so `pytest` still works without Docker.
"""
import pytest
from sqlalchemy import text

from app.core.db import engine
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
    # 1. create a job
    r = client.post(
        "/api/v1/ats/jobs",
        json={"title": "Backend Engineer", "required_skills": ["python", "react"],
              "preferred_skills": ["aws"], "min_experience_years": 3},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]

    # 2. apply (webhook front door) with a resume PDF
    pdf = make_pdf("Python React AWS engineer, 6 years experience. B.Tech IIT.")
    r = client.post(
        "/api/v1/ats/apply",
        files={"file": ("resume.pdf", pdf, "application/pdf")},
        data={"job_id": job_id, "full_name": "Asha Rao"},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    app_id = r.json()["application_id"]
    candidate_id = r.json()["candidate_id"]
    assert r.json()["status"] == "received"

    # 3. screen → deterministic Tier A for a strong match
    r = client.post(f"/api/v1/ats/applications/{app_id}/screen", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "scored"
    assert body["tier"] == "A"
    assert body["recommended_action"] == "Immediate Interview"

    # 4. read it back
    r = client.get(f"/api/v1/ats/applications/{app_id}", headers=headers)
    assert r.json()["tier"] == "A"

    # 5. dedup — same resume returns the SAME candidate, no re-upload
    r = client.post(
        "/api/v1/ats/apply",
        files={"file": ("resume.pdf", pdf, "application/pdf")},
        data={"job_id": job_id},
        headers=headers,
    )
    assert r.json()["candidate_id"] == candidate_id
