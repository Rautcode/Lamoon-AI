"""Work facts, the payroll input ledger, establishments, and validation.

THE PERMISSION BOUNDARY THAT MATTERS HERE
-----------------------------------------
Work facts are hours, sites and shifts. The ledger is money. They are split
across two permission families on purpose:

    workfact.*   read, record and APPROVE what happened.  manager, HR, admin
    payroll.*    read and write what it costs.            HR, admin only

A supervisor who saw the overtime happen is the right person to approve it,
and approving it teaches them nothing about anyone's pay. Folding both into
`payroll.write` would have forced a choice between letting supervisors see
salaries and having payroll admins approve work they never witnessed.

The other rule enforced throughout: a period with a finalized run is closed.
Work facts and inputs for it are history, and history is not edited.
"""
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.audit import service as audit
from app.modules.hr_core.models import Employee
from app.modules.payroll import ledger, validation
from app.modules.payroll.models import PayrollRun
from app.modules.payroll.schemas import (
    ApproveIn,
    EstablishmentIn,
    EstablishmentOut,
    FindingOut,
    PayrollInputIn,
    PayrollInputOut,
    ValidationOut,
    WorkFactIn,
    WorkFactOut,
)
from app.modules.payroll.workforce import Establishment, PayrollInput, WorkFact

facts_router = APIRouter(prefix="/workforce", tags=["workforce"])
ledger_router = APIRouter(prefix="/payroll", tags=["payroll"])

CAN_READ_FACTS = [Depends(require("workfact.read"))]
CAN_WRITE_FACTS = [Depends(require("workfact.write"))]
CAN_APPROVE_FACTS = [Depends(require("workfact.approve"))]
CAN_READ_PAY = [Depends(require("payroll.read"))]
CAN_WRITE_PAY = [Depends(require("payroll.write"))]


def _period(value: date) -> date:
    return value.replace(day=1)


def _closed(db: Session, period: date) -> bool:
    """Has this period been finalized? A closed period is a record."""
    return db.scalar(
        select(PayrollRun.id).where(
            PayrollRun.period == _period(period),
            PayrollRun.status == "finalized",
            PayrollRun.deleted_at.is_(None),
        )
    ) is not None


def _refuse_if_closed(db: Session, period: date) -> None:
    if _closed(db, period):
        raise HTTPException(
            409,
            f"payroll for {_period(period).strftime('%B %Y')} is finalized — "
            "corrections belong in a later period as an adjustment",
        )


def _employee_or_404(db: Session, employee_id: uuid.UUID) -> Employee:
    emp = db.get(Employee, employee_id)
    if emp is None or emp.deleted_at is not None:
        raise HTTPException(404, "employee not found")
    return emp


# --- establishments ---------------------------------------------------------


@ledger_router.get(
    "/establishments", response_model=list[EstablishmentOut], dependencies=CAN_READ_PAY
)
def list_establishments(db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)):
    return db.scalars(
        select(Establishment)
        .where(Establishment.deleted_at.is_(None))
        .order_by(Establishment.name)
    ).all()


@ledger_router.post(
    "/establishments", response_model=EstablishmentOut, dependencies=CAN_WRITE_PAY
)
def create_establishment(
    body: EstablishmentIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    company_id = uuid.UUID(cid)
    if body.is_default:
        # Exactly one default, or "which jurisdiction applies?" has no answer.
        for other in db.scalars(
            select(Establishment).where(
                Establishment.is_default.is_(True), Establishment.deleted_at.is_(None)
            )
        ).all():
            other.is_default = False

    est = Establishment(company_id=company_id, **body.model_dump())
    db.add(est)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="establishment", entity_id=est.id,
        action="created", payload={"state_code": est.state_code, "name": est.name},
    )
    return est


# --- work facts -------------------------------------------------------------


