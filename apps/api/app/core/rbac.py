"""RBAC — the entire enforcement surface (ADR-0008). The Principal comes from
the JWT (permissions derived from role); routes declare what they need.
"""
from fastapi import Depends, HTTPException, Request

from app.core.auth.provider import Principal, bearer_token, get_identity_provider


def current_user(request: Request) -> Principal:
    token = bearer_token(request)
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        return get_identity_provider().verify_session(token)
    except Exception:
        raise HTTPException(401, "invalid or expired token") from None


def require(permission: str):
    def dep(user: Principal = Depends(current_user)) -> Principal:
        if "*" not in user.permissions and permission not in user.permissions:
            raise HTTPException(403, f"missing permission: {permission}")
        return user

    return dep
