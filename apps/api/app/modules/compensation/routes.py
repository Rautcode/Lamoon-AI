"""Compensation endpoints.

Reading a salary and reading its history are the same permission
(`payroll.read`) because they are the same secret. Writing one is
`payroll.write`.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.audit import service as audit
from app.modules.compensation import service
from app.modules.compensation.models import CompensationVersion
from app.modules.compensation.schemas import LineOut, VersionIn, VersionOut
from app.modules.hr_core.models import Employee
from app.modules.payroll.models import PayComponent, PayrollRun

router = APIRouter(prefix="/compensation", tags=["compensation"])
CAN_READ = [Depends(require("payroll.read"))]
CAN_WRITE = [Depends(require("payroll.write"))]


def _employee_or_404(db: Session, employee_id: uuid.UUID) -> Employee:
    row = db.get(Employee, employee_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "employee not found")
    return row


def _render(db: Session, version: CompensationVersion) -> VersionOut:
    components = {
        c.id: c
        for c in db.scalars(select(PayComponent).where(PayComponent.deleted_at.is_(None))).all()
    }
    lines = []
    gross = service.ZERO
    for line in service.lines_for(db, [version.id]).get(version.id, []):
        component = components.get(line.component_id)
        if component is None:
            continue
        lines.append(
            LineOut(
                component_id=line.component_id, code=component.code,
                name=component.name, amount=line.amount,
            )
        )
        if component.kind != "deduction":
            gross += line.amount
    lines.sort(key=lambda x: x.code)
    return VersionOut(
        id=version.id, employee_id=version.employee_id,
        effective_from=version.effective_from, effective_to=version.effective_to,
        reason=version.reason, note=version.note, gross=gross, lines=lines,
    )


@router.get(
    "/employees/{employee_id}/versions", response_model=list[VersionOut],
    dependencies=CAN_READ,
)
def list_versions(
    employee_id: uuid.UUID, db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)
):
    """Newest first — the current salary is the thing people look for."""
    _employee_or_404(db, employee_id)
    rows = db.scalars(
        select(CompensationVersion)
        .where(
            CompensationVersion.employee_id == employee_id,
            CompensationVersion.deleted_at.is_(None),
        )
        .order_by(CompensationVersion.effective_from.desc())
    ).all()
    return [_render(db, v) for v in rows]


@router.get("/employees/{employee_id}/resolve", response_model=VersionOut | None,
            dependencies=CAN_READ)
def resolve(
    employee_id: uuid.UUID,
    on: date | None = None,
    db: Session = Depends(get_db),
    _cid: str = Depends(resolve_tenant),
):
    """What applied on a given date. Answers "why was August paid at that
    rate" without anybody reconstructing the timeline by hand."""
    _employee_or_404(db, employee_id)
    version = service.current_version(db, employee_id=employee_id, on=on)
    return _render(db, version) if version else None


@router.post(
    "/employees/{employee_id}/versions", response_model=VersionOut, dependencies=CAN_WRITE
)
def create_version(
    employee_id: uuid.UUID,
    body: VersionIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """Add a version. Never edits an existing one.

    A version dated into an already-finalized period is ALLOWED — the history
    has to be able to become correct — but it does not rewrite those payslips.
    They are frozen, and the difference is owed as arrears, which is an
    adjustment in a later period. The response says so rather than leaving
    somebody to discover it at year end.
    """
    company_id = uuid.UUID(cid)
    _employee_or_404(db, employee_id)

    known = {
        c.id
        for c in db.scalars(select(PayComponent).where(PayComponent.deleted_at.is_(None))).all()
    }
    unknown = [str(x.component_id) for x in body.lines if x.component_id not in known]
    if unknown:
        raise HTTPException(422, f"unknown pay components: {', '.join(unknown)}")
    if not body.lines:
        raise HTTPException(422, "a compensation version needs at least one component")

    try:
        version = service.create_version(
            db, company_id=company_id, employee_id=employee_id,
            effective_from=body.effective_from,
            lines=[(x.component_id, x.amount) for x in body.lines],
            reason=body.validated_reason(), note=body.note,
            created_by=uuid.UUID(principal.user_id),
        )
    except service.OverlappingVersion as e:
        raise HTTPException(409, str(e)) from None

    # Finalized periods this version reaches back into. Reported, never rewritten.
    affected = db.scalars(
        select(PayrollRun.period)
        .where(
            PayrollRun.status == "finalized",
            PayrollRun.deleted_at.is_(None),
            PayrollRun.period >= body.effective_from.replace(day=1),
        )
        .order_by(PayrollRun.period)
    ).all()

    audit.record(
        db, company_id=company_id, entity="compensation_version", entity_id=version.id,
        action="created",
        # Amounts stay out: the audit log is readable by more people than the
        # salary is. Who changed it and from when is the tamper-evident part.
        payload={
            "employee_id": str(employee_id),
            "effective_from": body.effective_from.isoformat(),
            "reason": version.reason,
            "component_count": len(body.lines),
            "finalized_periods_affected": [p.isoformat() for p in affected],
        },
    )
    return _render(db, version)


@router.delete("/versions/{version_id}", status_code=204, dependencies=CAN_WRITE)
def delete_version(
    version_id: uuid.UUID, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Remove a version entered in error, and re-open the previous one.

    Only ever the LATEST version — deleting one from the middle would leave a
    gap in the timeline, and a period that resolves to no salary pays nobody.
    """
    version = db.get(CompensationVersion, version_id)
    if version is None or version.deleted_at is not None:
        raise HTTPException(404, "version not found")

    siblings = db.scalars(
        select(CompensationVersion)
        .where(
            CompensationVersion.employee_id == version.employee_id,
            CompensationVersion.deleted_at.is_(None),
        )
        .order_by(CompensationVersion.effective_from)
    ).all()
    if siblings[-1].id != version.id:
        raise HTTPException(
            409,
            "only the most recent compensation version can be removed — deleting an "
            "earlier one would leave a period with no salary at all",
        )

    from datetime import UTC, datetime

    version.deleted_at = datetime.now(UTC)
    if len(siblings) > 1:
        siblings[-2].effective_to = None  # re-open the one it had closed
    audit.record(
        db, company_id=uuid.UUID(cid), entity="compensation_version",
        entity_id=version.id, action="deleted",
    )