@facts_router.get("/facts", response_model=list[WorkFactOut], dependencies=CAN_READ_FACTS)
def list_facts(
    employee_id: uuid.UUID | None = None,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    pending_only: bool = False,
    db: Session = Depends(get_db),
    _cid: str = Depends(resolve_tenant),
):
    stmt = select(WorkFact).where(WorkFact.deleted_at.is_(None))
    if employee_id:
        stmt = stmt.where(WorkFact.employee_id == employee_id)
    if from_:
        stmt = stmt.where(WorkFact.day >= from_)
    if to:
        stmt = stmt.where(WorkFact.day <= to)
    if pending_only:
        # The approval queue: what a supervisor still has to sign off.
        stmt = stmt.where(WorkFact.approved_at.is_(None))
    return db.scalars(stmt.order_by(WorkFact.day.desc()).limit(1000)).all()


@facts_router.post("/facts", response_model=WorkFactOut, dependencies=CAN_WRITE_FACTS)
def record_fact(
    body: WorkFactIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Record or replace one day.

    Upserts on (employee, day) because a day has one truth. Re-recording it
    clears any previous approval — an approved fact that quietly changed
    underneath its approver is worse than one that needs approving twice.
    """
    company_id = uuid.UUID(cid)
    _employee_or_404(db, body.employee_id)
    _refuse_if_closed(db, body.day)

    existing = db.scalar(
        select(WorkFact).where(
            WorkFact.employee_id == body.employee_id,
            WorkFact.day == body.day,
            WorkFact.deleted_at.is_(None),
        )
    )
    fields = body.model_dump()
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.approved_at = None
        existing.approved_by = None
        db.flush()
        return existing

    fact = WorkFact(company_id=company_id, **fields)
    db.add(fact)
    db.flush()
    return fact


@facts_router.post(
    "/facts/bulk", response_model=list[WorkFactOut], dependencies=CAN_WRITE_FACTS
)
def record_facts(
    body: list[WorkFactIn], db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Import a device export or a contractor's sheet. Arrives unapproved."""
    if len(body) > 1000:
        raise HTTPException(422, "send at most 1000 days per request")
    return [record_fact(item, db=db, cid=cid) for item in body]


@facts_router.post(
    "/facts/approve", response_model=list[WorkFactOut], dependencies=CAN_APPROVE_FACTS
)
def approve_facts(
    body: ApproveIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """Sign off work. Only approved facts reach payroll."""
    company_id = uuid.UUID(cid)
    now = datetime.now(UTC)
    approved: list[WorkFact] = []

    for fact_id in body.ids:
        fact = db.get(WorkFact, fact_id)
        if fact is None or fact.deleted_at is not None:
            raise HTTPException(404, f"work fact {fact_id} not found")
        _refuse_if_closed(db, fact.day)
        fact.approved_at = now
        fact.approved_by = uuid.UUID(principal.user_id)
        approved.append(fact)

    db.flush()
    audit.record(
        db, company_id=company_id, entity="work_fact", entity_id=approved[0].id,
        action="approved",
        payload={"count": len(approved), "days": [f.day.isoformat() for f in approved[:20]]},
    )
    return approved


@facts_router.delete("/facts/{fact_id}", status_code=204, dependencies=CAN_WRITE_FACTS)
def delete_fact(
    fact_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    fact = db.get(WorkFact, fact_id)
    if fact is None or fact.deleted_at is not None:
        raise HTTPException(404, "work fact not found")
    _refuse_if_closed(db, fact.day)
    fact.deleted_at = datetime.now(UTC)


# --- the payroll input ledger ------------------------------------------------


@ledger_router.get(
    "/inputs", response_model=list[PayrollInputOut], dependencies=CAN_READ_PAY
)
def list_inputs(
    employee_id: uuid.UUID,
    period: date,
    include_unapproved: bool = True,
    db: Session = Depends(get_db),
    _cid: str = Depends(resolve_tenant),
):
    """What payroll will use for this person this period, and what is pending.

    Defaults to showing unapproved rows: this is the operator's view of the
    ledger, and something waiting for approval is exactly what they need to
    see. The ENGINE never sees them — that filter lives in the service.
    """
    return ledger.inputs_for(
        db, employee_id, _period(period), include_unapproved=include_unapproved
    )


@ledger_router.post("/inputs", response_model=PayrollInputOut, dependencies=CAN_WRITE_PAY)
def create_input(
    body: PayrollInputIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """Add a manual input — a bonus, a recovery, a one-off allowance.

    Manual rows survive every recompute, which is what makes them worth
    entering. They are NOT auto-approved: entering a figure and sanctioning it
    are separable acts, and a customer who wants two pairs of eyes gets them.
    """
    company_id = uuid.UUID(cid)
    period = _period(body.period)
    _employee_or_404(db, body.employee_id)
    _refuse_if_closed(db, period)

    clash = db.scalar(
        select(PayrollInput).where(
            PayrollInput.employee_id == body.employee_id,
            PayrollInput.period == period,
            PayrollInput.code == body.code,
            PayrollInput.source == "manual",
            PayrollInput.deleted_at.is_(None),
        )
    )
    if clash:
        raise HTTPException(
            409, f"a manual input with code {body.code} already exists for this period"
        )

    row = PayrollInput(
        company_id=company_id,
        source="manual",
        created_by=uuid.UUID(principal.user_id),
        **body.model_dump(exclude={"period"}),
        period=period,
    )
    db.add(row)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="payroll_input", entity_id=row.id,
        action="created",
        # The amount stays out: the audit log has a wider readership than pay.
        payload={"code": row.code, "kind": row.kind, "period": period.isoformat()},
    )
    return row


@ledger_router.post(
    "/inputs/approve", response_model=list[PayrollInputOut], dependencies=CAN_WRITE_PAY
)
def approve_inputs(
    body: ApproveIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    now = datetime.now(UTC)
    rows: list[PayrollInput] = []
    for row_id in body.ids:
        row = db.get(PayrollInput, row_id)
        if row is None or row.deleted_at is not None:
            raise HTTPException(404, f"payroll input {row_id} not found")
        _refuse_if_closed(db, row.period)
        row.approved_at = now
        row.approved_by = uuid.UUID(principal.user_id)
        rows.append(row)
    db.flush()
    audit.record(
        db, company_id=uuid.UUID(cid), entity="payroll_input", entity_id=rows[0].id,
        action="approved", payload={"count": len(rows)},
    )
    return rows


@ledger_router.delete("/inputs/{input_id}", status_code=204, dependencies=CAN_WRITE_PAY)
def delete_input(
    input_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    row = db.get(PayrollInput, input_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "payroll input not found")
    _refuse_if_closed(db, row.period)
    if row.source != "manual":
        raise HTTPException(
            409,
            f"{row.source} inputs are derived — change the salary structure or the "
            "work facts behind them instead",
        )
    row.deleted_at = datetime.now(UTC)


# --- validation and risk, answered separately -------------------------------


def _findings_out(findings: list[validation.Finding]) -> list[FindingOut]:
    return [
        FindingOut(
            code=f.code, severity=f.severity, message=f.message,
            employee_id=f.employee_id, employee_name=f.employee_name,
            impact=f.impact, detail=f.detail,
        )
        for f in findings
    ]


@ledger_router.get("/validation", response_model=ValidationOut, dependencies=CAN_READ_PAY)
def run_validation(
    period: date, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Are the inputs valid? Blocking findings exclude an employee from the run."""
    p = _period(period)
    findings = validation.validate(db, company_id=uuid.UUID(cid), period=p)
    summary = validation.summarise(findings)
    return ValidationOut(period=p, findings=_findings_out(findings), **{
        k: summary[k] for k in ("blocking", "warnings", "info", "impact", "groups")
    })


@ledger_router.get("/risk", response_model=ValidationOut, dependencies=CAN_READ_PAY)
def run_risk(
    period: date, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Does anything look unusual? Separate from validation on purpose — every
    one of these may be entirely correct, and the value is that somebody
    looked."""
    p = _period(period)
    findings = validation.risk(db, company_id=uuid.UUID(cid), period=p)
    summary = validation.summarise(findings)
    return ValidationOut(period=p, findings=_findings_out(findings), **{
        k: summary[k] for k in ("blocking", "warnings", "info", "impact", "groups")
    })
