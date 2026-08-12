"""Contractor reconciliation — what attendance says, against what was billed.

    approved work facts ─→ payroll for their workers ─→ COMPUTED
                                                          │
                                        contractor's invoice ─→ INVOICED
                                                          │
                                                       VARIANCE

Billing for days nobody worked is the most common leak in site payroll, and it
is invisible until those two figures sit next to each other. So the variance is
the headline, and the worker-level breakdown behind it is what an operator
takes back to the contractor.

WHAT "COMPUTED" MEANS HERE
It is what the engine calculated for that contractor's deployed workers in the
period — the same figures on their payslips, from the same approved work facts.
Not an estimate, and not a second calculation: if the two ever disagreed, one
of them would be wrong and nobody would know which.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.hr_core.models import Employee
from app.modules.payroll import ledger, statutory
from app.modules.payroll.models import Payslip
from app.modules.payroll.workforce import Contractor, ContractorInvoice, WorkFact

ZERO = Decimal("0")


def workers_of(db: Session, contractor_id: uuid.UUID) -> list[Employee]:
    return list(
        db.scalars(
            select(Employee).where(
                Employee.contractor_id == contractor_id,
                Employee.deleted_at.is_(None),
            )
        ).all()
    )


def reconcile(
    db: Session, *, company_id: uuid.UUID, contractor: Contractor, period: date
) -> dict:
    """One contractor, one period: computed, invoiced, and the gap."""
    period = period.replace(day=1)
    start, end = ledger.month_bounds(period)
    workers = workers_of(db, contractor.id)
    worker_ids = {w.id for w in workers}

    slips = {
        p.employee_id: p
        for p in db.scalars(
            select(Payslip).where(
                Payslip.period == period, Payslip.deleted_at.is_(None)
            )
        ).all()
        if p.employee_id in worker_ids
    }

    facts = (
        db.scalars(
            select(WorkFact).where(
                WorkFact.employee_id.in_(worker_ids),
                WorkFact.day >= start,
                WorkFact.day <= end,
                WorkFact.deleted_at.is_(None),
            )
        ).all()
        if worker_ids
        else []
    )
    by_worker: dict[uuid.UUID, list[WorkFact]] = {}
    for f in facts:
        by_worker.setdefault(f.employee_id, []).append(f)

    lines = []
    computed = ZERO
    unbilled_days = 0
    for w in workers:
        slip = slips.get(w.id)
        w_facts = by_worker.get(w.id, [])
        approved = [f for f in w_facts if f.approved_at is not None]
        pending = [f for f in w_facts if f.approved_at is None]
        pay = slip.gross if slip else ZERO
        computed += pay
        unbilled_days += len(pending)
        lines.append(
            {
                "employee_id": w.id,
                "name": w.full_name,
                "site": next((f.site for f in w_facts if f.site), None),
                "days_approved": sum(1 for f in approved if f.status == "worked"),
                "days_pending": len(pending),
                "overtime_hours": sum((f.overtime_hours for f in approved), start=ZERO),
                "computed": statutory.money(pay),
                # Somebody with no payslip is deployed but unpaid — usually a
                # missing salary structure, and always worth naming before the
                # invoice is agreed.
                "has_payslip": slip is not None,
            }
        )

    invoice = db.scalar(
        select(ContractorInvoice).where(
            ContractorInvoice.contractor_id == contractor.id,
            ContractorInvoice.period == period,
            ContractorInvoice.deleted_at.is_(None),
        )
    )
    invoiced = invoice.amount if invoice else None
    computed = statutory.money(computed)

    return {
        "contractor_id": contractor.id,
        "contractor_name": contractor.name,
        "period": period,
        "workers": len(workers),
        "computed": computed,
        "invoiced": invoiced,
        # None, not zero, when nothing has been billed yet. Zero would read as
        # "they invoiced nothing", which is a different and much worse claim.
        "variance": statutory.money(invoiced - computed) if invoiced is not None else None,
        "invoice_id": invoice.id if invoice else None,
        "invoice_status": invoice.status if invoice else None,
        "invoice_reference": invoice.reference if invoice else None,
        "workers_without_pay": sum(1 for ln in lines if not ln["has_payslip"]),
        "days_awaiting_approval": unbilled_days,
        "lines": sorted(lines, key=lambda ln: ln["name"]),
    }


def summary(db: Session, *, company_id: uuid.UUID, period: date) -> list[dict]:
    """Every active contractor for a period, worst variance first — an
    operator triages by the size of the disagreement, not alphabetically."""
    contractors = db.scalars(
        select(Contractor).where(
            Contractor.is_active.is_(True), Contractor.deleted_at.is_(None)
        )
    ).all()
    rows = [
        reconcile(db, company_id=company_id, contractor=c, period=period)
        for c in contractors
    ]
    return sorted(
        rows,
        key=lambda r: abs(r["variance"]) if r["variance"] is not None else ZERO,
        reverse=True,
    )
