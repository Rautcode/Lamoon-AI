"""Reconciling the inbox.

`sync()` is the whole design. A producer says "here is everything of kind K in
scope S that is still true", and this closes anything it did not mention. That
is what makes the item disappear when somebody fixes the problem directly
rather than by clicking through the inbox — and an inbox that only grows is a
worse Excel sheet than the one it replaces.

The same shape already exists in `build_run`, which soft-deletes payslips for
anyone no longer eligible. Upsert-and-close-the-rest is the pattern; this is
its second use.
"""
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.inbox.models import SEVERITIES, InboxItem

#: blocking sorts before review sorts before info. Index in this tuple IS the
#: rank, so adding a severity means putting it in the right place here.
_RANK = {s: i for i, s in enumerate(SEVERITIES)}

#: How long an open item may sit before it belongs to somebody else too.
ESCALATE_AFTER_DAYS = 3


@dataclass(frozen=True)
class Item:
    """One thing a producer asserts is still true."""

    kind: str
    dedupe_key: str
    title: str
    detail: str | None
    severity: str
    entity: str | None
    entity_id: uuid.UUID | None
    href: str | None
    due_on: date | None


@dataclass
class SyncResult:
    opened: int = 0
    still_open: int = 0
    resolved: int = 0


def order_key(item: Item | InboxItem, *, today: date) -> tuple:
    """What blocks payroll soonest, first.

    Severity dominates: a blocking item due in six weeks still outranks a
    review item due tomorrow, because one stops the run and the other does not.
    Within a severity, the nearer due date wins, and an item with no due date
    sorts last — undated means nobody said when, not that it is urgent.
    """
    severity = _RANK.get(item.severity, len(SEVERITIES))
    if item.due_on is None:
        return (severity, 1, 0)
    return (severity, 0, (item.due_on - today).days)


def sync(
    db: Session,
    *,
    company_id: uuid.UUID,
    kind: str,
    scope_key: str,
    items: dict[uuid.UUID, list[Item]],
) -> SyncResult:
    """Reconcile one producer's whole scope.

    `items` maps the user who must act → what they must act on. Anything open
    in this (kind, scope) that is not in `items` is resolved, because the
    producer has just said it is no longer true.

    Dismissed items are left alone. Somebody looked at that and said "not a
    problem"; re-opening it on the next sweep would be the product arguing.
    """
    now = datetime.now(UTC)
    result = SyncResult()

    existing = {
        (row.subject_user_id, row.dedupe_key): row
        for row in db.scalars(
            select(InboxItem).where(
                InboxItem.kind == kind,
                InboxItem.scope_key == scope_key,
                InboxItem.state.in_(("open", "dismissed")),
                InboxItem.deleted_at.is_(None),
            )
        ).all()
    }

    reported: set[tuple[uuid.UUID, str]] = set()
    for user_id, theirs in items.items():
        for item in theirs:
            key = (user_id, item.dedupe_key)
            reported.add(key)
            row = existing.get(key)
            if row is None:
                db.add(
                    InboxItem(
                        company_id=company_id, subject_user_id=user_id,
                        kind=item.kind, dedupe_key=item.dedupe_key, scope_key=scope_key,
                        title=item.title, detail=item.detail, severity=item.severity,
                        entity=item.entity, entity_id=item.entity_id, href=item.href,
                        due_on=item.due_on, state="open",
                        seen_count=1, first_seen_at=now, last_seen_at=now,
                    )
                )
                result.opened += 1
                continue
            if row.state == "dismissed":
                continue  # a human said no; do not argue
            # Still true. Refresh the wording — a title can improve — and count
            # the sighting, which is what ageing and the digest read.
            row.title, row.detail = item.title, item.detail
            row.severity, row.due_on = item.severity, item.due_on
            row.seen_count += 1
            row.last_seen_at = now
            result.still_open += 1

    for key, row in existing.items():
        if key not in reported and row.state == "open":
            row.state = "resolved"
            row.resolved_at = now  # resolved BY reality, so resolved_by stays null
            result.resolved += 1

    db.flush()
    return result


def open_for(db: Session, user_id: uuid.UUID, *, state: str = "open") -> list[InboxItem]:
    """One person's list, ordered by what blocks payroll soonest."""
    rows = db.scalars(
        select(InboxItem).where(
            InboxItem.subject_user_id == user_id,
            InboxItem.state == state,
            InboxItem.deleted_at.is_(None),
        )
    ).all()
    today = date.today()
    return sorted(rows, key=lambda r: (order_key(r, today=today), r.first_seen_at))


def dismiss(db: Session, item: InboxItem, *, by: uuid.UUID, reason: str | None) -> InboxItem:
    """A human says it is not a problem. Recorded, and not resurrected."""
    item.state = "dismissed"
    item.resolved_at = datetime.now(UTC)
    item.resolved_by = by
    item.dismissed_reason = reason
    db.flush()
    return item


def escalate_due(db: Session, *, now: datetime | None = None) -> list[InboxItem]:
    """Open items old enough that somebody else should also know.

    Marks them; it does not reassign. The person who can close it has not
    changed — what has changed is that they have not, and HR now needs to see
    it before the run rather than at it.
    """
    now = now or datetime.now(UTC)
    rows = db.scalars(
        select(InboxItem).where(
            InboxItem.state == "open",
            InboxItem.escalated_at.is_(None),
            InboxItem.deleted_at.is_(None),
        )
    ).all()
    stale = [
        r for r in rows
        if (now - r.first_seen_at).days >= ESCALATE_AFTER_DAYS
    ]
    for row in stale:
        row.escalated_at = now
    db.flush()
    return stale
