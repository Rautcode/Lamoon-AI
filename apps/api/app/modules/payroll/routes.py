"""Payroll endpoints.

Salary is the most confidential data in an HRMS, so the permissions here are
tighter than anywhere else in the product:

- `payroll.read` / `payroll.write` — HR and admin only.
- **manager gets neither.** A manager can approve their team's leave and see
  their attendance; whether they may see their team's pay is a policy decision
  every company answers differently, and the safe default is no.
- an employee sees their own payslips, and only from FINALIZED runs — a draft
  is a number someone is still editing, and showing it as pay would generate
  a support ticket at best.
"""
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.audit import service as audit
from app.modules.compensation import service as compensation
from app.modules.compensation.models import CompensationLine, CompensationVersion
from app.modules.hr_core.models import Employee
from app.modules.payroll import service
from app.modules.payroll.models import (
    PayComponent,
    PayrollRun,
    Payslip,
    ProfessionalTaxSlab,
)
from app.modules.payroll.schemas import (
    PayComponentIn,
    PayComponentOut,
    PayrollSettingsIn,
    PayrollSettingsOut,
    PayslipAdjustIn,
    PayslipOut,
    PTSlabIn,
    PTSlabOut,
    RunDetailOut,
    RunIn,
    RunOut,
    SalaryComponentOut,
    SalaryStructureIn,
    SalaryStructureOut,
)
from app.modules.payroll.workforce import Establishment

router = APIRouter(prefix="/payroll", tags=["payroll"])
CAN_READ = [Depends(require("payroll.read"))]
CAN_WRITE = [Depends(require("payroll.write"))]


# --- configuration ----------------------------------------------------------


@router.get("/settings", response_model=PayrollSettingsOut, dependencies=CAN_READ)
def get_settings(db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    return service.get_settings(db, uuid.UUID(cid))


@router.put("/settings", response_model=PayrollSettingsOut, dependencies=CAN_WRITE)
def update_settings(
    body: PayrollSettingsIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    company_id = uuid.UUID(cid)
    row = service.get_settings(db, company_id)
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="payroll_settings", entity_id=row.id,
        action="updated", payload=body.model_dump(mode="json"),
    )
    return row


@router.get("/components", response_model=list[PayComponentOut], dependencies=CAN_READ)
def list_components(db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)):
    return db.scalars(
        select(PayComponent)
        .where(PayComponent.deleted_at.is_(None))
        .order_by(PayComponent.sequence, PayComponent.name)
    ).all()


