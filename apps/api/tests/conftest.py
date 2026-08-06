"""Test fixtures. FakeProvider lets the full pipeline run without a Gemini key.
`headers` performs a real bootstrap+login and returns a Bearer token — the dev
X-Company-Id shim is gone, so tests exercise real auth."""
import fitz  # pymupdf
import pytest
from fastapi.testclient import TestClient

from app.core.ai.provider import AIResult, get_ai_provider
from app.main import app
from app.modules.ats.schemas import Screening

COMPANY = {
    "company_name": "Acme", "subdomain": "acme",
    "email": "admin@acme.test", "password": "pw123456",
}
CREDS = {"company": "acme", "email": "admin@acme.test", "password": "pw123456"}


class FakeProvider:
    async def analyze(self, *, prompt_key, prompt_ver, inputs, output_schema, cache_key=None):
        s = Screening(
            skills=["Python", "React", "AWS"], years_experience=6, education="B.Tech, IIT",
            summary="Strong full-stack engineer.", technical_score=8.5, experience_score=8.0,
            education_score=7.5, communication_score=8.0, overall_ai_score=8.5,
        )
        return AIResult(
            output=s, model="fake", prompt_version=prompt_ver, recipe_hash="fake",
            tokens_in=0, tokens_out=0, cost=0.0, cache_hit=False,
        )

    async def embed(self, texts):
        return [[0.0] for _ in texts]


@pytest.fixture
def client():
    app.dependency_overrides[get_ai_provider] = lambda: FakeProvider()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def headers(client):
    """Real Bearer token via bootstrap + login (idempotent)."""
    client.post("/api/v1/auth/bootstrap", json=COMPANY)
    r = client.post("/api/v1/auth/login", json=CREDS)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    return doc.tobytes()
