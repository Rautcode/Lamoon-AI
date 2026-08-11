"""Indian statutory deductions: EPF, ESI, professional tax.

Pure functions over `Decimal`. No database, no ORM — every rule here is
testable with numbers you can check against a payslip by hand, which for
money is the only kind of test worth having.

WHAT THIS DOES NOT COMPUTE
--------------------------
**Income tax (TDS).** Deliberately, and this is the most important line in
the module. Doing it correctly needs the employee's regime election, their
investment declarations and the proofs behind them, HRA exemption from actual
rent paid, chapter VI-A deductions, surcharge and cess — an entire
declarations-and-proofs subsystem that does not exist here. A payroll system
that half-computes TDS produces numbers that look authoritative and are
wrong, and the employer wears the interest and penalty for short deduction.
So TDS is an INPUT: whatever the employer's CA says, entered per employee and
overridable per month.

Also not modelled: gratuity, statutory bonus, labour welfare fund, and the
Maharashtra February professional-tax top-up (₹300 rather than ₹200 in the
last month of the year — worth ₹100/employee/year, and easy to add as a
per-slab month rule when a Maharashtra customer needs it).

Rates arrive as an effective-dated rule object (see `rules.py`) rather than
module constants, so a run for a past period computes under that period's law.
Ceilings are passed in separately because they are per-establishment
configuration, not law the customer can't choose.
"""
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.modules.payroll.rules import EpfRule, EsiRule

ZERO = Decimal("0")



def rupees(amount: Decimal) -> Decimal:
    """Statutory contributions are remitted in whole rupees."""
    return Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def money(amount: Decimal) -> Decimal:
    """Everything else carries paise."""
    return Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def provident_fund(
    pf_wage: Decimal,
    *,
    ceiling: Decimal,
    rule: EpfRule,
    on_full_wage: bool = False,
    eps_eligible: bool = True,
) -> dict[str, Decimal]:
    """EPF at 12% each side, with the employer's share split into EPS and EPF.

    `on_full_wage` contributes on the whole PF wage instead of capping it at
    the ceiling. Both are lawful and the choice is the employer's; capping is
    the common default, which is why it's the default here.

    Also returns the two employer-only charges that ride alongside the 12%:
    EDLI (insurance) and EPF administration. Administration additionally has a
    per-ESTABLISHMENT monthly floor, which cannot be settled on one payslip —
    `admin_shortfall_for` handles that at run level.

    The employer's EPF share is the remainder AFTER pension, not another
    rounded percentage — deriving it by subtraction is what keeps
    employee-share and employer-share reconciling to the same 12% total
    instead of drifting a rupee apart. That also means an EPS-ineligible
    employee needs no special arithmetic: pension is zero and the whole
    employer contribution falls through to EPF, which is exactly the rule.
    """
    base = pf_wage if on_full_wage else min(pf_wage, ceiling)
    if base <= ZERO:
        return {
            "employee": ZERO, "employer_epf": ZERO, "employer_eps": ZERO,
            "employer_edli": ZERO, "employer_admin": ZERO, "wage": ZERO,
        }

    employee = rupees(base * rule.employee_rate)
    employer_total = rupees(base * rule.employer_rate)
    pension = (
        rupees(min(base, rule.pension_wage_cap) * rule.pension_rate)
        if eps_eligible
        else ZERO
    )
    return {
        "employee": employee,
        "employer_eps": pension,
        "employer_epf": employer_total - pension,
        # Employer-only, on top of the 12%. Leaving these out understates what
        # the month actually costs by about 1% of PF wages.
        "employer_edli": rupees(min(base, rule.edli_wage_cap) * rule.edli_rate),
        "employer_admin": rupees(base * rule.admin_rate),
        "wage": money(base),
    }


def esi(
    gross: Decimal, *, ceiling: Decimal, rule: EsiRule, locked_in: bool = False
) -> dict[str, Decimal]:
    """ESI at 0.75% employee / 3.25% employer, on gross, below the ceiling.

    `locked_in` implements the contribution-period rule: ESI runs Apr–Sep and
    Oct–Mar, and someone who crosses the wage ceiling *mid-period* keeps
    contributing until that period ends. Without it, a mid-year raise stops
    contributions early and the employer under-remits.

    Rounded UP to the next rupee, per ESIC regulation 40 — not half-up like
    PF. Two schemes, two rounding rules; that is genuinely how they work.
    """
    if gross <= ZERO or (gross > ceiling and not locked_in):
        return {"employee": ZERO, "employer": ZERO, "wage": ZERO}
    return {
        "employee": Decimal(gross * rule.employee_rate).quantize(
            Decimal("1"), rounding=ROUND_CEILING
        ),
        "employer": Decimal(gross * rule.employer_rate).quantize(
            Decimal("1"), rounding=ROUND_CEILING
        ),
        "wage": money(gross),
    }


def contribution_period_start(period: date) -> date:
    """First month of the ESI contribution period containing `period`:
    April for Apr–Sep, October for Oct–Mar.

    Note Oct–Mar straddles the new year, so January's period started in
    October of the PREVIOUS year — the off-by-one that makes this worth a
    function rather than an inline expression."""
    if period.month >= 10 or period.month <= 3:
        year = period.year if period.month >= 10 else period.year - 1
        return date(year, 10, 1)
    return date(period.year, 4, 1)


def professional_tax(gross: Decimal, slabs: list[tuple[Decimal | None, Decimal]]) -> Decimal:
    """First slab whose upper bound the gross does not exceed.

    `slabs` is `[(up_to, amount)]` with `up_to=None` meaning the unbounded top
    slab. No slabs configured means no PT — correct for Delhi, Haryana, UP and
    the other states that don't levy it, and the safe default everywhere else.
    """
    for up_to, amount in sorted(slabs, key=lambda s: (s[0] is None, s[0] or ZERO)):
        if up_to is None or gross <= up_to:
            return money(amount)
    return ZERO


def admin_shortfall_for(charged: Decimal, *, rule: EpfRule, has_members: bool) -> Decimal:
    """Whatever must be added to the summed per-employee administration charges
    to reach the establishment's monthly minimum.

    The floor is per establishment, not per employee, so a five-person company
    paying 0.5% of a small wage bill still owes the minimum. Returned as a
    separate run-level figure rather than smeared across payslips: it is not
    attributable to any one employee, and putting it on a payslip would make
    that payslip's arithmetic stop reconciling.
    """
    if not has_members:
        return ZERO
    return max(ZERO, rule.admin_min_per_establishment - charged)
