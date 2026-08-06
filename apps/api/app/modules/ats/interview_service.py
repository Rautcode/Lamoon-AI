"""Interview scheduling — self-service booking. HR proposes slots; the
candidate books via a magic link (no login). Real Calendar-API push is
deferred (see Interview.calendar_event_id) — this is the working V1 scheduler.
"""
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.notify.base import Notifier
from app.modules.ats.models import (
    Application,
    Candidate,
    Interview,
    InterviewBookingLink,
    InterviewSlot,
    JobOpening,
)
from app.modules.ats.notify_helpers import hr_recipient
from app.modules.ats.schemas import ProposeSlotsIn
from app.modules.audit import service as audit
from app.modules.auth.models import Company

BOOKING_LINK_TTL_DAYS = 14


async def propose_slots(
    db: Session, application: Application, body: ProposeSlotsIn, notifier: Notifier
) -> tuple[InterviewBookingLink, list[InterviewSlot]]:
    """HR offers time slots for an application; the candidate gets a magic
    link. Reuses an existing unexpired link for the same application rather
    than minting a new one each time (stable link across re-proposals)."""
    slots = [
        InterviewSlot(
            company_id=application.company_id, application_id=application.id,
            starts_at=s.starts_at, ends_at=s.ends_at, status="open",
        )
        for s in body.slots
    ]
    db.add_all(slots)

    link = db.scalar(
        select(InterviewBookingLink).where(
            InterviewBookingLink.application_id == application.id,
            InterviewBookingLink.expires_at > datetime.now(UTC),
        )
    )
    if link is None:
        link = InterviewBookingLink(
            token=secrets.token_urlsafe(24),
            company_id=application.company_id,
            application_id=application.id,
            expires_at=datetime.now(UTC) + timedelta(days=BOOKING_LINK_TTL_DAYS),
        )
        db.add(link)
    db.flush()

    application.status = "interview_proposed"

    cand = db.get(Candidate, application.candidate_id)
    job = db.get(JobOpening, application.job_opening_id) if application.job_opening_id else None
    company = db.get(Company, application.company_id)
    if cand and cand.email:
        booking_url = f"{get_settings().api_base_url}/api/v1/public/interviews/{link.token}"
        await notifier.send(
            to=cand.email, template="interview_invite",
            ctx={
                "candidate_name": cand.full_name or "there",
                "job_title": job.title if job else "the role",
                "company_name": company.name if company else "the company",
                "scheduling_link": booking_url,
            },
        )
    audit.record(
        db, company_id=application.company_id, entity="application", entity_id=application.id,
        action="interview_slots_proposed", payload={"count": len(slots)},
    )
    return link, slots


class BookingError(Exception):
    """Public-facing booking failure — routes map this to an HTTP status."""


def resolve_booking_link(db: Session, token: str) -> InterviewBookingLink:
    link = db.scalar(select(InterviewBookingLink).where(InterviewBookingLink.token == token))
    if link is None:
        raise BookingError("not_found")
    if link.expires_at <= datetime.now(UTC):
        raise BookingError("expired")
    return link


async def book_slot(
    db: Session, link: InterviewBookingLink, slot_id: uuid.UUID, notifier: Notifier
) -> Interview:
    # Atomic conditional UPDATE is the actual concurrency guard: under Postgres
    # READ COMMITTED, concurrent bookings of the same slot serialize on the row
    # lock and only one UPDATE affects a row — no separate SELECT-then-check race.
    # cast: Session.execute()'s generic overload loses the CursorResult type
    # once .values() is chained; it IS one at runtime for a Core UPDATE.
    result = cast(
        CursorResult,
        db.execute(
            update(InterviewSlot)
            .where(
                InterviewSlot.id == slot_id,
                InterviewSlot.application_id == link.application_id,
                InterviewSlot.status == "open",
            )
            .values(status="booked")
        ),
    )
    if result.rowcount == 0:
        raise BookingError("slot_unavailable")

    slot = db.get(InterviewSlot, slot_id)
    assert slot is not None  # guaranteed by the UPDATE above matching a row

    # Free the other slots offered for this application — one booking per candidate.
    db.execute(
        update(InterviewSlot)
        .where(
            InterviewSlot.application_id == link.application_id,
            InterviewSlot.status == "open",
        )
        .values(status="cancelled")
    )

    application = db.get(Application, link.application_id)
    assert application is not None
    application.status = "interview_scheduled"

    interview = Interview(
        company_id=link.company_id, application_id=link.application_id, slot_id=slot.id,
        scheduled_at=slot.starts_at, status="booked",
    )
    db.add(interview)
    db.flush()

    cand = db.get(Candidate, application.candidate_id)
    job = db.get(JobOpening, application.job_opening_id) if application.job_opening_id else None
    company = db.get(Company, link.company_id)
    company_name = company.name if company else "the company"
    job_title = job.title if job else "the role"
    when = slot.starts_at.isoformat()

    if cand and cand.email:
        await notifier.send(
            to=cand.email, template="interview_confirmed",
            ctx={
                "candidate_name": cand.full_name or "there",
                "job_title": job_title, "company_name": company_name, "scheduled_at": when,
            },
        )
    hr = hr_recipient(db, link.company_id)
    if hr:
        await notifier.send(
            to=hr, template="hr_alert",
            ctx={
                "subject": f"Interview booked — {job_title}: {cand.full_name if cand else ''}",
                "body": f"Application {application.id} booked for {when}.",
            },
        )
    audit.record(
        db, company_id=link.company_id, entity="application", entity_id=application.id,
        action="interview_booked",
        payload={"interview_id": str(interview.id), "scheduled_at": when},
    )
    return interview
