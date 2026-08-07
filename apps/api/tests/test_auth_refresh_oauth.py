"""/auth/refresh and /auth/oauth/{provider}. OAuth's network call to a real
provider can't be tested without registered-app credentials (same situation
as Gemini) — everything around it is: state/CSRF, callback wiring, the
existing-account-only rule, and token issuance, against a fake client.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.auth.oauth import OAuthProfile
from app.core.config import get_settings
from app.core.db import engine
from app.main import app
from app.modules.auth.routes import oauth_client_dependency
from tests.conftest import COMPANY, CREDS


def _db_up() -> bool:
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="Postgres not reachable")


# ---------------------------------------------------------------- refresh ---


def test_refresh_issues_working_access_token(client, headers):
    client.post("/api/v1/auth/bootstrap", json=COMPANY)
    login = client.post("/api/v1/auth/login", json=CREDS).json()

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    # Not asserting the token STRING differs from login's: claims + exp (second
    # resolution) can legitimately be byte-identical if both happen in the same
    # second. What matters is that refresh succeeds and the token actually works.

    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


def test_refresh_rejects_an_access_token(client, headers):
    client.post("/api/v1/auth/bootstrap", json=COMPANY)
    login = client.post("/api/v1/auth/login", json=CREDS).json()
    # Passing the ACCESS token where a refresh token belongs must fail (typ check).
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": login["access_token"]})
    assert r.status_code == 401


def _set_admin_active(cid: str, user_id: str, active: bool) -> None:
    """Each call gets its OWN open_session(): set_config(..., is_local=true) is
    transaction-scoped, so reusing one session across two commits leaves the
    second write running with a reset (and, for a placeholder custom GUC,
    empty-string-not-NULL) company_id — this bit a scheduled-task test before
    and, worse, corrupted the shared dev DB's admin row when I first wrote
    this test without the lesson applied here too."""
    from app.core.db import open_session
    from app.modules.auth.models import User

    with open_session() as db:
        db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": cid})
        user = db.get(User, uuid.UUID(user_id))
        user.is_active = active


def test_refresh_rejects_deactivated_user(client, headers):
    import jwt as pyjwt

    from app.core.config import get_settings

    client.post("/api/v1/auth/bootstrap", json=COMPANY)
    login = client.post("/api/v1/auth/login", json=CREDS).json()

    s = get_settings()
    claims = pyjwt.decode(login["refresh_token"], s.jwt_secret, algorithms=[s.jwt_alg])

    _set_admin_active(claims["cid"], claims["sub"], False)
    try:
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
        assert r.status_code == 401
    finally:
        _set_admin_active(claims["cid"], claims["sub"], True)


# ------------------------------------------------------------------ oauth ---


@pytest.fixture
def google_configured():
    """No real GOOGLE_CLIENT_ID exists in this environment — same situation as
    Gemini. Fake a non-empty id just to pass the is_configured() gate; nothing
    here ever reaches Google (authorize_url is pure string-building, and the
    callback's exchange() is overridden with a fake client in these tests)."""
    s = get_settings()
    s.google_client_id = "test-fake-client-id"
    try:
        yield
    finally:
        s.google_client_id = ""


class _FakeOAuthClient:
    def __init__(self, email: str):
        self.email = email

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        return f"https://fake-provider.test/authorize?state={state}"

    async def exchange(self, code: str, redirect_uri: str) -> OAuthProfile:
        return OAuthProfile(email=self.email, name="Fake User")


def _override_oauth(email: str) -> None:
    app.dependency_overrides[oauth_client_dependency] = lambda: _FakeOAuthClient(email)


def test_oauth_start_redirects_with_state(client, google_configured):
    r = client.get(
        "/api/v1/auth/oauth/google/start", params={"company": "acme"}, follow_redirects=False
    )
    assert r.status_code in (302, 307)
    assert "state=" in r.headers["location"]


def test_oauth_unconfigured_provider_503(client):
    # No MICROSOFT_CLIENT_ID set in test env → the honest failure, not a
    # redirect to a provider that will reject an empty client_id.
    r = client.get(
        "/api/v1/auth/oauth/microsoft/start", params={"company": "acme"}, follow_redirects=False
    )
    assert r.status_code == 503


def test_oauth_providers_reports_configured_state(client, google_configured):
    r = client.get("/api/v1/auth/oauth/providers")
    assert r.status_code == 200
    assert r.json() == {"google": True, "microsoft": False}


def test_oauth_login_existing_user(client, headers, google_configured):
    """The callback is a browser redirect target, not an API endpoint — it
    never returns JSON. Success lands tokens in the URL FRAGMENT of a
    redirect to the frontend; nothing before the `#` (server logs, proxies)
    ever sees them."""
    client.post("/api/v1/auth/bootstrap", json=COMPANY)
    _override_oauth(COMPANY["email"])
    try:
        start = client.get(
            "/api/v1/auth/oauth/google/start", params={"company": "acme"}, follow_redirects=False
        )
        state = start.headers["location"].split("state=")[1]

        r = client.get(
            f"/api/v1/auth/oauth/google/callback?code=fake-code&state={state}",
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        location = r.headers["location"]
        assert location.startswith(get_settings().oauth_frontend_redirect)
        assert "#access_token=" in location and "refresh_token=" in location

        fragment = location.split("#", 1)[1]
        parsed = dict(pair.split("=", 1) for pair in fragment.split("&"))
        me = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {parsed['access_token']}"}
        )
        assert me.json()["role"] == "admin"
    finally:
        app.dependency_overrides.pop(oauth_client_dependency, None)


def test_oauth_unknown_email_redirects_with_error(client, headers, google_configured):
    client.post("/api/v1/auth/bootstrap", json=COMPANY)
    _override_oauth("nobody-registered@acme.test")
    try:
        start = client.get(
            "/api/v1/auth/oauth/google/start", params={"company": "acme"}, follow_redirects=False
        )
        state = start.headers["location"].split("state=")[1]
        r = client.get(
            f"/api/v1/auth/oauth/google/callback?code=fake-code&state={state}",
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        assert r.headers["location"] == f"{get_settings().oauth_frontend_redirect}?error=no_account"
    finally:
        app.dependency_overrides.pop(oauth_client_dependency, None)


def test_oauth_tampered_state_redirects_with_error(client):
    r = client.get(
        "/api/v1/auth/oauth/google/callback?code=fake-code&state=not-a-real-jwt",
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert r.headers["location"] == f"{get_settings().oauth_frontend_redirect}?error=invalid_state"
