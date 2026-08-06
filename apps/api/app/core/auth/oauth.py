"""OAuth2 authorization-code login (Google/Microsoft). Real, provider-agnostic
client behind a seam — same reasoning as AIProvider: the network call to
Google/Microsoft can't be exercised without real registered-app credentials,
but everything around it (state/CSRF, callback handling, user lookup, token
issuance) can be and is tested, against a fake client (see tests/test_oauth.py).
"""
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.security import encode_token


@dataclass
class OAuthProfile:
    email: str
    name: str | None = None


class OAuthClient(Protocol):
    def authorize_url(self, redirect_uri: str, state: str) -> str: ...
    async def exchange(self, code: str, redirect_uri: str) -> OAuthProfile: ...


@dataclass(frozen=True)
class _Endpoints:
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str


# Public, stable, well-documented endpoints — not secrets. Only client_id/
# secret (in Settings) are. {tenant} is filled in for Microsoft only.
PROVIDERS: dict[str, _Endpoints] = {
    "google": _Endpoints(
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
    ),
    "microsoft": _Endpoints(
        authorize_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/oidc/userinfo",
        scope="openid email profile",
    ),
}


def is_configured(provider: str) -> bool:
    s = get_settings()
    if provider == "google":
        return bool(s.google_client_id)
    if provider == "microsoft":
        return bool(s.microsoft_client_id)
    return False


class GenericOAuthClient:
    """One implementation drives both providers — they're both plain OAuth2
    authorization-code + OIDC userinfo, just different URLs/credentials."""

    def __init__(self, provider: str) -> None:
        s = get_settings()
        ep = PROVIDERS[provider]
        self._authorize_endpoint = ep.authorize_url.format(tenant=s.microsoft_tenant)
        self._token_endpoint = ep.token_url.format(tenant=s.microsoft_tenant)
        self._userinfo_endpoint = ep.userinfo_url
        self._scope = ep.scope
        if provider == "google":
            self._client_id, self._client_secret = s.google_client_id, s.google_client_secret
        else:
            self._client_id, self._client_secret = s.microsoft_client_id, s.microsoft_client_secret

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self._client_id, "redirect_uri": redirect_uri,
            "response_type": "code", "scope": self._scope, "state": state,
        }
        return f"{self._authorize_endpoint}?{urlencode(params)}"

    async def exchange(self, code: str, redirect_uri: str) -> OAuthProfile:
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                self._token_endpoint,
                data={
                    "client_id": self._client_id, "client_secret": self._client_secret,
                    "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            info_resp = await client.get(
                self._userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"}
            )
            info_resp.raise_for_status()
            data = info_resp.json()
            return OAuthProfile(email=data["email"], name=data.get("name"))


def get_oauth_client(provider: str) -> OAuthClient:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown OAuth provider: {provider}")
    return GenericOAuthClient(provider)


def new_state(provider: str, company_subdomain: str) -> str:
    """Signed, short-lived state = CSRF protection + carries which company the
    login is for (OAuth callbacks only give us an email, not a tenant)."""
    claims = {
        "typ": "oauth_state", "provider": provider,
        "company": company_subdomain, "nonce": uuid.uuid4().hex,
    }
    return encode_token(claims, timedelta(minutes=get_settings().oauth_state_ttl_min))