@router.post("/components", response_model=PayComponentOut, dependencies=CAN_WRITE)
def create_component(
    body: PayComponentIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    existing = db.scalar(
        select(PayComponent).where(
            PayComponent.code == body.code, PayComponent.deleted_at.is_(None)
        )
    )
    if existing:
        raise HTTPException(409, f"a component with code {body.code} already exists")
    component = PayComponent(company_id=uuid.UUID(cid), **body.model_dump())
    db.add(component)
    db.flush()
    return component


@router.get("/pt-slabs", response_model=list[PTSlabOut], dependencies=CAN_READ)
def list_pt_slabs(
    establishment_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _cid: str = Depends(resolve_tenant),
):
    """One state's schedule, or the company-wide one when no establishment is
    named. Schedules are never merged — mixing two states' slabs would produce
    a deduction that belongs to neither."""
    rows = db.scalars(
        select(ProfessionalTaxSlab).where(
            ProfessionalTaxSlab.deleted_at.is_(None),
            ProfessionalTaxSlab.establishment_id == establishment_id
            if establishment_id is not None
            else ProfessionalTaxSlab.establishment_id.is_(None),
        )
    ).all()
    return sorted(rows, key=lambda s: (s.up_to is None, s.up_to or 0))


@router.put("/pt-slabs", response_model=list[PTSlabOut], dependencies=CAN_WRITE)
def replace_pt_slabs(
    body: list[PTSlabIn],
    establishment_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    """Replace one jurisdiction's schedule. A state's PT table is edited as a
    unit — patching one slab of a slab schedule is how you end up with a gap.

    Scoped: replacing Maharashtra's schedule must not touch Karnataka's."""
    company_id = uuid.UUID(cid)
    if sum(1 for s in body if s.up_to is None) > 1:
        raise HTTPException(422, "only one slab can be unbounded (up_to = null)")
    if establishment_id is not None:
        est = db.get(Establishment, establishment_id)
        if est is None or est.deleted_at is not None:
            raise HTTPException(404, "establishment not found")

    now = datetime.now(UTC)
    for old in db.scalars(
        select(ProfessionalTaxSlab).where(
            ProfessionalTaxSlab.deleted_at.is_(None),
            ProfessionalTaxSlab.establishment_id == establishment_id
            if establishment_id is not None
            else ProfessionalTaxSlab.establishment_id.is_(None),
        )
    ).all():
        old.deleted_at = now
    slabs = [
        ProfessionalTaxSlab(
            company_id=company_id, establishment_id=establishment_id, **s.model_dump()
        )
        for s in body
    ]
    db.add_all(slabs)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="pt_slabs", entity_id=company_id,
        action="replaced",
        payload={
            "establishment_id": str(establishment_id) if establishment_id else None,
            "slabs": [s.model_dump(mode="json") for s in body],
        },
    )
    return sorted(slabs, key=lambda s: (s.up_to is None, s.up_to or 0))


# --- salary structures ------------------------------------------------------


def _structure(
    db: Session, employee_id: uuid.UUID, *, on: date | None = None
) -> SalaryStructureOut:
    """The salary in force on a date — today unless asked otherwise.

    Reads the compensation timeline, not a live "current salary" row, because
    there no longer is one. Payroll itself does NOT come through here: it
    resolves by period in `ledger.seed_from_structure`, and "today" is not a
    payroll period.
    """
    version = compensation.current_version(db, employee_id=employee_id, on=on)
    if version is None:
        return SalaryStructureOut(employee_id=employee_id, components=[], monthly_gross=0)

    catalogue = {
        c.id: c
        for c in db.scalars(select(PayComponent).where(PayComponent.deleted_at.is_(None))).all()
    }
    pairs = [
        (line, catalogue[line.component_id])
        for line in compensation.lines_for(db, [version.id]).get(version.id, [])
        if line.component_id in catalogue
    ]
    components = [
        SalaryComponentOut(
            component_id=c.id, code=c.code, name=c.name, kind=c.kind, amount=line.amount
        )
        for line, c in sorted(pairs, key=lambda r: (r[1].sequence, r[1].name))
    ]
    return SalaryStructureOut(
        employee_id=employee_id,
        components=components,
        monthly_gross=sum((c.amount for c in components if c.kind == "earning"), start=0),
    )


@router.get(
    "/employees/{employee_id}/salary", response_model=SalaryStructureOut, dependencies=CAN_READ
)
def get_salary(
    employee_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    return _structure(db, employee_id)


@router.put(
    "/employees/{employee_id}/salary", response_model=SalaryStructureOut, dependencies=CAN_WRITE
)
def set_salary(
    employee_id: uuid.UUID,
    body: SalaryStructureIn,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    company_id = uuid.UUID(cid)
    employee = db.get(Employee, employee_id)
    if employee is None or employee.deleted_at is not None:
        raise HTTPException(404, "employee not found")

    known = {
        c.id
        for c in db.scalars(
            select(PayComponent).where(PayComponent.deleted_at.is_(None))
        ).all()
    }
    unknown = [str(c.component_id) for c in body.components if c.component_id not in known]
    if unknown:
        raise HTTPException(422, f"unknown pay components: {', '.join(unknown)}")

    # A compatibility shim over the compensation timeline. Salary is no longer
    # a value that can be overwritten, so this creates a VERSION.
    #
    # The effective date is the whole difficulty, because this endpoint does
    # not carry one. Two cases, and both defaults are chosen to avoid paying
    # somebody the wrong amount by accident:
    #
    #   FIRST salary — from their joining date, or EPOCH if that is unknown.
    #     There is no earlier version to conflict with, and dating it "today"
    #     would make every past period resolve to no salary at all and pay zero.
    #
    #   A CHANGE — from the 1st of the current month. An undated salary edit
    #     means "this is the pay for the period being worked on", not "prorate
    #     the month from the moment I clicked save". Anyone who genuinely means
    #     a mid-month change posts to /compensation/.../versions with the date,
    #     and gets proration.
    existing = compensation.current_version(db, employee_id=employee_id)
    effective_from = (
        date.today().replace(day=1)
        if existing
        else (employee.joined_on or compensation.EPOCH)
    )

    try:
        version = compensation.create_version(
            db, company_id=company_id, employee_id=employee_id,
            effective_from=effective_from,
            lines=[(c.component_id, c.amount) for c in body.components],
            reason="hire" if existing is None else "revision",
        )
    except compensation.OverlappingVersion:
        # A version already starts on that date — i.e. the salary was already
        # changed this month. Replace ITS lines rather than refusing or
        # stacking a second version on the same day: correcting a figure
        # entered an hour ago is not a pay revision, and this endpoint has no
        # way for the caller to say which it meant.
        clash = db.scalar(
            select(CompensationVersion).where(
                CompensationVersion.employee_id == employee_id,
                CompensationVersion.effective_from == effective_from,
                CompensationVersion.deleted_at.is_(None),
            )
        )
        if clash is None:  # pragma: no cover — the exception guarantees one
            raise HTTPException(409, "conflicting compensation version") from None
        version = clash
        now = datetime.now(UTC)
        for line in compensation.lines_for(db, [version.id]).get(version.id, []):
            line.deleted_at = now
        for c in body.components:
            db.add(
                CompensationLine(
                    company_id=company_id, version_id=version.id,
                    component_id=c.component_id, amount=c.amount,
                )
            )
        db.flush()

    structure = _structure(db, employee_id)
    # The amounts stay out of the audit payload: the audit log is readable by
    # more people than the salary is, and "who changed it, when" is the part
    # that needs to be tamper-evident.
    audit.record(
        db, company_id=company_id, entity="salary_structure", entity_id=employee_id,
        action="updated",
        payload={
            "component_count": len(body.components),
            "effective_from": effective_from.isoformat(),
            "version_id": str(version.id),
        },
    )
    return structure


# --- runs -------------------------------------------------------------------


def _run_or_404(db: Session, run_id: uuid.UUID) -> PayrollRun:
    run = db.get(PayrollRun, run_id)
    if run is None or run.deleted_at is not None:
        raise HTTPException(404, "payroll run not found")
    return run


def _payslips(db: Session, run_id: uuid.UUID) -> list[Payslip]:
    return list(
        db.scalars(
            select(Payslip)
            .where(Payslip.run_id == run_id, Payslip.deleted_at.is_(None))
            .order_by(Payslip.employee_name)
        ).all()
    )


def _detail(db: Session, run: PayrollRun) -> RunDetailOut:
    return RunDetailOut(
        **RunOut.model_validate(run).model_dump(),
        payslips=[PayslipOut.model_validate(p) for p in _payslips(db, run.id)],
    )


@router.get("/runs", response_model=list[RunOut], dependencies=CAN_READ)
def list_runs(db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)):
    return db.scalars(
        select(PayrollRun)
        .where(PayrollRun.deleted_at.is_(None))
        .order_by(PayrollRun.period.desc())
    ).all()


@router.post("/runs", response_model=RunDetailOut, dependencies=CAN_WRITE)
def create_run(body: RunIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)):
    """Open (or reopen) the draft for a month and compute every payslip.

    Calling this twice for the same month recomputes the existing draft rather
    than creating a second one — the unique constraint on (company, period)
    means there is exactly one payroll for March, always.
    """
    company_id = uuid.UUID(cid)
    period = body.period.replace(day=1)
    run = db.scalar(
        select(PayrollRun).where(PayrollRun.period == period, PayrollRun.deleted_at.is_(None))
    )
    if run is None:
        run = PayrollRun(company_id=company_id, period=period)
        db.add(run)
        db.flush()
    try:
        service.build_run(db, company_id=company_id, run=run)
    except service.RunFinalized as e:
        raise HTTPException(409, str(e)) from None

    audit.record(
        db, company_id=company_id, entity="payroll_run", entity_id=run.id,
        action="computed", payload={"period": period.isoformat(), "net_total": str(run.net_total)},
    )
    return _detail(db, run)


@router.get("/runs/{run_id}", response_model=RunDetailOut, dependencies=CAN_READ)
def get_run(
    run_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    return _detail(db, _run_or_404(db, run_id))


@router.patch(
    "/runs/{run_id}/payslips/{payslip_id}", response_model=PayslipOut, dependencies=CAN_WRITE
)
def adjust_payslip(
    run_id: uuid.UUID,
    payslip_id: uuid.UUID,
    body: PayslipAdjustIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    company_id = uuid.UUID(cid)
    run = _run_or_404(db, run_id)
    if run.status == "finalized":
        raise HTTPException(409, "this run is finalized and cannot be changed")

    slip = db.get(Payslip, payslip_id)
    if slip is None or slip.run_id != run_id or slip.deleted_at is not None:
        raise HTTPException(404, "payslip not found")
    employee = db.get(Employee, slip.employee_id)
    if employee is None:
        raise HTTPException(404, "employee not found")

    if body.lop_days is not None:
        slip.lop_days = body.lop_days
        slip.lop_overridden = True  # survives the next recompute
    if body.tds is not None:
        slip.tds = body.tds
        # Provenance travels with the amount. "Why was ₹4,850 deducted?" has to
        # be answerable from the payslip, not from someone's memory.
        slip.tds_source = body.tds_source
        slip.tds_tax_year = body.tds_tax_year
        slip.tds_note = body.tds_note
        slip.tds_provided_by = uuid.UUID(principal.user_id)
        slip.tds_provided_at = datetime.now(UTC)

    # Resolve unpaid days the same way a run does, rather than reusing the
    # stored total. Passing the stored value back was the bug: it already
    # included the pre-joining shortfall, which then got added a second time,
    # so editing only the TDS on a mid-month joiner zeroed their pay.
    lop = service.lop_for(
        db, company_id=company_id, employee=employee, period=run.period, prior=slip
    )
    computed = service.compute_payslip(
        db, company_id=company_id, employee=employee, period=run.period,
        lop_days=lop, tds=slip.tds,
    )
    for field in (
        "employee_name", "period", "working_days", "paid_days", "lop_days",
        "gross", "deductions", "net", "employer_cost", "esi_employee", "breakdown",
    ):
        setattr(slip, field, computed[field])
    db.flush()

    # Recompute the run totals from the payslips so the header can't drift
    # away from the rows it is supposed to be summing.
    slips = _payslips(db, run_id)
    zero = Decimal("0")
    run.gross_total = sum((p.gross for p in slips), start=zero)
    run.deductions_total = sum((p.deductions for p in slips), start=zero)
    run.net_total = sum((p.net for p in slips), start=zero)
    # The establishment admin top-up is a run-level figure, not a payslip one.
    run.employer_cost_total = sum(
        (p.employer_cost for p in slips), start=zero
    ) + run.admin_shortfall
    db.flush()

    audit.record(
        db, company_id=company_id, entity="payslip", entity_id=slip.id, action="adjusted",
        payload={"lop_days": slip.lop_days, "tds": str(slip.tds)},
    )
    return slip


@router.post("/runs/{run_id}/finalize", response_model=RunOut, dependencies=CAN_WRITE)
def finalize_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """One way. After this the numbers are what was paid, and the only way to
    correct a mistake is an adjustment in a later month — which is also how
    payroll corrections work outside software."""
    run = _run_or_404(db, run_id)
    if run.status == "finalized":
        raise HTTPException(409, "this run is already finalized")
    if not _payslips(db, run_id):
        raise HTTPException(422, "nothing to finalize: this run has no payslips")

    run.status = "finalized"
    run.finalized_at = datetime.now(UTC)
    run.finalized_by = uuid.UUID(principal.user_id)
    db.flush()
    audit.record(
        db, company_id=uuid.UUID(cid), entity="payroll_run", entity_id=run.id,
        action="finalized",
        payload={"period": run.period.isoformat(), "net_total": str(run.net_total)},
    )
    return run


@router.get(
    "/employees/{employee_id}/payslips", response_model=list[PayslipOut], dependencies=CAN_READ
)
def employee_payslips(
    employee_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    return db.scalars(
        select(Payslip)
        .join(PayrollRun, PayrollRun.id == Payslip.run_id)
        .where(Payslip.employee_id == employee_id, Payslip.deleted_at.is_(None))
        .order_by(PayrollRun.period.desc())
    ).all()
