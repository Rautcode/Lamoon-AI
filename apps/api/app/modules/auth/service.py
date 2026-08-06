"""Auth DB operations. Login/OAuth/refresh all resolve tenant scope before
touching `users` (RLS-scoped), so the user lookup never reads across tenants.
"""
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.modules.auth.models import Company, User


def set_tenant(db: Session, company_id: str) -> None:
    """Public: reused by /auth/refresh, which already has company_id from the
    refresh token's claims and doesn't need a subdomain lookup."""
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": company_id})


def _resolve_company(db: Session, subdomain: str) -> Company | None:
    company = db.scalar(
        select(Company).where(Company.subdomain == subdomain, Company.deleted_at.is_(None))
    )
    if company is not None:
        set_tenant(db, str(company.id))
    return company


def authenticate(db: Session, subdomain: str, email: str, password: str) -> User | None:
    company = _resolve_company(db, subdomain)
    if company is None:
        return None
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None or not user.password_hash or not verify_password(password, user.password_hash):
        return None
    return user


def authenticate_oauth(db: Session, subdomain: str, email: str, provider: str) -> User | None:
    """OAuth never creates accounts (no open self-signup into an arbitrary
    company) — the email must match an existing active user in that company.
    ponytail: domain-verified auto-provisioning is a real, separate feature
    (needs a verified-domain or invite-token story), deliberately not built here.
    """
    company = _resolve_company(db, subdomain)
    if company is None:
        return None
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None:
        return None
    if user.oauth_provider is None:
        user.oauth_provider = provider  # record how they first authenticated
    return user


def create_company_with_admin(
    db: Session, *, company_name: str, subdomain: str, email: str, password: str
) -> tuple[Company, User]:
    """Idempotent bootstrap: get-or-create the company and its admin."""
    company = db.scalar(select(Company).where(Company.subdomain == subdomain))
    if company is None:
        company = Company(name=company_name, subdomain=subdomain, plan="starter", seat_limit=25)
        db.add(company)
        db.flush()
    set_tenant(db, str(company.id))
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            company_id=company.id,
            email=email,
            full_name="Admin",
            role="admin",
            password_hash=hash_password(password),
            is_active=True,
        )
        db.add(user)
        db.flush()
    else:
        user.password_hash = hash_password(password)
    return company, user
