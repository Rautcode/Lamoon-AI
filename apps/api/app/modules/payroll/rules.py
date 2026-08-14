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
    """How to derive the statutory wage from a salary structure.

    Scoped by STATUTE and JURISDICTION, because the Codes were not notified all
    at once: only certain provisions took effect on 21 November 2025, Central
    Rules followed, and state rules follow separately. So EPF can be on the
    revised basis while another statute is not, and Maharashtra can differ from
    Karnataka. A single global date cannot express that, and gets it silently
    wrong for months at a time.
    """

    version: str
    effective_from: date
    #: When set, the excluded-allowance test applies: anything excluded beyond
    #: this share of total remuneration is added back into wages. `None` is the
    #: pre-Labour-Code behaviour — the nominated components and nothing else.
    excluded_share_cap: Decimal | None
    #: None = applies to every statute that has no rule of its own.
    statute: str | None = None
    #: None = central. A state code ("MH") overrides the centre for that state.
    jurisdiction: str | None = None


@dataclass(frozen=True)
class EpfRule:
    version: str
    effective_from: date
    employee_rate: Decimal
    employer_rate: Decimal
    pension_rate: Decimal
    #: EPS is capped on this wage however much the employer contributes on.
    pension_wage_cap: Decimal
    #: Employees' Deposit Linked Insurance. Employer-only, on wages capped at
    #: `edli_wage_cap` — so at most ~75/month per employee.
    edli_rate: Decimal
    edli_wage_cap: Decimal
    #: EPF administration charges (A/c 2). Employer-only.
    admin_rate: Decimal
    #: ...but with a floor per ESTABLISHMENT per month, not per employee. A
    #: small company owes this minimum however little 0.5% comes to, which is
    #: why it is settled at run level rather than on a payslip.
    admin_min_per_establishment: Decimal
    #: The date from which EPS is not payable to a first-time member earning
    #: above the ceiling. Their whole employer share goes to EPF instead.
    eps_new_member_cutoff: date
    eps_max_age: int


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
        edli_rate=Decimal("0.005"),
        edli_wage_cap=Decimal("15000"),
        admin_rate=Decimal("0.005"),
        admin_min_per_establishment=Decimal("500"),
        eps_new_member_cutoff=date(2014, 9, 1),
        eps_max_age=58,
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
    """The general definition. Kept for callers that genuinely have no statute
    in hand; anything computing money should use `definition_for`."""
    return _resolve([d for d in WAGE_DEFINITIONS if d.statute is None], period)


#: Most specific wins: a rule naming both the statute and the state beats one
#: naming either, which beats the general one. Specificity beats recency, so a
#: general definition notified later cannot silently take over a statute that
#: has its own — the same rule calendar assignment follows.
def _specificity(d: WageDefinition) -> int:
    return (1 if d.statute else 0) + (2 if d.jurisdiction else 0)


def pick_definition(
    catalogue: list[WageDefinition],
    *,
    statute: str,
    jurisdiction: str | None,
    period: date,
) -> WageDefinition:
    """The wage definition for one statute, in one jurisdiction, on one period.

    Pure, so the decision that sets everybody's PF basis is testable without a
    database or a clock.
    """
    matches = [
        d
        for d in catalogue
        if d.effective_from <= period
        and d.statute in (None, statute)
        and d.jurisdiction in (None, jurisdiction)
    ]
    if not matches:
        raise ValueError(
            f"no wage definition in force for {statute} in "
            f"{jurisdiction or 'the centre'} on {period.isoformat()}"
        )
    return max(matches, key=lambda d: (_specificity(d), d.effective_from))


def definition_for(
    statute: str, *, jurisdiction: str | None, period: date
) -> WageDefinition:
    return pick_definition(
        WAGE_DEFINITIONS, statute=statute, jurisdiction=jurisdiction, period=period
    )


def basis_for(
    lines: list[tuple[Decimal, str]],
    *,
    statute: str,
    jurisdiction: str | None,
    period: date,
    catalogue: list[WageDefinition] | None = None,
) -> "WageBasis":
    """The statutory wage for one statute. THE entry point for anything
    computing money — `statutory_wage` below is the arithmetic it uses."""
    definition = pick_definition(
        catalogue if catalogue is not None else WAGE_DEFINITIONS,
        statute=statute, jurisdiction=jurisdiction, period=period,
    )
    return statutory_wage(lines, definition)


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


# --- EPS eligibility --------------------------------------------------------


@dataclass(frozen=True)
class EpsDecision:
    """Whether the pension scheme applies, and WHY — the reason goes on the
    payslip, because "your employer's whole 12% went to EPF this month" is a
    question employees actually ask."""

    eligible: bool
    reason: str


def age_on(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def eps_decision(
    *,
    period: date,
    rule: EpfRule,
    pf_wage: Decimal,
    ceiling: Decimal,
    date_of_birth: date | None = None,
    pf_first_joined_on: date | None = None,
) -> EpsDecision:
    """EPS is NOT universal, and the engine must never assume an 8.33/3.67
    split.

    Two exclusions are modelled, both from EPFO's employer guidance:
      * a member who has attained 58 stops accruing pension
      * someone becoming an EPF member for the FIRST time on or after
        1 Sep 2014 while earning above the wage ceiling never joins EPS

    In both cases the employer's whole contribution goes to EPF. Because the
    residual EPF share is derived by subtraction, that falls out of setting
    pension to zero — no separate arithmetic.

    Unknown dates mean eligible: a company that hasn't captured dates of birth
    yet should keep contributing to EPS, not silently stop. Under-remitting to
    EPS is the more harmful error, and the payslip says the basis was assumed.
    """
    if date_of_birth is not None and age_on(date_of_birth, period) >= rule.eps_max_age:
        return EpsDecision(False, f"aged {rule.eps_max_age} or over")

    if (
        pf_first_joined_on is not None
        and pf_first_joined_on >= rule.eps_new_member_cutoff
        and pf_wage > ceiling
    ):
        return EpsDecision(
            False,
            f"first became an EPF member on or after "
            f"{rule.eps_new_member_cutoff.isoformat()} above the wage ceiling",
        )

    if date_of_birth is None or pf_first_joined_on is None:
        return EpsDecision(True, "eligible (date of birth or EPF joining date not on record)")
    return EpsDecision(True, "eligible")
