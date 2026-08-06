"""Unauthenticated, candidate-facing endpoints — a booking token IS the auth
(a magic link), so these never go through get_db/resolve_tenant (which require
a JWT). Each request resolves its own tenant scope from the token and opens
its own session, mirroring the auth bootstrap/login pattern.

ponytail: no rate limiting on these yet — a real gap for a public endpoint,
worth adding (e.g. per-IP/per-token throttling) before this handles real
internet traffic; deferred to keep this slice focused on the booking flow.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import open_session
from app.core.notify.base import Notifier, get_notifier
from app.modules.ats import interview_service as svc
from app.modules.ats.models import Application, InterviewBookingLink, InterviewSlot, JobOpening
from app.modules.ats.schemas import BookIn, BookingSlotsOut, BookOut
from app.modules.auth.models import Company

router = APIRouter(prefix="/public/interviews", tags=["public"])


def _set_tenant(db: Session, company_id: uuid.UUID) -> None:
    db.execute(text("SELECT set_config('app.company_id', :cid, true)"), {"cid": str(company_id)})


def _resolve_or_404(db: Session, token: str) -> InterviewBookingLink:
    try:
        return svc.resolve_booking_link(db, token)
    except svc.BookingError as e:
        status = 404 if str(e) == "not_found" else 410  # 410 Gone: link expired
        raise HTTPException(status, str(e)) from None


@router.get("/{token}", response_model=BookingSlotsOut)
def list_open_slots(token: str):
    with open_session() as db:
        link = _resolve_or_404(db, token)
        _set_tenant(db, link.company_id)

        application = db.get(Application, link.application_id)
        if application is None:
            raise HTTPException(404, "not_found")
        job = (
            db.get(JobOpening, application.job_opening_id)
            if application.job_opening_id
            else None
        )
        company = db.get(Company, link.company_id)
        slots = db.scalars(
            select(InterviewSlot).where(
                InterviewSlot.application_id == link.application_id,
                InterviewSlot.status == "open",
            )
        ).all()
        return BookingSlotsOut(
            job_title=job.title if job else "the role",
            company_name=company.name if company else "the company",
            slots=list(slots),
        )


@router.post("/{token}/book", response_model=BookOut)
async def book_interview(
    token: str, body: BookIn, notifier: Notifier = Depends(get_notifier)
):
    with open_session() as db:
        link = _resolve_or_404(db, token)
        _set_tenant(db, link.company_id)
        try:
            interview = await svc.book_slot(db, link, body.slot_id, notifier)
        except svc.BookingError:
            raise HTTPException(409, "slot no longer available") from None
        return BookOut(
            interview_id=interview.id, scheduled_at=interview.scheduled_at, status=interview.status
        )
