"""Entitlement engine (platform §7). Answers one question everywhere: *can this
company use this, now, within limits?* Pricing changes are data (entitlement
rows), never schema.

Types:
  boolean — on/off      (module.ats = true)
  limit   — a ceiling   (employees <= 100)
  quota   — consumable  (ai_credits balance >= amount)
"""
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

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


def _resolve(company_id: UUID | str, key: str) -> Entitlement | None:
    return None  # ponytail: DB lookup of entitlements row; None = deny is the safe default


def _current(company_id: UUID | str, key: str) -> float:
    return 0  # ponytail: count (limit) or remaining balance (quota) from DB


def can_use(company_id: UUID | str, key: str, *, amount: float = 1) -> Decision:
    """The one call that guards every gated action (module gate, seat cap, AI credits)."""
    return decide(_resolve(company_id, key), current=_current(company_id, key), amount=amount)
