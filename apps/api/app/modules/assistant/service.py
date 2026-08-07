"""Lumo orchestration.

Two paths, one set of tools:

  * **Model path** (GEMINI_API_KEY set) — Gemini picks tools, we execute them
    against real data, Gemini writes the prose from the results.
  * **Fallback path** (no key) — keyword routing picks one tool and we use its
    own sentence verbatim.

Both are grounded in `tools.py`, so the product answers correctly either way
and the model is an upgrade to the *language*, never to the *facts*. The
fallback is also what runs if the model errors out mid-conversation — a
degraded answer beats a 500.
"""
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai.provider import AIProvider
from app.core.config import get_settings
from app.modules.assistant import tools

logger = logging.getLogger("lamoon.assistant")

# Bounded so a confused model can't spin on tool calls. Two rounds is enough
# for "call a tool, read the result, answer" plus one retry.
MAX_STEPS = 3

SYSTEM = """You are Lumo, the assistant inside Lamoon HR.

Rules you must follow:
- Answer ONLY from tool results. Never invent names, numbers, dates or facts.
- If the tools don't contain the answer, say you don't have that information.
- Be brief: one or two sentences. No preamble, no bullet lists.
- Don't restate the full list of results — the interface already shows them.
- Today's date is available from the who_is_on_leave tool if you need it.
"""


@dataclass
class Answer:
    text: str
    items: list[dict] = field(default_factory=list)
    unmatched: bool = False
    #: True when a real model wrote the prose; False on the keyword fallback.
    model_used: bool = False


FALLBACK_SUGGESTIONS = [
    {"title": "Who's on leave today?"},
    {"title": "Show pending leave requests"},
    {"title": "Find Tier A candidates"},
    {"title": "How many people do we have?"},
    {"title": "What roles are open?"},
]


def _has(q: str, *words: str) -> bool:
    return any(w in q for w in words)


def _route(question: str) -> tuple[str, dict] | None:
    """Keyword router for the no-model path. Deliberately dumb and predictable."""
    q = question.lower().strip()
    if not q:
        return None
    if _has(q, "how many", "headcount", "team size", "total employees"):
        return ("headcount", {})
    if _has(q, "leave", "time off", "vacation", "holiday", "absent"):
        if _has(q, "pending", "approve", "waiting", "awaiting", "decision"):
            return ("pending_leave_requests", {})
        return ("who_is_on_leave", {})
    if _has(q, "candidate", "applicant", "hiring", "shortlist", "tier", "interview"):
        tier = "A" if _has(q, "tier a", "best", "top", "strongest", "shortlist") else None
        return ("find_candidates", {"tier": tier} if tier else {})
    if _has(q, "job", "role", "opening", "position", "vacanc"):
        return ("open_roles", {})
    if _has(q, "department", "team", "org", "structure"):
        return ("list_departments", {})
    # Last resort: hand the whole question to the name lookup, which strips
    # stopwords and matches on any remaining word.
    return ("find_person", {"name": question})


def _fallback(db: Session, question: str) -> Answer:
    route = _route(question)
    if route is None:
        return Answer(
            text="I can't answer that one yet. Here's what I can do today:",
            items=FALLBACK_SUGGESTIONS,
            unmatched=True,
        )
    name, args = route
    result = tools.run_tool(db, name, args)
    if not result.items and name == "find_person":
        return Answer(
            text="I can't answer that one yet. Here's what I can do today:",
            items=FALLBACK_SUGGESTIONS,
            unmatched=True,
        )
    return Answer(text=result.text, items=result.items)


async def ask(db: Session, question: str, provider: AIProvider | None = None) -> Answer:
    question = (question or "").strip()
    if not question:
        return Answer(text="Ask me anything about your people, hiring, or time off.")

    if not get_settings().gemini_api_key or provider is None:
        return _fallback(db, question)

    messages: list[dict] = [{"role": "user", "text": question}]
    collected: list[dict] = []

    try:
        for _ in range(MAX_STEPS):
            turn = await provider.converse(system=SYSTEM, messages=messages, tools=tools.SPECS)

            if not turn.tool_calls:
                text = (turn.text or "").strip()
                if not text:
                    break  # model said nothing useful — fall through to keywords
                return Answer(text=text, items=collected, model_used=True)

            messages.append(
                {
                    "role": "model",
                    "text": turn.text,
                    "tool_calls": [{"name": c.name, "args": c.args} for c in turn.tool_calls],
                }
            )
            for call in turn.tool_calls:
                result = tools.run_tool(db, call.name, call.args)
                # Items come from the tool, never from the model — this is what
                # makes a hallucinated name impossible to click.
                collected.extend(result.items)
                messages.append({"role": "tool", "name": call.name, "result": result.data})
    except Exception:
        # Network blip, bad key, SDK change — the product should still answer.
        logger.exception("Lumo model path failed; using keyword fallback")

    return _fallback(db, question)
