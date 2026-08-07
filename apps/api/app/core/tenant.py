"""Tenant resolution (ARCH §1). Just "who is this request for" — entitlement/
billing concerns (module gates, seat limits) live in core/billing/entitlements.py,
which depends on this module + core/db.py. Keeping that dependency one-directional
(entitlements -> tenant, not the reverse) avoids a tenant<->db<->entitlements cycle.
"""
from fastapi import HTTPException, Request

from app.core import context
from app.core.auth.provider import bearer_token, get_identity_provider


def resolve_tenant(request: Request) -> str:
    token = bearer_token(request)
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        principal = get_identity_provider().verify_session(token)
    except Exception:
        raise HTTPException(401, "invalid or expired token") from None
    context.tenant_id.set(principal.company_id)
    context.user_id.set(principal.user_id)
    return principal.company_id
