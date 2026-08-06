"""Auth DB operations. Login resolves the company first (companies is not RLS-
scoped), sets the tenant GUC, then looks the user up *within* that tenant — so
the user lookup never reads across tenants and RLS stays honest.
"""
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.modules.auth.models import Company, User


def _set_tenant(db: Session, company_id: str) -> None:
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": company_id})


def authenticate(db: Session, subdomain: str, email: str, password: str) -> User | None:
    company = db.scalar(
        select(Company).where(Company.subdomain == subdomain, Company.deleted_at.is_(None))
    )
    if company is None:
        return None
    _set_tenant(db, str(company.id))
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None or not user.password_hash or not verify_password(password, user.password_hash):
        return None
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
    _set_tenant(db, str(company.id))
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
