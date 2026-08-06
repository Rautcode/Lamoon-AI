"""Tenant resolution + module-entitlement guard (ARCH §1, platform §7).

Tenant now comes from the JWT (real auth) — the dev X-Company-Id shim is gone.
resolve_tenant verifies the bearer token, sets context (for logging/audit), and
returns company_id so get_db can apply the RLS GUC.
"""
from fastapi import Depends, HTTPException, Request

from app.core import context
from app.core.auth.provider import bearer_token, get_identity_provider
from app.core.billing import entitlements


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


def require_module(module_key: str):
    """Reject requests to a module the company hasn't enabled/paid for → 402."""

    def dep(cid: str = Depends(resolve_tenant)) -> None:
        if not entitlements.can_use(cid, f"module.{module_key}").allowed:
            raise HTTPException(402, f"module '{module_key}' not enabled")

    return dep
