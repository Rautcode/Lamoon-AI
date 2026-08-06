"""Small notification helpers shared by the pipeline and intake routes."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.auth.models import User


def hr_recipient(db: Session, company_id: uuid.UUID) -> str | None:
    """First admin/hr user for the company — the "HR email" the spec routes
    Tier A/B notices and failure alerts to. ponytail: a dedicated hr_email on
    Company is a cleaner home for this once multiple HR users are common."""
    user = db.scalar(
        select(User)
        .where(
            User.company_id == company_id,
            User.role.in_(("admin", "hr")),
            User.is_active.is_(True),
        )
        .order_by(User.created_at)
    )
    return user.email if user else None
