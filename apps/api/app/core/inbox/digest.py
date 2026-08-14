"""One message per person, not one per item.

The failure this avoids is the reason people stop reading notifications: an
exception that stays unresolved for a week must not generate seven emails. So
the digest sends only what it has not already mentioned — `notified_at` on each
item is the watermark — and sends nothing at all when there is nothing new.

What it deliberately does NOT do: re-nag. If an item was reported yesterday and
is still open today, the digest stays quiet. Escalation, not repetition, is how
this product raises its voice.
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.inbox import service
from app.core.inbox.models import InboxItem
from app.core.notify.base import Notifier, get_notifier
from app.modules.auth.models import User


async def send_digests(
    db: Session, *, company_id: uuid.UUID, notifier: Notifier | None = None
) -> dict[str, int]:
    """Mail everybody who has something new. Returns what it did, for the task
    log — a sweep that reports nothing is indistinguishable from one that broke.
    """
    notifier = notifier or get_notifier()
    unsent = db.scalars(
        select(InboxItem).where(
            InboxItem.state == "open",
            InboxItem.notified_at.is_(None),
            InboxItem.deleted_at.is_(None),
        )
    ).all()
    if not unsent:
        return {"people": 0, "items": 0}

    by_user: dict[uuid.UUID, list[InboxItem]] = {}
    for row in unsent:
        by_user.setdefault(row.subject_user_id, []).append(row)

    users = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_(by_user))).all()
    }

    now = datetime.now(UTC)
    base = get_settings().oauth_frontend_redirect.rsplit("/oauth", 1)[0]
    people = 0
    for user_id, items in by_user.items():
        user = users.get(user_id)
        if user is None or not user.email:
            continue  # nobody to write to; the item stays open and visible in-app

        ordered = sorted(items, key=lambda r: service.order_key(r, today=now.date()))
        await notifier.send(
            to=user.email,
            template="inbox_digest",
            ctx={
                "name": user.email.split("@")[0],
                "count": len(ordered),
                "plural": "" if len(ordered) == 1 else "s",
                "verb": "is" if len(ordered) == 1 else "are",
                # Title only. The detail may name a site or hours; the summary
                # line in an email is not the place to widen what is disclosed.
                "lines": "\n".join(f"  - {r.title}" for r in ordered),
                "link": f"{base}/home",
            },
        )
        for row in ordered:
            row.notified_at = now
        people += 1

    db.flush()
    return {"people": people, "items": len(unsent)}
