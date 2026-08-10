"""Lumo endpoint. Tenant-scoped like everything else — the tools query through
the same RLS-guarded session, so Lumo physically cannot read another company's
data even if the model asks for it."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.ai.provider import AIProvider, get_ai_provider
from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user
from app.modules.assistant import service

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AskIn(BaseModel):
    question: str


class AskItem(BaseModel):
    title: str
    meta: str | None = None
    href: str | None = None


class AskOut(BaseModel):
    text: str
    items: list[AskItem] = []
    unmatched: bool = False
    #: Lets the UI be honest about whether a model or the keyword fallback
    #: produced this answer.
    model_used: bool = False


@router.post("/ask", response_model=AskOut)
async def ask(
    body: AskIn,
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
    principal: Principal = Depends(current_user),
) -> AskOut:
    # Lumo reads exactly what this caller could read through the REST API —
    # it must not be a way around route-level permissions.
    answer = await service.ask(db, body.question, provider, principal.permissions)
    return AskOut(
        text=answer.text,
        items=[AskItem(**i) for i in answer.items],
        unmatched=answer.unmatched,
        model_used=answer.model_used,
    )
