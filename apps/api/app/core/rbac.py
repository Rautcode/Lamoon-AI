"""RBAC — the entire enforcement surface (ADR-0008). Permissions load onto the
Principal at login; routes declare what they need.
"""
from fastapi import Depends, HTTPException

from app.core.auth.provider import Principal


def current_user() -> Principal:
    # ponytail: wired to IdentityProvider.verify_session in the auth module (V1).
    raise NotImplementedError


def require(permission: str):
    def dep(user: Principal = Depends(current_user)) -> Principal:
        if permission not in user.permissions:
            raise HTTPException(403, f"missing permission: {permission}")
        return user

    return dep
