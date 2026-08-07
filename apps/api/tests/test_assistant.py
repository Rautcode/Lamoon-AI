"""Lumo: tools, the keyword fallback, and the model tool-calling loop.

The one thing NOT covered here is the network hop to Gemini itself — no API
key exists in this environment (same boundary as OAuth). Everything around it
is covered with a fake provider: tool selection, execution against real data,
the multi-step loop, error recovery, and the grounding property that items
come from tools rather than from the model.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.ai.provider import ModelTurn, ToolCall
from app.core.config import get_settings
from app.core.db import engine
from app.main import app
from app.modules.assistant import service


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


@pytest.fixture
def company(client):
    """Fresh tenant per test so counts are exact and nothing leaks between runs."""
    sub = f"lumo-{uuid.uuid4().hex[:8]}"
    body = {
        "company_name": "Lumo Co", "subdomain": sub,
        "email": f"admin@{sub}.test", "password": "pw123456",
    }
    client.post("/api/v1/auth/bootstrap", json=body)
    login = client.post(
        "/api/v1/auth/login",
        json={"company": sub, "email": body["email"], "password": "pw123456"},
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    emp = client.post(
        "/api/v1/hr/employees", json={"full_name": "Asha Rao"}, headers=headers
    ).json()
    return {"headers": headers, "employee_id": emp["id"], "sub": sub}


def ask(client, headers, question: str) -> dict:
    r = client.post("/api/v1/assistant/ask", json={"question": question}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- endpoint + fallback (no API key in this environment) -------------------


def test_requires_auth(client):
    assert client.post("/api/v1/assistant/ask", json={"question": "hi"}).status_code == 401


def test_headcount_from_real_data(client, company):
    out = ask(client, company["headers"], "How many people do we have?")
    assert "1" in out["text"]
    assert any(i["title"] == "Asha Rao" for i in out["items"])
    assert out["model_used"] is False  # no key configured — honest about it


def test_leave_answers_reflect_actual_state(client, company):
    headers, emp = company["headers"], company["employee_id"]

    out = ask(client, headers, "Who's on leave today?")
    assert "Nobody is on leave" in out["text"]

    lt = client.post(
        "/api/v1/leave/types", json={"name": "Sick", "annual_quota": 10}, headers=headers
    ).json()
    today = date.today()
    req = client.post(
        "/api/v1/leave/requests",
        json={
            "employee_id": emp, "leave_type_id": lt["id"],
            "start_date": str(today), "end_date": str(today + timedelta(days=2)),
        },
        headers=headers,
    ).json()

    # Pending is not "on leave" — only an approved request counts.
    assert "Nobody is on leave" in ask(client, headers, "Who's on leave today?")["text"]
    pending = ask(client, headers, "Show pending leave requests")
    assert "1 leave request needs" in pending["text"]
    assert any(i["title"] == "Asha Rao" for i in pending["items"])

    client.post(f"/api/v1/leave/requests/{req['id']}/approve", headers=headers)
    out = ask(client, headers, "Who's on leave today?")
    assert "1 person is on leave" in out["text"]
    assert any(i["title"] == "Asha Rao" for i in out["items"])


def test_person_lookup_by_name(client, company):
    out = ask(client, company["headers"], "Tell me about Asha")
    assert "Asha Rao" in out["text"]
    assert out["items"][0]["href"].startswith("/people/")


def test_unrecognised_question_is_honest(client, company):
    out = ask(client, company["headers"], "what is the airspeed velocity of a swallow")
    assert out["unmatched"] is True
    assert "can't answer that one yet" in out["text"]
    assert len(out["items"]) > 0  # offers what it CAN do


def test_tenant_isolation(client, company):
    """Lumo runs through the same RLS session as everything else, so it must
    not see another company's people."""
    other_sub = f"lumo-{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/v1/auth/bootstrap",
        json={
            "company_name": "Other", "subdomain": other_sub,
            "email": f"admin@{other_sub}.test", "password": "pw123456",
        },
    )
    other = client.post(
        "/api/v1/auth/login",
        json={"company": other_sub, "email": f"admin@{other_sub}.test", "password": "pw123456"},
    ).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}

    out = ask(client, other_headers, "Tell me about Asha")
    assert "Asha Rao" not in out["text"]
    assert not any(i["title"] == "Asha Rao" for i in out["items"])


