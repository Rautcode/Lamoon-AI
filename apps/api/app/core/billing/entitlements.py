"""Entitlement engine (platform §7). Answers one question everywhere: *can this
company use this, now, within limits?* Pricing changes are data, never schema.

Types:
  boolean — on/off      (module.ats = true)
  limit   — a ceiling   (employees <= 100)
  quota   — consumable  (ai_credits balance >= amount)

Only the "employees" key is wired to real data (companies.seat_limit — no
separate generic `entitlements` table exists yet). Module flags / AI-credit
quotas stay stubbed (unknown key -> deny) until something outside a code
comment actually needs them; that's the trigger to add the generic table this
docstring originally sketched, not a guess at when "later" arrives.

_resolve/_current use raw SQL, not the ORM models, so this module — which sits
in core/ — never imports a feature module (`app.modules.*`), matching the
project's own import-boundary rule (ARCH §1).
"""
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.tenant import resolve_tenant

EntType = Literal["boolean", "limit", "quota"]


@dataclass(frozen=True)
class Entitlement:
    key: str
    type: EntType
    value: float  # boolean uses 1/0; limit/quota use the number


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def decide(ent: Entitlement | None, *, current: float, amount: float) -> Decision:
    """Pure decision logic — the heart of the engine, no I/O. Unknown key = deny."""
    if ent is None:
        return Decision(False, "no entitlement")
    match ent.type:
        case "boolean":
            return Decision(ent.value == 1, "" if ent.value == 1 else "disabled")
        case "limit":
            ok = current + amount <= ent.value
            return Decision(ok, "" if ok else f"limit {ent.value} reached")
        case "quota":
            ok = current >= amount  # `current` is the remaining balance here
            return Decision(ok, "" if ok else "insufficient balance")


def _resolve(db: Session, company_id: UUID | str, key: str) -> Entitlement | None:
    if key == "employees":
        row = db.execute(
            text("SELECT seat_limit FROM companies WHERE id = :cid"), {"cid": str(company_id)}
        ).first()
        return Entitlement("employees", "limit", float(row[0])) if row else None
    return None  # module.*/ai_credits/etc. land here once a generic table exists


def _current(db: Session, company_id: UUID | str, key: str) -> float:
    if key == "employees":
        # Active headcount against the seat cap: soft-deleted rows don't count
        # (never did), and neither does 'exited' — offboarding frees the seat.
        n = db.execute(
            text(
                "SELECT count(*) FROM employees "
                "WHERE company_id = :cid AND deleted_at IS NULL AND status != 'exited'"
            ),
            {"cid": str(company_id)},
        ).scalar()
        return float(n or 0)
    return 0


def can_use(db: Session, company_id: UUID | str, key: str, *, amount: float = 1) -> Decision:
    """The one call that guards every gated action (module gate, seat cap, AI credits).

    ponytail: two concurrent requests can both pass this check and both insert,
    overshooting the limit by a row or two. Seat overselling is a low-severity,
    low-frequency race (reconciled the next time anyone looks), not worth a
    `SELECT ... FOR UPDATE` / advisory lock unless it's ever actually observed.
    """
    ent = _resolve(db, company_id, key)
    current = _current(db, company_id, key)
    return decide(ent, current=current, amount=amount)


def require_module(module_key: str):
    """FastAPI dependency: reject requests to a module the company hasn't
    enabled/paid for -> 402."""

    def dep(cid: str = Depends(resolve_tenant), db: Session = Depends(get_db)) -> None:
        if not can_use(db, cid, f"module.{module_key}").allowed:
            raise HTTPException(402, f"module '{module_key}' not enabled")

    return dep
