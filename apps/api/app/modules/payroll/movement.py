"""Period-on-period movement, and why the number changed.

A finance lead's first question about payroll is never "what is it" — it is
"why is it different". A line chart cannot answer that. A bridge can:

    gross moved +1,10,000
      new employees      +2,70,000
      salary revisions   +1,40,000
      overtime             +80,000
      exits              −3,99,000
      loss of pay          −41,000

THE PROPERTY THAT MAKES THIS WORTH HAVING
-----------------------------------------
The bridge must SUM EXACTLY to the change it explains. A decomposition that
doesn't close is worse than none: it invites someone to trust four of the five
lines. So the arithmetic below is exact by construction, and whatever is left
over is published as `unexplained` rather than quietly folded into a bucket.
That figure should always be zero; if it ever isn't, it is visible.

SEPARATING A RAISE FROM AN ABSENCE
----------------------------------
For somebody present in both months, their structure pay changed for two
independent reasons: the rate moved, and the days they were paid for moved.
Attributing the whole delta to either is wrong. Writing F for full monthly pay
and r for the fraction of the month paid:

    revision  = (F_now − F_before) × r_now      the raise, at today's attendance
    attendance=  F_before × (r_now − r_before)  the days, at yesterday's rate

which expands to F_now·r_now − F_before·r_before — exactly their delta, with no
remainder. The choice of which factor to hold constant is a convention (this is
the standard Laspeyres/Paasche split); it is stated here because the two halves
would differ if it were made the other way.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.payroll import statutory
from app.modules.payroll.models import Payslip

ZERO = Decimal("0")

#: Lines the engine generates from approved work facts.
WORK_CODES = {"OT", "PREMIUM"}


def previous_period(period: date) -> date:
    first = period.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def _slips(db: Session, period: date) -> dict[uuid.UUID, Payslip]:
    return {
        p.employee_id: p
        for p in db.scalars(
            select(Payslip).where(
                Payslip.period == period.replace(day=1), Payslip.deleted_at.is_(None)
            )
        ).all()
    }


def _line_total(slip: Payslip, key: str, predicate) -> Decimal:
    return sum(
        (Decimal(line["amount"]) for line in slip.breakdown.get(key, []) if predicate(line)),
        start=ZERO,
    )


def _deduction(slip: Payslip, code: str) -> Decimal:
    return _line_total(slip, "deductions", lambda ln: ln["code"] == code)


def _work_pay(slip: Payslip) -> Decimal:
    """Overtime and premium-day pay — derived from hours, not from a structure."""
    return _line_total(
        slip, "earnings",
        lambda ln: ln["code"] in WORK_CODES or ln.get("source") == "work_facts",
    )


def _entered_pay(slip: Payslip) -> Decimal:
    """One-off pay a person entered: bonuses, corrections carried forward."""
    return _line_total(
        slip, "earnings", lambda ln: ln.get("source") in ("manual", "adjustment")
    )


def _structure_pay(slip: Payslip) -> Decimal:
    """Everything else — the salary itself, already prorated."""
    return _line_total(
        slip, "earnings",
        lambda ln: ln["code"] not in WORK_CODES
        and ln.get("source") not in ("manual", "adjustment", "work_facts"),
    )


def _paid_ratio(slip: Payslip) -> Decimal:
    """The fraction of the month this person was paid for. A month with no
    working days counts as fully paid rather than dividing by zero."""
    if not slip.working_days:
        return Decimal("1")
    return Decimal(slip.paid_days) / Decimal(slip.working_days)


def compare(db: Session, *, company_id: uuid.UUID, period: date) -> dict:
    """Totals for two periods, and the bridge between their gross."""
    period = period.replace(day=1)
    prior = previous_period(period)
    now, before = _slips(db, period), _slips(db, prior)

    def totals(slips: dict[uuid.UUID, Payslip]) -> dict:
        return {
            "employees": len(slips),
            "gross": sum((s.gross for s in slips.values()), start=ZERO),
            "deductions": sum((s.deductions for s in slips.values()), start=ZERO),
            "net": sum((s.net for s in slips.values()), start=ZERO),
            "employer_cost": sum((s.employer_cost for s in slips.values()), start=ZERO),
            "pf": sum((_deduction(s, "EPF") for s in slips.values()), start=ZERO),
            "esi": sum((_deduction(s, "ESI") for s in slips.values()), start=ZERO),
            "pt": sum((_deduction(s, "PT") for s in slips.values()), start=ZERO),
            "tds": sum((_deduction(s, "TDS") for s in slips.values()), start=ZERO),
        }

    current, prev_totals = totals(now), totals(before)

    if not before:
        # Nothing to compare against. Saying so is the honest answer; inventing
        # a baseline of zero would report a company's first payroll as infinite
        # growth.
        return {
            "period": period, "previous_period": prior, "comparable": False,
            "current": current, "previous": prev_totals, "lines": [],
            "bridge": [], "unexplained": ZERO,
        }

    joined = [e for e in now if e not in before]
    left = [e for e in before if e not in now]
    stayed = [e for e in now if e in before]

    joiners = sum((now[e].gross for e in joined), start=ZERO)
    leavers = -sum((before[e].gross for e in left), start=ZERO)

    revision = attendance = overtime = entered = ZERO
    for e in stayed:
        a, b = now[e], before[e]
        r_now, r_before = _paid_ratio(a), _paid_ratio(b)

        # Un-prorate to compare like with like, then split the delta into the
        # rate change and the days change. See the module docstring.
        full_now = _structure_pay(a) / r_now if r_now else ZERO
        full_before = _structure_pay(b) / r_before if r_before else ZERO
        revision += (full_now - full_before) * r_now
        attendance += full_before * (r_now - r_before)

        overtime += _work_pay(a) - _work_pay(b)
        entered += _entered_pay(a) - _entered_pay(b)

    bridge = [
        ("joiners", "New employees", statutory.money(joiners), len(joined)),
        ("leavers", "Exits", statutory.money(leavers), len(left)),
        ("revision", "Salary revisions", statutory.money(revision), None),
        ("overtime", "Overtime and premium work", statutory.money(overtime), None),
        ("attendance", "Loss of pay and attendance", statutory.money(attendance), None),
        ("entered", "Bonuses and adjustments", statutory.money(entered), None),
    ]

    delta = current["gross"] - prev_totals["gross"]
    explained = sum((amount for _, _, amount, _ in bridge), start=ZERO)

    return {
        "period": period,
        "previous_period": prior,
        "comparable": True,
        "current": current,
        "previous": prev_totals,
        "lines": [
            {
                "code": code,
                "label": label,
                "previous": prev_totals[code],
                "current": current[code],
                "change": current[code] - prev_totals[code],
            }
            for code, label in [
                ("gross", "Gross"), ("pf", "Provident fund"), ("esi", "ESI"),
                ("pt", "Professional tax"), ("tds", "Income tax"),
                ("deductions", "Total deductions"),
                ("employer_cost", "Employer cost"), ("net", "Net payable"),
            ]
        ],
        # Only lines that moved. A bridge full of zeroes hides the ones that
        # didn't.
        "bridge": [
            {"code": c, "label": lbl, "amount": amt, "count": n}
            for c, lbl, amt, n in bridge
            if amt != ZERO or n
        ],
        # Should always be zero. Published rather than absorbed, so that if the
        # decomposition ever stops closing, it is visible instead of silently
        # wrong.
        "unexplained": statutory.money(delta - explained),
    }