# --- model path (fake provider) --------------------------------------------


class FakeProvider:
    """Drives the tool-calling loop without touching a network. `script` is a
    list of ModelTurns returned in order."""

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    async def converse(self, *, system, messages, tools):
        self.seen.append(list(messages))
        return self.script.pop(0) if self.script else ModelTurn(text="done", tool_calls=[])

    async def analyze(self, **kw):  # unused here
        raise NotImplementedError

    async def embed(self, texts):
        raise NotImplementedError


@pytest.fixture
def with_key():
    """service.ask only takes the model path when a key is configured."""
    s = get_settings()
    s.gemini_api_key = "test-fake-key"
    try:
        yield
    finally:
        s.gemini_api_key = ""


def _use(provider):
    from app.core.ai.provider import get_ai_provider

    app.dependency_overrides[get_ai_provider] = lambda: provider
    return lambda: app.dependency_overrides.pop(get_ai_provider, None)


def test_model_path_calls_tool_then_answers(client, company, with_key):
    provider = FakeProvider(
        [
            ModelTurn(text=None, tool_calls=[ToolCall(name="headcount", args={})]),
            ModelTurn(text="You have one person on the team.", tool_calls=[]),
        ]
    )
    undo = _use(provider)
    try:
        out = ask(client, company["headers"], "how big is the team?")
        assert out["text"] == "You have one person on the team."
        assert out["model_used"] is True
        # The prose came from the model; the items came from the TOOL.
        assert any(i["title"] == "Asha Rao" for i in out["items"])
        # The tool's real result was fed back to the model.
        last = provider.seen[-1]
        assert any(m.get("role") == "tool" and m["result"]["total"] == 1 for m in last)
    finally:
        undo()


def test_model_cannot_invent_clickable_results(client, company, with_key):
    """Even when the model names someone who doesn't exist, no fabricated
    person becomes a link — items are built from tool output only."""
    provider = FakeProvider(
        [
            ModelTurn(text=None, tool_calls=[ToolCall(name="headcount", args={})]),
            ModelTurn(text="Bob Fictional and Jane Nonexistent are here.", tool_calls=[]),
        ]
    )
    undo = _use(provider)
    try:
        out = ask(client, company["headers"], "who works here?")
        titles = [i["title"] for i in out["items"]]
        assert "Bob Fictional" not in titles
        assert "Jane Nonexistent" not in titles
        assert titles == ["Asha Rao"]
    finally:
        undo()


def test_model_failure_falls_back_to_keywords(client, company, with_key):
    class Exploding(FakeProvider):
        async def converse(self, **kw):
            raise RuntimeError("gemini is down")

    undo = _use(Exploding([]))
    try:
        out = ask(client, company["headers"], "How many people do we have?")
        # Degraded to the deterministic path rather than 500ing.
        assert out["model_used"] is False
        assert "1" in out["text"]
    finally:
        undo()


def test_loop_is_bounded(client, company, with_key):
    """A model that only ever asks for tools must not spin forever."""
    provider = FakeProvider(
        [ModelTurn(text=None, tool_calls=[ToolCall(name="headcount", args={})])] * 10
    )
    undo = _use(provider)
    try:
        out = ask(client, company["headers"], "How many people do we have?")
        assert len(provider.seen) <= service.MAX_STEPS
        assert out["model_used"] is False  # gave up, answered deterministically
    finally:
        undo()


def test_unknown_tool_does_not_crash(client, company, with_key):
    provider = FakeProvider(
        [
            ModelTurn(text=None, tool_calls=[ToolCall(name="launch_missiles", args={})]),
            ModelTurn(text="I can't do that.", tool_calls=[]),
        ]
    )
    undo = _use(provider)
    try:
        out = ask(client, company["headers"], "do something weird")
        assert out["text"] == "I can't do that."
    finally:
        undo()
