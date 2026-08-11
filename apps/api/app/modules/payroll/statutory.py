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

Rates below are national and statutory. Ceilings are passed in, because the
government moves them and a redeploy is a bad way to find that out.
"""
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

ZERO = Decimal("0")

EPF_RATE = Decimal("0.12")
#: Of the employer's 12%, 8.33% is diverted to the pension scheme (EPS) and the
#: remainder stays in EPF. EPS is capped on a ₹15,000 wage regardless of what
#: the employer contributes on — that cap is statutory and separate from the
#: company's own PF ceiling choice, so it is not configurable.
EPS_RATE = Decimal("0.0833")
EPS_WAGE_CAP = Decimal("15000")

ESI_EMPLOYEE_RATE = Decimal("0.0075")
ESI_EMPLOYER_RATE = Decimal("0.0325")


def rupees(amount: Decimal) -> Decimal:
    """Statutory contributions are remitted in whole rupees."""
    return Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def money(amount: Decimal) -> Decimal:
    """Everything else carries paise."""
    return Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def provident_fund(
    pf_wage: Decimal, *, ceiling: Decimal, on_full_wage: bool = False
) -> dict[str, Decimal]:
    """EPF at 12% each side, with the employer's share split into EPS and EPF.

    `on_full_wage` contributes on the whole PF wage instead of capping it at
    the ceiling. Both are lawful and the choice is the employer's; capping is
    the common default, which is why it's the default here.

    The employer's EPF share is the remainder AFTER pension, not another
    rounded percentage — deriving it by subtraction is what keeps
    employee-share and employer-share reconciling to the same 12% total
    instead of drifting a rupee apart.
    """
    base = pf_wage if on_full_wage else min(pf_wage, ceiling)
    if base <= ZERO:
        return {"employee": ZERO, "employer_epf": ZERO, "employer_eps": ZERO, "wage": ZERO}

    employee = rupees(base * EPF_RATE)
    employer_total = rupees(base * EPF_RATE)
    pension = rupees(min(base, EPS_WAGE_CAP) * EPS_RATE)
    return {
        "employee": employee,
        "employer_eps": pension,
        "employer_epf": employer_total - pension,
        "wage": money(base),
    }


def esi(gross: Decimal, *, ceiling: Decimal, locked_in: bool = False) -> dict[str, Decimal]:
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
        "employee": Decimal(gross * ESI_EMPLOYEE_RATE).quantize(
            Decimal("1"), rounding=ROUND_CEILING
        ),
        "employer": Decimal(gross * ESI_EMPLOYER_RATE).quantize(
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
