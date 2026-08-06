"""Auth seam (ADR-0008). Business logic sees only an authenticated Principal,
never a password, token lib, or IdP. Swapping to Keycloak/SAML at V4 = a new
implementation registered in one place, zero module changes.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Principal:
    user_id: str
    company_id: str
    permissions: frozenset[str]


@dataclass
class TokenPair:
    access: str
    refresh: str


class IdentityProvider(Protocol):
    async def authenticate(self, credentials: dict) -> Principal: ...
    async def issue_session(self, principal: Principal) -> TokenPair: ...
    async def verify_session(self, token: str) -> Principal: ...


class LocalIdentityProvider:
    """V1: password + Google/Microsoft OAuth against our own `users` table."""

    async def authenticate(self, credentials: dict) -> Principal:
        raise NotImplementedError  # ponytail: implement in the auth module (V1 ATS phase)

    async def issue_session(self, principal: Principal) -> TokenPair:
        raise NotImplementedError

    async def verify_session(self, token: str) -> Principal:
        raise NotImplementedError


def get_identity_provider() -> IdentityProvider:
    return LocalIdentityProvider()  # the one place the impl is chosen
