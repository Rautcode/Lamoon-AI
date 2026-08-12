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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.audit import service as audit
from app.modules.hr_core.models import Employee
from app.modules.payroll import adjustments, ledger, movement, readiness, validation
from app.modules.payroll.models import PayrollRun
from app.modules.payroll.schemas import (
    AdjustmentIn,
    AdjustmentOut,
    ApproveIn,
    AssignEmployeesIn,
    AssignmentOut,
    EstablishmentIn,
    EstablishmentOut,
    FindingOut,
    MovementOut,
    PayrollInputIn,
    PayrollInputOut,
    ReadinessOut,
    RebuildIn,
    RebuildOut,
    ValidationOut,
    WorkFactIn,
    WorkFactOut,
)
from app.modules.payroll.workforce import (
    Establishment,
    PayrollAdjustment,
    PayrollInput,
    WorkFact,
)

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


@ledger_router.patch(
    "/establishments/{establishment_id}", response_model=EstablishmentOut,
    dependencies=CAN_WRITE_PAY,
)
def update_establishment(
    establishment_id: uuid.UUID,
    body: EstablishmentIn,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    est = db.get(Establishment, establishment_id)
    if est is None or est.deleted_at is not None:
        raise HTTPException(404, "establishment not found")

    if body.is_default and not est.is_default:
        for other in db.scalars(
            select(Establishment).where(
                Establishment.is_default.is_(True), Establishment.deleted_at.is_(None)
            )
        ).all():
            other.is_default = False

    before_state = est.state_code
    for field, value in body.model_dump().items():
        setattr(est, field, value)
    db.flush()
    audit.record(
        db, company_id=uuid.UUID(cid), entity="establishment", entity_id=est.id,
        action="updated",
        # A state change moves everyone here into a different PT schedule, so
        # it is worth naming in the log rather than recording "updated".
        payload={"state_code": est.state_code, "state_was": before_state},
    )
    return est


@ledger_router.delete(
    "/establishments/{establishment_id}", status_code=204, dependencies=CAN_WRITE_PAY
)
def delete_establishment(
    establishment_id: uuid.UUID,
    db: Session = Depends(get_db),
    _cid: str = Depends(resolve_tenant),
):
    """Refused while anyone is still attached.

    Removing it would silently move those people to the company-wide PT
    schedule — a different state's tax, applied without anyone deciding to.
    """
    est = db.get(Establishment, establishment_id)
    if est is None or est.deleted_at is not None:
        raise HTTPException(404, "establishment not found")

    attached = db.scalar(
        select(func.count())
        .select_from(Employee)
        .where(
            Employee.establishment_id == establishment_id,
            Employee.deleted_at.is_(None),
        )
    )
    if attached:
        raise HTTPException(
            409,
            f"{attached} employees are still attached — move them to another "
            "establishment first",
        )
    est.deleted_at = datetime.now(UTC)


@ledger_router.post(
    "/establishments/{establishment_id}/employees", response_model=AssignmentOut,
    dependencies=CAN_WRITE_PAY,
)
def assign_employees(
    establishment_id: uuid.UUID,
    body: AssignEmployeesIn,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    """Attach people to an establishment.

    This decides which state's professional tax and minimum wage apply to
    their pay, so it takes explicit ids rather than a filter — "everyone
    currently on screen" is not a jurisdiction.

    Finalized periods are untouched. Their payslips are frozen records that
    were correct under the jurisdiction in force when they were paid.
    """
    company_id = uuid.UUID(cid)
    est = db.get(Establishment, establishment_id)
    if est is None or est.deleted_at is not None:
        raise HTTPException(404, "establishment not found")

    employees = db.scalars(
        select(Employee).where(
            Employee.id.in_(body.employee_ids), Employee.deleted_at.is_(None)
        )
    ).all()
    found = {e.id for e in employees}
    missing = [str(i) for i in body.employee_ids if i not in found]
    if missing:
        raise HTTPException(404, f"employees not found: {', '.join(missing[:5])}")

    for emp in employees:
        emp.establishment_id = establishment_id
    db.flush()
    audit.record(
        db, company_id=company_id, entity="establishment", entity_id=establishment_id,
        action="employees_assigned",
        payload={"count": len(employees), "state_code": est.state_code},
    )
    return AssignmentOut(
        establishment_id=establishment_id,
        assigned=len(employees),
        note=(
            f"Professional tax and minimum wage for these {len(employees)} people now "
            f"follow {est.name} ({est.state_code}) from the next payroll run. "
            "Finalized periods are unchanged."
        ),
    )


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


@ledger_router.post("/inputs/rebuild", response_model=RebuildOut, dependencies=CAN_WRITE_PAY)
def rebuild_ledger(
    body: RebuildIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Regenerate the ledger for a period without computing payroll.

    Looking at what a period will consist of, and paying against it, are
    separate acts. This lets an operator review and correct the inputs first —
    a payroll run is a heavy and consequential way to ask what August contains.

    Idempotent, and safe to call as often as you like: derived rows are
    replaced, manual entries and adjustments are left standing.
    """
    company_id = uuid.UUID(cid)
    period = _period(body.period)
    _refuse_if_closed(db, period)
    if body.employee_id is not None:
        _employee_or_404(db, body.employee_id)

    summary = ledger.rebuild_period(
        db, company_id=company_id, period=period, employee_id=body.employee_id
    )
    audit.record(
        db, company_id=company_id, entity="payroll_input", entity_id=company_id,
        action="ledger_rebuilt",
        payload={
            "period": period.isoformat(),
            "employees": summary["employees"],
            "employee_id": str(body.employee_id) if body.employee_id else None,
        },
    )
    return RebuildOut(**summary)


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


# --- adjustments: the only lawful way to correct a closed month -------------


@ledger_router.get(
    "/adjustments", response_model=list[AdjustmentOut], dependencies=CAN_READ_PAY
)
def list_adjustments(
    target_period: date | None = None,
    employee_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _cid: str = Depends(resolve_tenant),
):
    return adjustments.for_period(
        db, target_period=target_period, employee_id=employee_id
    )


@ledger_router.post("/adjustments", response_model=AdjustmentOut, dependencies=CAN_WRITE_PAY)
def create_adjustment(
    body: AdjustmentIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """Record a correction to a finalized period.

    Creates no money. Approving it does — so a mistake can be written down by
    whoever spotted it without that person also being able to pay it.
    """
    company_id = uuid.UUID(cid)
    _employee_or_404(db, body.employee_id)
    try:
        row = adjustments.create(
            db, company_id=company_id, created_by=uuid.UUID(principal.user_id),
            **body.model_dump(),
        )
    except adjustments.AdjustmentError as e:
        raise HTTPException(422, str(e)) from None

    audit.record(
        db, company_id=company_id, entity="payroll_adjustment", entity_id=row.id,
        action="raised",
        payload={
            "source_period": row.source_period.isoformat(),
            "target_period": row.target_period.isoformat(),
            "kind": row.kind, "reason": row.reason,
        },
    )
    return row


@ledger_router.post(
    "/adjustments/{adjustment_id}/approve", response_model=AdjustmentOut,
    dependencies=CAN_WRITE_PAY,
)
def approve_adjustment(
    adjustment_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """Agree to settle it. THIS is what puts it in the ledger."""
    row = db.get(PayrollAdjustment, adjustment_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "adjustment not found")
    try:
        adjustments.approve(db, adjustment=row, approved_by=uuid.UUID(principal.user_id))
    except adjustments.AdjustmentError as e:
        raise HTTPException(422, str(e)) from None

    audit.record(
        db, company_id=uuid.UUID(cid), entity="payroll_adjustment", entity_id=row.id,
        action="approved",
        payload={"target_period": row.target_period.isoformat(), "kind": row.kind},
    )
    return row


@ledger_router.delete(
    "/adjustments/{adjustment_id}", status_code=204, dependencies=CAN_WRITE_PAY
)
def cancel_adjustment(
    adjustment_id: uuid.UUID, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Withdraw it, and the ledger row with it."""
    row = db.get(PayrollAdjustment, adjustment_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "adjustment not found")
    try:
        adjustments.cancel(db, adjustment=row)
    except adjustments.AdjustmentError as e:
        raise HTTPException(409, str(e)) from None
    audit.record(
        db, company_id=uuid.UUID(cid), entity="payroll_adjustment", entity_id=row.id,
        action="cancelled",
    )


@ledger_router.get("/movement", response_model=MovementOut, dependencies=CAN_READ_PAY)
def run_movement(
    period: date, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Period-on-period totals, and a bridge explaining the change in gross.

    The bridge sums exactly to the change it explains; whatever is left over
    comes back as `unexplained` rather than being folded into a bucket.
    """
    return MovementOut(
        **movement.compare(db, company_id=uuid.UUID(cid), period=_period(period))
    )


@ledger_router.get("/readiness", response_model=ReadinessOut, dependencies=CAN_READ_PAY)
def run_readiness(
    period: date, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Can payroll run at all? Configuration and coverage, per company.

    Distinct from validation, which asks whether one person's inputs are
    valid. "Nobody has a salary structure" belongs here; "Meera has no salary
    structure" belongs there.
    """
    return ReadinessOut(
        **readiness.evaluate(db, company_id=uuid.UUID(cid), period=_period(period))
    )


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
