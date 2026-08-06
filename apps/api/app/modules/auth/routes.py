"""Auth endpoints. Login/bootstrap run before a tenant exists, so they use
open_session (no pre-set GUC) and manage the tenant scope in the service.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.core.auth.permissions import permissions_for
from app.core.auth.provider import Principal, get_identity_provider
from app.core.config import get_settings
from app.core.db import open_session
from app.core.rbac import current_user
from app.modules.auth import service
from app.modules.auth.schemas import BootstrapIn, LoginIn, MeOut, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/bootstrap", status_code=201)
def bootstrap(body: BootstrapIn) -> dict:
    """Dev-only: create a company + admin so there's something to log in as.
    ponytail: real self-serve signup (with email verify + billing) is a later flow."""
    if get_settings().environment != "dev":
        raise HTTPException(403, "bootstrap disabled outside dev")
    with open_session() as db:
        company, user = service.create_company_with_admin(
            db, company_name=body.company_name, subdomain=body.subdomain,
            email=body.email, password=body.password,
        )
        return {"company_id": str(company.id), "subdomain": company.subdomain, "admin": user.email}


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn) -> TokenOut:
    with open_session() as db:
        user = service.authenticate(db, body.company, body.email, body.password)
        if user is None:
            raise HTTPException(401, "invalid credentials")
        principal = Principal(
            user_id=str(user.id), company_id=str(user.company_id),
            role=user.role, permissions=permissions_for(user.role),
        )
    tokens = get_identity_provider().issue_session(principal)
    return TokenOut(access_token=tokens.access, refresh_token=tokens.refresh)


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(current_user)) -> MeOut:
    return MeOut(
        user_id=principal.user_id, company_id=principal.company_id,
        role=principal.role, permissions=sorted(principal.permissions),
    )
