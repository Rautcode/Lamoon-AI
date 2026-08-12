"""Contractor endpoints: the master, their invoices, and the reconciliation.

The one rule worth stating up front: **an invoice can always be RECORDED, and
only agreed when it matches what attendance says.** Those are different acts.
Refusing to record a disputed invoice would leave the disagreement invisible;
approving past one silently would make the whole comparison decorative.
"""
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth.provider import Principal
from app.core.db import get_db
from app.core.rbac import current_user, require
from app.core.tenant import resolve_tenant
from app.modules.audit import service as audit
from app.modules.payroll import contractors as service
from app.modules.payroll.schemas import (
    ContractorIn,
    ContractorOut,
    InvoiceIn,
    InvoiceOut,
    ReconciliationOut,
)
from app.modules.payroll.workforce import Contractor, ContractorInvoice

router = APIRouter(prefix="/payroll/contractors", tags=["payroll"])
CAN_READ = [Depends(require("payroll.read"))]
CAN_WRITE = [Depends(require("payroll.write"))]


def _period(value: date) -> date:
    return value.replace(day=1)


def _contractor_or_404(db: Session, contractor_id: uuid.UUID) -> Contractor:
    row = db.get(Contractor, contractor_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "contractor not found")
    return row


@router.get("", response_model=list[ContractorOut], dependencies=CAN_READ)
def list_contractors(db: Session = Depends(get_db), _cid: str = Depends(resolve_tenant)):
    return db.scalars(
        select(Contractor)
        .where(Contractor.deleted_at.is_(None))
        .order_by(Contractor.name)
    ).all()


@router.post("", response_model=ContractorOut, dependencies=CAN_WRITE)
def create_contractor(
    body: ContractorIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    company_id = uuid.UUID(cid)
    row = Contractor(company_id=company_id, **body.model_dump())
    db.add(row)
    db.flush()
    audit.record(
        db, company_id=company_id, entity="contractor", entity_id=row.id,
        action="created", payload={"name": row.name},
    )
    return row


@router.get("/reconciliation", response_model=list[ReconciliationOut], dependencies=CAN_READ)
def reconcile_all(
    period: date, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Every active contractor for a period, worst variance first.

    An operator triages by the size of the disagreement, not alphabetically.
    """
    return service.summary(db, company_id=uuid.UUID(cid), period=_period(period))


@router.get(
    "/{contractor_id}/reconciliation", response_model=ReconciliationOut,
    dependencies=CAN_READ,
)
def reconcile_one(
    contractor_id: uuid.UUID,
    period: date,
    db: Session = Depends(get_db),
    cid: str = Depends(resolve_tenant),
):
    """One contractor, with the worker-days behind the variance — which is
    what somebody actually takes back to them."""
    contractor = _contractor_or_404(db, contractor_id)
    return service.reconcile(
        db, company_id=uuid.UUID(cid), contractor=contractor, period=_period(period)
    )


@router.post("/invoices", response_model=InvoiceOut, dependencies=CAN_WRITE)
def record_invoice(
    body: InvoiceIn, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Record what was billed.

    Always allowed, whatever the variance. Refusing to record a disputed
    invoice would leave the disagreement nowhere, which is the opposite of
    what the reconciliation is for.
    """
    company_id = uuid.UUID(cid)
    contractor = _contractor_or_404(db, body.contractor_id)
    period = _period(body.period)

    existing = db.scalar(
        select(ContractorInvoice).where(
            ContractorInvoice.contractor_id == body.contractor_id,
            ContractorInvoice.period == period,
            ContractorInvoice.deleted_at.is_(None),
        )
    )
    if existing is not None:
        if existing.approved_at is not None:
            month = period.strftime("%B %Y")
            raise HTTPException(
                409,
                f"the {month} invoice for {contractor.name} is already approved — "
                "record a credit note rather than editing it",
            )
        # One invoice per contractor per period: a corrected invoice supersedes
        # rather than accompanies, so re-recording replaces.
        for field, value in body.model_dump(exclude={"period", "contractor_id"}).items():
            setattr(existing, field, value)
        db.flush()
        return existing

    row = ContractorInvoice(
        company_id=company_id,
        contractor_id=body.contractor_id,
        period=period,
        **body.model_dump(exclude={"period", "contractor_id"}),
    )
    db.add(row)
    db.flush()
    return row


@router.post("/invoices/{invoice_id}/approve", response_model=InvoiceOut,
             dependencies=CAN_WRITE)
def approve_invoice(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_user),
    cid: str = Depends(resolve_tenant),
):
    """Agree to pay it.

    Refused while it disagrees with attendance. The variance is the entire
    reason the figure is worth checking, and approving past one silently would
    make the check decorative — dispute it or fix the attendance first.
    """
    row = db.get(ContractorInvoice, invoice_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "invoice not found")
    if row.approved_at is not None:
        raise HTTPException(409, "already approved")

    contractor = _contractor_or_404(db, row.contractor_id)
    result = service.reconcile(
        db, company_id=uuid.UUID(cid), contractor=contractor, period=row.period
    )
    if result["variance"] != 0:
        raise HTTPException(
            409,
            f"this invoice differs from attendance by {result['variance']} — "
            "investigate or dispute it before approving",
        )

    row.status = "approved"
    row.approved_by = uuid.UUID(principal.user_id)
    row.approved_at = datetime.now(UTC)
    db.flush()
    audit.record(
        db, company_id=uuid.UUID(cid), entity="contractor_invoice", entity_id=row.id,
        action="approved",
        payload={"period": row.period.isoformat(), "amount": str(row.amount)},
    )
    return row


@router.post("/invoices/{invoice_id}/dispute", response_model=InvoiceOut,
             dependencies=CAN_WRITE)
def dispute_invoice(
    invoice_id: uuid.UUID, db: Session = Depends(get_db), cid: str = Depends(resolve_tenant)
):
    """Mark it as contested. Keeps the figure on record while it is argued
    about, rather than deleting the evidence of what was claimed."""
    row = db.get(ContractorInvoice, invoice_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "invoice not found")
    if row.approved_at is not None:
        raise HTTPException(409, "already approved — record a credit note instead")
    row.status = "disputed"
    db.flush()
    audit.record(
        db, company_id=uuid.UUID(cid), entity="contractor_invoice", entity_id=row.id,
        action="disputed",
    )
    return row
