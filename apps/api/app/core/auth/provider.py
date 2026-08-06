"""Auth seam (ADR-0008). Business logic sees only a Principal — never a token
lib or IdP. Swapping to Keycloak/SAML = a new IdentityProvider registered in
get_identity_provider(), zero module changes.

Credential verification (password/OAuth) is provider-specific and lives in the
auth module's service; the seam here is session issue/verify (JWT for V1).
"""
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from fastapi import Request

from app.core.auth.permissions import permissions_for
from app.core.config import get_settings
from app.core.security import decode_token, encode_token


@dataclass
class Principal:
    user_id: str
    company_id: str
    role: str
    permissions: frozenset[str]


@dataclass
class TokenPair:
    access: str
    refresh: str


def bearer_token(request: Request) -> str | None:
    h = request.headers.get("Authorization", "")
    return h[7:] if h[:7].lower() == "bearer " else None


class IdentityProvider(Protocol):
    def issue_session(self, principal: Principal) -> TokenPair: ...
    def verify_session(self, token: str) -> Principal: ...


class LocalIdentityProvider:
    """V1: HS256 JWTs carrying user/company/role. Verify is local + sync (hot path)."""

    def issue_session(self, principal: Principal) -> TokenPair:
        s = get_settings()
        claims = {"sub": principal.user_id, "cid": principal.company_id, "role": principal.role}
        access = encode_token({**claims, "typ": "access"}, timedelta(minutes=s.access_ttl_min))
        refresh = encode_token({**claims, "typ": "refresh"}, timedelta(days=s.refresh_ttl_days))
        return TokenPair(access, refresh)

    def verify_session(self, token: str) -> Principal:
        data = decode_token(token)  # raises on invalid/expired → caller maps to 401
        role = str(data.get("role", ""))
        return Principal(
            user_id=str(data["sub"]),
            company_id=str(data["cid"]),
            role=role,
            permissions=permissions_for(role),
        )


def get_identity_provider() -> IdentityProvider:
    return LocalIdentityProvider()  # the one place the impl is chosen
