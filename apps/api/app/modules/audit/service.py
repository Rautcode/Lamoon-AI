"""audit.record() — one call to log a domain action with the current context."""
import uuid

from sqlalchemy.orm import Session

from app.core import context
from app.modules.audit.models import AuditEvent


def record(
    db: Session,
    *,
    company_id: uuid.UUID,
    entity: str,
    entity_id: uuid.UUID | None,
    action: str,
    payload: dict | None = None,
    source: str | None = None,
) -> None:
    # company_id is passed explicitly: sync deps run in a threadpool where the
    # tenant contextvar doesn't propagate. correlation/user come from the async
    # middleware, which does propagate.
    ctx = context.current()
    db.add(
        AuditEvent(
            company_id=company_id,
            entity=entity,
            entity_id=entity_id,
            action=action,
            actor_user_id=uuid.UUID(ctx.user_id) if ctx.user_id else None,
            correlation_id=ctx.correlation_id,
            source=source,
            payload=payload or {},
        )
    )
