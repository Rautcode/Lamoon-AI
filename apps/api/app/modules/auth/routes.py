"""Auth endpoints. Login/bootstrap/refresh/OAuth all run before a tenant is
established via the JWT, so they use open_session (no pre-set GUC) and manage
tenant scope themselves (service.py / oauth.py).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.core.auth.oauth import OAuthClient, get_oauth_client, is_configured, new_state
from app.core.auth.permissions import permissions_for
from app.core.auth.provider import Principal, bearer_token, get_identity_provider
from app.core.auth.revocation import is_revoked, revoke, ttl_from_exp
from app.core.config import get_settings
from app.core.db import open_session
from app.core.rbac import current_user
from app.core.security import decode_token
from app.modules.auth import service
from app.modules.auth.models import User
from app.modules.auth.schemas import BootstrapIn, LoginIn, MeOut, RefreshIn, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(user: User) -> TokenOut:
    principal = Principal(
        user_id=str(user.id), company_id=str(user.company_id),
        role=user.role, permissions=permissions_for(user.role),
    )
    tokens = get_identity_provider().issue_session(principal)
    return TokenOut(access_token=tokens.access, refresh_token=tokens.refresh)


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
        return {
            "company_id": str(company.id), "subdomain": company.subdomain,
            "admin": user.email, "seat_limit": company.seat_limit,
        }


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn) -> TokenOut:
    with open_session() as db:
        user = service.authenticate(db, body.company, body.email, body.password)
        if user is None:
            raise HTTPException(401, "invalid credentials")
        return _issue(user)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn) -> TokenOut:
    """Re-issues a fresh access+refresh pair from a valid refresh token. Role
    and active-status are re-read from the DB (not trusted from stale claims),
    so a demoted/deactivated user is denied immediately rather than at their
    old token's expiry.

    Rotation: the presented refresh token is revoked once it's been used —
    each refresh token is single-use. Replaying an already-rotated (or
    logged-out) refresh token is rejected here.

    ponytail: no reuse-DETECTION beyond a flat deny — a real IdP would treat
    replay of an already-rotated token as a compromise signal and revoke the
    whole token family, not just deny the one request. Flat deny is the
    correct minimum; family-wide revocation is a further hardening step.
    """
    try:
        claims = decode_token(body.refresh_token)
    except Exception:
        raise HTTPException(401, "invalid or expired refresh token") from None
    if claims.get("typ") != "refresh":
        raise HTTPException(401, "not a refresh token")
    if is_revoked(claims.get("jti")):
        raise HTTPException(401, "refresh token revoked")

    with open_session() as db:
        service.set_tenant(db, claims["cid"])
        user = db.get(User, uuid.UUID(claims["sub"]))
        if user is None or not user.is_active or user.deleted_at is not None:
            raise HTTPException(401, "account no longer active")
        tokens = _issue(user)

    revoke(claims["jti"], ttl_from_exp(int(claims["exp"])))
    return tokens


@router.post("/logout", status_code=204, dependencies=[Depends(current_user)])
def logout(body: RefreshIn, request: Request) -> None:
    """Revokes both the access token used to call this endpoint and the
    presented refresh token, so sign-out is immediate server-side — not just
    the client discarding tokens it might not have actually deleted.
    `dependencies=[current_user]` means a garbage/expired access token 401s
    before we even get here, same as any other authenticated route."""
    for token in (bearer_token(request), body.refresh_token):
        if not token:
            continue
        try:
            claims = decode_token(token)
        except Exception:
            continue  # already invalid/expired — nothing to revoke
        jti = claims.get("jti")
        if jti:
            revoke(jti, ttl_from_exp(int(claims["exp"])))


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(current_user)) -> MeOut:
    return MeOut(
        user_id=principal.user_id, company_id=principal.company_id,
        role=principal.role, permissions=sorted(principal.permissions),
    )


def _redirect_uri(provider: str) -> str:
    # Must be byte-identical between /start and /callback — both build it here.
    return f"{get_settings().api_base_url}/api/v1/auth/oauth/{provider}/callback"


@router.get("/oauth/providers")
def oauth_providers() -> dict[str, bool]:
    """Lets the UI show/enable only what's actually configured, instead of
    leading a click into a raw 503 — there are no real Google/Microsoft app
    credentials in most environments running this (including this one)."""
    return {"google": is_configured("google"), "microsoft": is_configured("microsoft")}


@router.get("/oauth/{provider}/start")
def oauth_start(provider: str, company: str = Query(..., description="company subdomain")):
    if provider not in ("google", "microsoft"):
        raise HTTPException(404, "unknown provider")
    if not is_configured(provider):
        raise HTTPException(503, f"{provider} OAuth is not configured")
    client = get_oauth_client(provider)
    state = new_state(provider, company)
    return RedirectResponse(client.authorize_url(_redirect_uri(provider), state))


def oauth_client_dependency(provider: str) -> OAuthClient:
    """A parameterized dependency: FastAPI resolves `provider` from the route's
    own path param. Tests override THIS (app.dependency_overrides) to inject a
    fake client — /start doesn't need the override since authorize_url() is
    pure string-building; only /callback's exchange() touches the network."""
    if provider not in ("google", "microsoft"):
        raise HTTPException(404, "unknown provider")
    return get_oauth_client(provider)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str, code: str, state: str,
    client: OAuthClient = Depends(oauth_client_dependency),
) -> RedirectResponse:
    """Google/Microsoft land the browser HERE — there is no API client on the
    other end to hand a JSON body to, only whatever's rendered next. Every
    outcome (success or otherwise) redirects to the frontend; nothing here
    should ever show the browser a raw JSON error page.

    Success: tokens go in the URL fragment (`#...`), not query params — the
    fragment never leaves the browser (no server/proxy/access-log sees it).
    Failure: `?error=<code>` for the frontend to render a message from.
    """
    frontend = get_settings().oauth_frontend_redirect

    try:
        state_claims = decode_token(state)
        if state_claims.get("typ") != "oauth_state" or state_claims.get("provider") != provider:
            raise ValueError("state mismatch")
    except Exception:
        return RedirectResponse(f"{frontend}?error=invalid_state")

    try:
        profile = await client.exchange(code, _redirect_uri(provider))
    except Exception:
        return RedirectResponse(f"{frontend}?error=exchange_failed")

    with open_session() as db:
        user = service.authenticate_oauth(db, state_claims["company"], profile.email, provider)
        if user is None:
            return RedirectResponse(f"{frontend}?error=no_account")
        tokens = _issue(user)

    return RedirectResponse(
        f"{frontend}#access_token={tokens.access_token}&refresh_token={tokens.refresh_token}"
    )
