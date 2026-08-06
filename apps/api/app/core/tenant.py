"""Tenant resolution + module-entitlement guard (ARCH §1, platform §7).

resolve_tenant is the single entry: in prod the tenant is set on context by auth
middleware (from the JWT); until auth ships, dev accepts an X-Company-Id header.
It sets context.tenant_id so get_db can apply the RLS GUC.
"""
from fastapi import Depends, HTTPException, Request

from app.core import context
from app.core.billing import entitlements
from app.core.config import get_settings


def resolve_tenant(request: Request) -> str:
    cid = context.tenant_id.get()
    if not cid and get_settings().environment == "dev":
        # ponytail: dev-only shim. Real tenant comes from the JWT via auth middleware.
        cid = request.headers.get("X-Company-Id")
    if not cid:
        raise HTTPException(401, "no tenant")
    context.tenant_id.set(cid)
    return cid


def require_module(module_key: str):
    """Reject requests to a module the company hasn't enabled/paid for → 402."""

    def dep(cid: str = Depends(resolve_tenant)) -> None:
        if not entitlements.can_use(cid, f"module.{module_key}").allowed:
            raise HTTPException(402, f"module '{module_key}' not enabled")

    return dep
