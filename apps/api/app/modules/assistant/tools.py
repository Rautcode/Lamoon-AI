"""Lumo's tools — the ONLY way facts enter an answer.

The grounding contract, and the reason this module exists:

  * The model writes prose. It never supplies data.
  * `ToolResult.data` is what the model sees — real rows, already scoped to
    the caller's tenant by RLS.
  * `ToolResult.items` is what the UI renders as clickable results. These come
    straight from the DB and are passed around the model, so a hallucinated
    name can never become a link the user can click.

If the model invents a name in its prose, that's a (visible, correctable)
prose error. It can never invent an entity the product then treats as real.

This module imports other feature modules' models, which the usual boundary
rule discourages. That's deliberate here: an assistant that answers questions
across HR is by definition a cross-module reader, and typed model access beats
hand-rolled SQL for something this query-shaped.
"""
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.ai.provider import ToolSpec
from app.modules.ats.models import AIAnalysis, Application, Candidate, JobOpening
from app.modules.hr_core.models import Department, Employee
from app.modules.leave.models import LeaveRequest, LeaveType


@dataclass
class ToolResult:
    data: dict[str, Any]                      # shown to the model
    items: list[dict] = field(default_factory=list)  # rendered by the UI
    text: str = ""                            # used verbatim when no model is available


def _employee_names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = db.scalars(select(Employee).where(Employee.id.in_(ids))).all()
    return {e.id: e.full_name for e in rows}


# --- tools -----------------------------------------------------------------


def headcount(db: Session) -> ToolResult:
    rows = db.scalars(select(Employee).where(Employee.deleted_at.is_(None))).all()
    by_status: dict[str, int] = {}
    for e in rows:
        by_status[e.status] = by_status.get(e.status, 0) + 1
    return ToolResult(
        data={"total": len(rows), "by_status": by_status},
        items=[
            {"title": e.full_name, "meta": e.status, "href": f"/people/{e.id}"} for e in rows[:6]
        ],
        text=f"{len(rows)} people on the books — {by_status.get('active', 0)} active.",
    )


def who_is_on_leave(db: Session, on: str | None = None) -> ToolResult:
    try:
        day = date.fromisoformat(on) if on else datetime.now(UTC).date()
    except ValueError:
        day = datetime.now(UTC).date()

    rows = db.scalars(
        select(LeaveRequest).where(
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= day,
            LeaveRequest.end_date >= day,
            LeaveRequest.deleted_at.is_(None),
        )
    ).all()
    names = _employee_names(db, {r.employee_id for r in rows})
    return ToolResult(
        data={
            "date": day.isoformat(),
            "count": len(rows),
            "people": [
                {"name": names.get(r.employee_id, "Unknown"), "returns": r.end_date.isoformat()}
                for r in rows
            ],
        },
        items=[
            {
                "title": names.get(r.employee_id, "Unknown"),
                "meta": f"back {r.end_date.isoformat()}",
                "href": "/time",
            }
            for r in rows
        ],
        text=(
            f"{len(rows)} {'person is' if len(rows) == 1 else 'people are'} on leave "
            f"on {day.isoformat()}."
            if rows
            else f"Nobody is on leave on {day.isoformat()}."
        ),
    )


def pending_leave_requests(db: Session) -> ToolResult:
    rows = db.scalars(
        select(LeaveRequest)
        .where(LeaveRequest.status == "pending", LeaveRequest.deleted_at.is_(None))
        .order_by(LeaveRequest.created_at)
    ).all()
    names = _employee_names(db, {r.employee_id for r in rows})
    types = {t.id: t.name for t in db.scalars(select(LeaveType)).all()}
    return ToolResult(
        data={
            "count": len(rows),
            "requests": [
                {
                    "name": names.get(r.employee_id, "Unknown"),
                    "type": types.get(r.leave_type_id, "Leave"),
                    "days": r.days,
                    "start": r.start_date.isoformat(),
                    "end": r.end_date.isoformat(),
                }
                for r in rows
            ],
        },
        items=[
            {
                "title": names.get(r.employee_id, "Unknown"),
                "meta": f"{r.days}d · {r.start_date.isoformat()}",
                "href": "/time",
            }
            for r in rows
        ],
        text=(
            f"{len(rows)} leave {'request needs' if len(rows) == 1 else 'requests need'} "
            "a decision."
            if rows
            else "Nothing waiting — all leave requests are decided."
        ),
    )


