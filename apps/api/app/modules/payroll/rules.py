"""Effective-dated statutory rules, and the wage definition they apply to.

WHY THIS EXISTS
---------------
Two problems, one answer.

**The wage basis moved.** Until the Labour Codes, PF was computed on a set of
components the employer nominated (basic + DA, broadly). From 21 November 2025
the Code on Wages definition applies: if the excluded allowances exceed half of
total remuneration, the excess is added back into wages. An employer paying
₹12,000 basic inside a ₹41,000 package no longer has a ₹12,000 PF basis.

**Rules have dates.** A payroll run for August 2026 must be computable in 2028,
under August 2026's rules, or you cannot defend a number to an auditor or
re-run a corrected month. Rates as module constants make that impossible — the
moment a rate changes, history silently re-computes wrong.

So every rule here carries `effective_from` and a `version`, resolution is by
the payroll PERIOD rather than by today's date, and the resolved versions are
stamped into the payslip snapshot.

ponytail: rules live in code, not a database table. They are national law, not
customer configuration — a customer cannot lawfully choose a different EPF
rate, and a rules table would invite them to try. Code means they are
reviewed, diffed and released like any other change. The genuinely
per-customer parts (whether a scheme applies, PT slabs) already live in the
database. Move a rule family into a table when a customer needs to differ
lawfully — per-establishment PT is the likely first.
"""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")


def _money(amount: Decimal) -> Decimal:
    """Two decimal places. Halving remuneration for the 50% test otherwise
    leaves trailing precision that reaches the payslip snapshot as
    `20500.000` — money is 2dp everywhere in this product, including in
    intermediate values that get published."""
    return Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

#: The day the Code on Wages definition of "wages" took effect.
LABOUR_CODE_START = date(2025, 11, 21)


@dataclass(frozen=True)
class WageDefinition:
    """How to derive the statutory wage from a salary structure."""

    version: str
    effective_from: date
    #: When set, the excluded-allowance test applies: anything excluded beyond
    #: this share of total remuneration is added back into wages. `None` is the
    #: pre-Labour-Code behaviour — the nominated components and nothing else.
    excluded_share_cap: Decimal | None


@dataclass(frozen=True)
class EpfRule:
    version: str
    effective_from: date
    employee_rate: Decimal
    employer_rate: Decimal
    pension_rate: Decimal
    #: EPS is capped on this wage however much the employer contributes on.
    pension_wage_cap: Decimal


@dataclass(frozen=True)
class EsiRule:
    version: str
    effective_from: date
    employee_rate: Decimal
    employer_rate: Decimal


WAGE_DEFINITIONS: list[WageDefinition] = [
    WageDefinition(
        version="wages-nominated-components",
        effective_from=date(1952, 1, 1),
        excluded_share_cap=None,
    ),
    WageDefinition(
        version="wages-code-on-wages-2025-11",
        effective_from=LABOUR_CODE_START,
        excluded_share_cap=Decimal("0.5"),
    ),
]

EPF_RULES: list[EpfRule] = [
    EpfRule(
        version="epf-2014-09",
        effective_from=date(2014, 9, 1),
        employee_rate=Decimal("0.12"),
        employer_rate=Decimal("0.12"),
        pension_rate=Decimal("0.0833"),
        pension_wage_cap=Decimal("15000"),
    ),
]

ESI_RULES: list[EsiRule] = [
    EsiRule(
        version="esi-2019-07",
        effective_from=date(2019, 7, 1),
        employee_rate=Decimal("0.0075"),
        employer_rate=Decimal("0.0325"),
    ),
]


def _resolve(rules: list, period: date):
    """The latest rule in force on `period`.

    Resolution is by the payroll period, never by today — that is the whole
    point. Re-running March 2026 in 2028 must use March 2026's rules.
    """
    applicable = [r for r in rules if r.effective_from <= period]
    if not applicable:
        raise ValueError(f"no rule in force for {period.isoformat()}")
    return max(applicable, key=lambda r: r.effective_from)


def wage_definition_for(period: date) -> WageDefinition:
    return _resolve(WAGE_DEFINITIONS, period)


def epf_rule_for(period: date) -> EpfRule:
    return _resolve(EPF_RULES, period)


def esi_rule_for(period: date) -> EsiRule:
    return _resolve(ESI_RULES, period)


# --- the wage definition itself ---------------------------------------------

#: How a pay component is treated when deriving the statutory wage.
#:   "wages"     — basic, DA, retaining allowance. Always in.
#:   "excluded"  — HRA, conveyance, special allowance, overtime, commission.
#:                 Out, but counted in the total the 50% test is measured against.
#:   "outside"   — not remuneration at all (reimbursement of actual expense).
#:                 Neither in wages nor in the denominator.
WAGE_BASIS_VALUES = ("wages", "excluded", "outside")


@dataclass(frozen=True)
class WageBasis:
    """The statutory wage and the arithmetic that produced it, so a payslip can
    explain itself without anyone re-deriving the database state."""

    statutory_wage: Decimal
    nominated_wages: Decimal
    excluded: Decimal
    remuneration: Decimal
    added_back: Decimal
    version: str


def statutory_wage(
    lines: list[tuple[Decimal, str]], definition: WageDefinition
) -> WageBasis:
    """Derive the statutory wage from `(amount, wage_basis)` pairs.

    Under the Code on Wages, "remuneration" is wages plus the excluded
    allowances; overtime counts toward it. If the excluded portion exceeds half
    of that, the excess is added back — so an employer cannot shrink their PF
    liability by moving pay into allowances.

    Under the earlier definition the excluded portion is simply ignored, which
    is why `excluded_share_cap=None` short-circuits.
    """
    wages = _money(sum((a for a, k in lines if k == "wages"), start=ZERO))
    excluded = _money(sum((a for a, k in lines if k == "excluded"), start=ZERO))
    remuneration = wages + excluded

    if definition.excluded_share_cap is None:
        return WageBasis(
            statutory_wage=wages, nominated_wages=wages, excluded=excluded,
            remuneration=remuneration, added_back=_money(ZERO),
            version=definition.version,
        )

    permitted = remuneration * definition.excluded_share_cap
    added_back = _money(max(ZERO, excluded - permitted))
    return WageBasis(
        statutory_wage=_money(wages + added_back),
        nominated_wages=wages,
        excluded=excluded,
        remuneration=remuneration,
        added_back=added_back,
        version=definition.version,
    )
