"""One wage basis per statute, per jurisdiction, per period.

The engine had ONE definition of "wages" for everything, gated by a single
global date — `LABOUR_CODE_START = 21 November 2025`. That is wrong in
principle, not merely imprecise.

Only certain provisions of the Codes were notified on that date, with Central
Rules following and **state rules following separately**. So a statute can
adopt the revised basis on a different date from its neighbour, and a state can
differ from the centre. A single constant cannot express any of that, and the
failure mode is silent: PF computed on a basis that was not yet in force, for
months, before anybody reconciles a challan.

The fix is not another date. It is asking the right question:

    what is the wage basis for THIS statute, in THIS jurisdiction, in THIS
    period?

Deliberately still narrow: resolution and effective dating. Not a rules
database — that is D-1.2, which absorbs every statutory parameter at once.
"""
from datetime import date
from decimal import Decimal as D

import pytest

from app.modules.payroll import rules

CENTRAL = None  # no state override


def lines(basic="20000", hra="12000", special="28000"):
    """A package where the excluded share is well over half, so the 50% test
    bites and the two bases are visibly different."""
    return [(D(basic), "wages"), (D(hra), "excluded"), (D(special), "excluded")]


# --- resolution ---------------------------------------------------------------


def test_a_statute_resolves_its_own_definition():
    epf = rules.definition_for("epf", jurisdiction=CENTRAL, period=date(2026, 8, 1))
    esi = rules.definition_for("esi", jurisdiction=CENTRAL, period=date(2026, 8, 1))
    assert epf.version and esi.version


def test_an_unknown_statute_falls_back_to_the_general_definition():
    """A statute nobody has written a rule for still needs an answer, and the
    general Code definition is the right one — not an exception."""
    got = rules.definition_for("gratuity", jurisdiction=CENTRAL, period=date(2026, 8, 1))
    assert got is not None


def test_a_statute_specific_rule_beats_the_general_one():
    """The whole point. If EPF adopts the revised basis and ESI has not yet,
    the engine must be able to say so."""
    specific = rules.WageDefinition(
        version="epf-test-old-basis", effective_from=date(2020, 1, 1),
        excluded_share_cap=None, statute="epf", jurisdiction=None,
    )
    general = rules.WageDefinition(
        version="general-new-basis", effective_from=date(2025, 11, 21),
        excluded_share_cap=D("0.5"), statute=None, jurisdiction=None,
    )
    picked = rules.pick_definition(
        [general, specific], statute="epf", jurisdiction=None, period=date(2026, 8, 1)
    )
    assert picked.version == "epf-test-old-basis"
    assert picked.excluded_share_cap is None


def test_a_jurisdiction_rule_beats_a_central_one():
    """State rules follow the Centre separately. A state that has not notified
    must not be computed as though it had."""
    central = rules.WageDefinition(
        version="central", effective_from=date(2025, 11, 21),
        excluded_share_cap=D("0.5"), statute=None, jurisdiction=None,
    )
    state = rules.WageDefinition(
        version="mh-not-yet", effective_from=date(2020, 1, 1),
        excluded_share_cap=None, statute=None, jurisdiction="MH",
    )
    assert rules.pick_definition(
        [central, state], statute="epf", jurisdiction="MH", period=date(2026, 8, 1)
    ).version == "mh-not-yet"
    assert rules.pick_definition(
        [central, state], statute="epf", jurisdiction="KA", period=date(2026, 8, 1)
    ).version == "central"


def test_specificity_beats_recency():
    """A general definition notified later must not silently take over a
    statute that has its own — the same rule calendar resolution follows."""
    general = rules.WageDefinition(
        version="general-2026", effective_from=date(2026, 7, 1),
        excluded_share_cap=D("0.5"), statute=None, jurisdiction=None,
    )
    specific = rules.WageDefinition(
        version="epf-2020", effective_from=date(2020, 1, 1),
        excluded_share_cap=None, statute="epf", jurisdiction=None,
    )
    assert rules.pick_definition(
        [general, specific], statute="epf", jurisdiction=None, period=date(2026, 8, 1)
    ).version == "epf-2020"


def test_resolution_is_by_period_never_by_today():
    """Re-running August 2026 in 2028 must use August 2026's basis."""
    before = rules.definition_for("epf", jurisdiction=CENTRAL, period=date(2025, 6, 1))
    after = rules.definition_for("epf", jurisdiction=CENTRAL, period=date(2026, 6, 1))
    assert before.excluded_share_cap is None, "pre-Code: nominated components only"
    assert after.excluded_share_cap == D("0.5"), "post-Code: the 50% test applies"


def test_a_period_before_every_rule_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="no wage definition"):
        rules.pick_definition([], statute="epf", jurisdiction=None, period=date(2026, 8, 1))


# --- the basis it produces ----------------------------------------------------


def test_two_statutes_can_produce_different_wages_for_one_person():
    """The reason this task exists. Same salary, same month, two bases."""
    old = rules.WageDefinition(
        version="esi-old", effective_from=date(2020, 1, 1),
        excluded_share_cap=None, statute="esi", jurisdiction=None,
    )
    new = rules.WageDefinition(
        version="epf-new", effective_from=date(2025, 11, 21),
        excluded_share_cap=D("0.5"), statute="epf", jurisdiction=None,
    )
    catalogue = [old, new]

    epf = rules.basis_for(lines(), statute="epf", jurisdiction=None,
                          period=date(2026, 8, 1), catalogue=catalogue)
    esi = rules.basis_for(lines(), statute="esi", jurisdiction=None,
                          period=date(2026, 8, 1), catalogue=catalogue)

    assert esi.statutory_wage == D("20000"), "nominated components only"
    assert epf.statutory_wage == D("30000"), "half of 60,000 remuneration"
    assert epf.statutory_wage != esi.statutory_wage


def test_the_basis_records_which_definition_produced_it():
    """A payslip that cannot say which rule it used cannot be defended."""
    got = rules.basis_for(lines(), statute="epf", jurisdiction=None,
                          period=date(2026, 8, 1))
    assert got.version
    assert got.added_back >= 0


def test_the_default_catalogue_still_matches_the_old_behaviour():
    """No existing payslip may move. The general definitions carry the same
    dates and caps they always had."""
    period = date(2026, 8, 1)
    old_way = rules.statutory_wage(lines(), rules.wage_definition_for(period))
    new_way = rules.basis_for(lines(), statute="epf", jurisdiction=None, period=period)
    assert old_way.statutory_wage == new_way.statutory_wage
    assert old_way.added_back == new_way.added_back