def find_candidates(db: Session, tier: str | None = None, limit: int = 8) -> ToolResult:
    limit = max(1, min(int(limit or 8), 25))
    q = select(Application).where(Application.deleted_at.is_(None))
    if tier:
        q = q.where(Application.tier == tier.upper()[:1])
    apps = list(db.scalars(q).all())

    jobs = {j.id: j.title for j in db.scalars(select(JobOpening)).all()}
    cands = {
        c.id: c
        for c in db.scalars(
            select(Candidate).where(Candidate.id.in_({a.candidate_id for a in apps}))
        ).all()
    }
    scores = {
        an.application_id: an.final_score
        for an in db.scalars(
            select(AIAnalysis).where(AIAnalysis.application_id.in_([a.id for a in apps]))
        ).all()
    }
    shown = apps[:limit]

    def cand_name(a: Application) -> str | None:
        c = cands.get(a.candidate_id)
        return c.full_name if c else None

    def role_name(a: Application) -> str | None:
        return jobs.get(a.job_opening_id) if a.job_opening_id else None

    return ToolResult(
        data={
            "count": len(apps),
            "tier_filter": tier,
            "candidates": [
                {
                    "name": cand_name(a),
                    "role": role_name(a),
                    "tier": a.tier,
                    "score": scores.get(a.id),
                    "status": a.status,
                }
                for a in shown
            ],
        },
        items=[
            {
                # Fall back to the role when a resume had no parseable name —
                # better a clickable "Backend Engineer" than a blank row.
                "title": cand_name(a) or role_name(a) or "Candidate",
                "meta": f"{'Tier ' + a.tier + ' · ' if a.tier else ''}{a.status.replace('_', ' ')}",
                "href": "/hiring",
            }
            for a in shown
        ],
        text=(
            f"{len(apps)} {('Tier ' + tier.upper()[:1] + ' ') if tier else ''}"
            f"{'candidate' if len(apps) == 1 else 'candidates'} in the pipeline."
            if apps
            else "No candidates match that."
        ),
    )


def open_roles(db: Session) -> ToolResult:
    rows = db.scalars(
        select(JobOpening).where(JobOpening.status == "open", JobOpening.deleted_at.is_(None))
    ).all()
    return ToolResult(
        data={"count": len(rows), "roles": [j.title for j in rows[:20]]},
        items=[{"title": j.title, "meta": "open", "href": "/hiring"} for j in rows[:10]],
        text=f"{len(rows)} open {'role' if len(rows) == 1 else 'roles'}.",
    )


def list_departments(db: Session) -> ToolResult:
    rows = db.scalars(select(Department).where(Department.deleted_at.is_(None))).all()
    counts: dict[uuid.UUID | None, int] = {
        dept_id: n
        for dept_id, n in db.execute(
            select(Employee.department_id, func.count())
            .where(Employee.deleted_at.is_(None))
            .group_by(Employee.department_id)
        ).all()
    }
    return ToolResult(
        data={
            "count": len(rows),
            "departments": [
                {"name": d.name, "headcount": counts.get(d.id, 0)} for d in rows
            ],
        },
        items=[
            {"title": d.name, "meta": f"{counts.get(d.id, 0)} people", "href": "/org"}
            for d in rows[:10]
        ],
        text=f"{len(rows)} {'department' if len(rows) == 1 else 'departments'}.",
    )


#: Dropped before name matching so a whole question ("tell me about Asha") works
#: as well as a bare name. The fallback router passes raw user text, and the
#: model isn't guaranteed to extract cleanly either.
_STOPWORDS = {
    "tell", "me", "about", "who", "is", "the", "what", "show", "find", "for",
    "does", "do", "know", "you", "any", "info", "information", "person",
    "employee", "details", "and", "with", "give", "get", "look", "up", "her",
    "his", "their", "them", "profile",
}


