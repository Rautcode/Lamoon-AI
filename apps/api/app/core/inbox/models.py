"""The task inbox — what needs one particular person.

**Owns:** the durable claim that somebody must act, and whether they still must.
**Consumes:** nothing. Producers push into it; it reads no domain data itself.
**Produces:** open items per user, and the digest a notification is built from.
**Depended on by:** every module with an exception a human must close.
**Correction behaviour:** items are RECONCILED, not accumulated. A producer
re-syncs its whole scope and anything no longer true is closed — so fixing the
underlying problem by any route, not only by clicking through the inbox, makes
the item disappear.

This is not a notification and not an event log. A notification is a message
that has already happened; an inbox item is a claim that is still true. The
difference shows up in three behaviours the naive version gets wrong:

  deduplicated  re-deriving the same exception nightly must not create thirty
                rows and send thirty emails
  reconciled    an item must close when reality changes, however it changed
  scoped        it belongs to ONE person — the one who can close it
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import TenantBase

#: open → resolved (reality changed) | dismissed (a human said "not a problem").
#: Dismissed is kept distinct because it must NOT reappear on the next sync,
#: whereas resolved may legitimately recur if the problem comes back.
STATES = ("open", "resolved", "dismissed")

#: blocking stops payroll; review wants a human's eye; info is context.
#: Ordering is by severity FIRST — see service.order_key.
SEVERITIES = ("blocking", "review", "info")


class InboxItem(TenantBase):
    __tablename__ = "inbox_items"
    __table_args__ = (
        # One live item per (person, kind, thing). This is what makes a nightly
        # re-derivation idempotent instead of a mailbox flood. Partial, so a
        # resolved item does not block the same problem recurring later.
        Index(
            "uq_inbox_open", "company_id", "subject_user_id", "kind", "dedupe_key",
            unique=True, postgresql_where=text("state = 'open'"),
        ),
        Index("ix_inbox_mine", "company_id", "subject_user_id", "state"),
        Index("ix_inbox_scope", "company_id", "kind", "scope_key", "state"),
    )

    #: WHO must act. A user, not an employee — the inbox belongs to somebody
    #: who logs in, and not every employee does.
    subject_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), index=True
    )

    #: What sort of thing: "workfact.pending", "attendance.unexplained",
    #: "payroll.blocking". Namespaced by producer so a sync can claim its own
    #: rows without touching anybody else's.
    kind: Mapped[str] = mapped_column(String(40))

    #: Stable identity of the underlying thing, chosen by the producer — a
    #: work-fact id, an employee+period. Two syncs of the same problem must
    #: produce the same key or deduplication does nothing.
    dedupe_key: Mapped[str] = mapped_column(String(200))

    #: What the producer re-synced. Closing "everything of this kind in this
    #: scope that I did not just report" is how reconciliation works, and the
    #: scope keeps one company's sweep from closing another's items.
    scope_key: Mapped[str] = mapped_column(String(120), default="")

    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(10), default="review")

    #: What it is about, and where to fix it. `href` is a product route, never
    #: a raw id — the inbox links to a place, not to a database row.
    entity: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    href: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: When it starts to hurt — usually the payroll run it would block.
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    state: Mapped[str] = mapped_column(String(10), default="open")
    #: How many syncs have seen it. Ageing and escalation read this and
    #: `first_seen_at`; a digest reads `last_seen_at` to find what is new.
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    dismissed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Set when it has sat unresolved long enough to belong to somebody else
    #: too. Escalation ADDS a watcher; it never moves the item, because the
    #: person who can close it has not changed.
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: When the digest last told this person about it, so a daily mail does not
    #: repeat what it already said.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
