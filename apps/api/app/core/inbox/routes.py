"""Inbox endpoints.

There is no permission on reading your own inbox beyond being logged in — it
contains only what somebody has already decided you may act on, and the
producers are responsible for that. What IS enforced: an item belongs to one
person, and nobody else can see or act on it, including HR.
"""
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.inbox import service
from app.core.inbox.models import InboxItem
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant

router = APIRouter(prefix="/inbox", tags=["inbox"])


class ItemOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    detail: str | None
    severity: str
    entity: str | None
    entity_id: uuid.UUID | None
    href: str | None
    due_on: date | None
    state: str
    seen_count: int
    age_days: int
    first_seen_at: datetime
    resolved_at: datetime | None
    escalated_at: datetime | None


class DismissIn(BaseModel):
    reason: str | None = None


def _out(row: InboxItem) -> ItemOut:
    return ItemOut(
        id=row.id, kind=row.kind, title=row.title, detail=row.detail,
        severity=row.severity, entity=row.entity, entity_id=row.entity_id,
        href=row.href, due_on=row.due_on, state=row.state,
        seen_count=row.seen_count,
        age_days=(datetime.now(UTC) - row.first_seen_at).days,
        first_seen_at=row.first_seen_at, resolved_at=row.resolved_at,
        escalated_at=row.escalated_at,
    )


def _mine_or_404(db: Session, item_id: uuid.UUID, principal: Principal) -> InboxItem:
    """Somebody else's item does not 403 — it 404s.

    Telling a person that an item exists but is not theirs leaks who is being
    asked to do what, which in an HR product is itself information.
    """
    row = db.get(InboxItem, item_id)
    if (
        row is None
        or row.deleted_at is not None
        or str(row.subject_user_id) != principal.user_id
    ):
        raise HTTPException(404, "not found")
    return row


@router.get("", response_model=list[ItemOut])
def my_inbox(
    state: str = "open",
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    _cid: str = Depends(resolve_tenant),
):
    """What needs ME, soonest-blocking first.

    Takes no user id by design — the same rule ESS follows. An inbox you can
    address by somebody else's id is not an inbox.
    """
    return [
        _out(r)
        for r in service.open_for(db, uuid.UUID(principal.user_id), state=state)
    ]


@router.post("/{item_id}/dismiss", response_model=ItemOut)
def dismiss(
    item_id: uuid.UUID,
    body: DismissIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    _cid: str = Depends(resolve_tenant),
):
    """"Not a problem." Recorded with a reason, and not resurrected by the next
    sync — the product should not argue with a human who has looked."""
    row = _mine_or_404(db, item_id, principal)
    return _out(
        service.dismiss(db, row, by=uuid.UUID(principal.user_id), reason=body.reason)
    )


@router.post("/sync", dependencies=[Depends(require("workfact.read"))])
def run_sync(db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    """Re-derive every producer's items for this company.

    Exposed as an endpoint so it is testable and so HR can force a refresh;
    the scheduled sweep calls the same function. Idempotent by construction —
    that is what `sync` is for.
    """
    from app.modules.payroll import inbox_sync as payroll_inbox

    result = payroll_inbox.sync_pending_work_facts(db, company_id=uuid.UUID(cid))
    return {
        "opened": result.opened,
        "still_open": result.still_open,
        "resolved": result.resolved,
    }