def find_person(db: Session, name: str = "") -> ToolResult:
    words = [
        w.strip(".,?!'\"")
        for w in (name or "").lower().split()
        if len(w.strip(".,?!'\"")) > 2 and w.strip(".,?!'\"") not in _STOPWORDS
    ]
    # Match ANY meaningful word, not the phrase — "Asha Rao", "asha", and
    # "tell me about asha" should all find the same person.
    rows = (
        db.scalars(
            select(Employee)
            .where(Employee.deleted_at.is_(None))
            .where(or_(*[Employee.full_name.ilike(f"%{w}%") for w in words]))
        ).all()
        if words
        else []
    )
    return ToolResult(
        data={
            "count": len(rows),
            "people": [
                {"name": e.full_name, "status": e.status, "email": e.email} for e in rows[:8]
            ],
        },
        items=[
            {"title": e.full_name, "meta": e.email or e.status, "href": f"/people/{e.id}"}
            for e in rows[:8]
        ],
        text=(
            f"Found {rows[0].full_name}."
            if len(rows) == 1
            else f"{len(rows)} people match “{name}”."
            if rows
            else f"Nobody matches “{name}”."
        ),
    )


# --- registry --------------------------------------------------------------

REGISTRY: dict[str, Callable[..., ToolResult]] = {
    "headcount": headcount,
    "who_is_on_leave": who_is_on_leave,
    "pending_leave_requests": pending_leave_requests,
    "find_candidates": find_candidates,
    "open_roles": open_roles,
    "list_departments": list_departments,
    "find_person": find_person,
}

SPECS: list[ToolSpec] = [
    ToolSpec(
        name="headcount",
        description="Total number of employees and a breakdown by status.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="who_is_on_leave",
        description="People on approved leave on a given date. Defaults to today.",
        parameters={
            "type": "object",
            "properties": {
                "on": {"type": "string", "description": "ISO date (YYYY-MM-DD). Omit for today."}
            },
        },
    ),
    ToolSpec(
        name="pending_leave_requests",
        description="Leave requests awaiting an approve/reject decision.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="find_candidates",
        description=(
            "Job applicants in the hiring pipeline, with AI screening tier and score. "
            "Filter by tier A/B/C/D — A is strongest."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tier": {"type": "string", "description": "One of A, B, C, D."},
                "limit": {"type": "integer", "description": "Max candidates to return."},
            },
        },
    ),
    ToolSpec(
        name="open_roles",
        description="Job openings currently accepting applications.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="list_departments",
        description="Departments with their headcount.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="find_person",
        description="Look up employees by (partial) name.",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Full or partial name."}},
            "required": ["name"],
        },
    ),
]


# SECURITY: Lumo is a second door onto the same data, so it needs the same
# locks as the routes. Without this an employee could ask "find Tier A
# candidates" and read the whole pipeline that /ats/applications denies them.
# Employees hold only `self.*`, so today they get NO tools and Lumo honestly
# says it can't help — self-scoped tools ("my balance") are the follow-up.
TOOL_PERMISSIONS: dict[str, str] = {
    "headcount": "employee.read",
    "who_is_on_leave": "leave.read",
    "pending_leave_requests": "leave.read",
    "find_candidates": "ats.read",
    "open_roles": "ats.read",
    "list_departments": "employee.read",
    "find_person": "employee.read",
}


def allowed(permissions: frozenset[str], tool_name: str) -> bool:
    needed = TOOL_PERMISSIONS.get(tool_name)
    if needed is None:
        return False  # unmapped tool = closed by default
    return "*" in permissions or needed in permissions


def specs_for(permissions: frozenset[str]) -> list[ToolSpec]:
    """Only advertise tools the caller may actually use — the model can't ask
    for what it can't see, which beats refusing after the fact."""
    return [s for s in SPECS if allowed(permissions, s.name)]


def run_tool(db: Session, name: str, args: dict) -> ToolResult:
    """Execute a tool by name. Unknown tools and bad arguments fail closed with
    a result the model can read, rather than raising into the request."""
    fn = REGISTRY.get(name)
    if fn is None:
        return ToolResult(data={"error": f"unknown tool {name}"}, text="I can't do that yet.")
    try:
        return fn(db, **(args or {}))
    except TypeError:
        return ToolResult(
            data={"error": "bad arguments"}, text="I didn't understand the details of that."
        )
