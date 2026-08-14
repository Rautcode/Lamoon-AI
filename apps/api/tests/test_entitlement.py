"""Leave entitlement arithmetic.

`LeaveType.annual_quota` carried the comment "days/year, same for every
employee in V1", and that was the whole problem. No real company gives the same
leave to a probationer and a ten-year employee, or to a factory and a head
office.

These are pure functions of dates and numbers, tested without a database,
because entitlement is where an off-by-one costs somebody a day of their life.
"""
from datetime import date
from decimal import Decimal as D

from app.modules.leave.entitlement import (
    completed_months,
    employment_window,
    entitlement,
    quantize,
)


def earned(annual="12", method="annual", year=2026, **kw):
    return entitlement(annual_days=D(annual), method=method, year=year, **kw)


# --- completed months: the rule that decides a joiner's first year ----------


def test_a_month_counts_once_it_has_been_served():
    """1 July has served July by 31 July. 31 July has not served anything."""
    assert completed_months(date(2026, 7, 1), date(2026, 7, 31)) == 1
    assert completed_months(date(2026, 7, 31), date(2026, 7, 31)) == 1
    assert completed_months(date(2026, 7, 1), date(2026, 7, 30)) == 1


def test_a_full_year_is_twelve_months():
    assert completed_months(date(2026, 1, 1), date(2026, 12, 31)) == 12


def test_a_late_joiner_does_not_get_a_month_for_one_day():
    """Counting calendar months instead of served months would hand somebody a
    month of entitlement for turning up on the 31st."""
    served = completed_months(date(2026, 7, 20), date(2026, 8, 10))
    assert served == 1, "20 Jul to 10 Aug is one completed month, not two"


def test_an_end_before_a_start_is_zero_not_negative():
    assert completed_months(date(2026, 8, 1), date(2026, 7, 1)) == 0


# --- the employment window ---------------------------------------------------


def test_a_full_year_employee_gets_the_whole_window():
    w = employment_window(2026, joined_on=date(2020, 1, 1), exited_on=None)
    assert (w.start, w.end, w.months) == (date(2026, 1, 1), date(2026, 12, 31), 12)


def test_joining_mid_year_starts_the_window_at_joining():
    w = employment_window(2026, joined_on=date(2026, 7, 1), exited_on=None)
    assert w.start == date(2026, 7, 1) and w.months == 6


def test_leaving_mid_year_ends_the_window_at_exit():
    w = employment_window(2026, joined_on=None, exited_on=date(2026, 6, 30))
    assert w.end == date(2026, 6, 30) and w.months == 6


def test_somebody_who_left_before_the_year_earns_nothing():
    w = employment_window(2026, joined_on=None, exited_on=date(2025, 6, 30))
    assert w.months == 0


# --- annual accrual ----------------------------------------------------------


def test_a_full_year_gives_the_exact_quota():
    """No rounding is applied to the ordinary case. 12 must be 12, not 12.0
    after a divide-and-multiply that could drift."""
    assert earned("12", joined_on=date(2020, 1, 1)) == D("12")


def test_half_a_year_gives_half_the_quota():
    assert earned("12", joined_on=date(2026, 7, 1)) == D("6")


def test_exit_prorates_the_same_way_joining_does():
    """The mirror. Entitlement stops accruing at the last working day, and the
    difference against leave already taken is what F&F settles."""
    assert earned("12", exited_on=date(2026, 6, 30)) == D("6")


def test_joining_and_leaving_in_the_same_year_compounds():
    assert earned("12", joined_on=date(2026, 4, 1), exited_on=date(2026, 9, 30)) == D("6")


# --- monthly accrual ---------------------------------------------------------


def test_monthly_accrual_earns_as_the_year_passes():
    """Asking in March must not hand over December's days."""
    assert earned("12", method="monthly", as_of=date(2026, 3, 31)) == D("3")
    assert earned("12", method="monthly", as_of=date(2026, 12, 31)) == D("12")


def test_monthly_accrual_for_a_joiner_counts_from_joining():
    assert earned(
        "12", method="monthly", joined_on=date(2026, 7, 1), as_of=date(2026, 9, 30)
    ) == D("3")


def test_annual_accrual_ignores_as_of():
    """"The whole year up front" is precisely what annual means. A company that
    wants otherwise is asking for monthly."""
    assert earned("12", method="annual", as_of=date(2026, 1, 15)) == D("12")


# --- rounding ----------------------------------------------------------------


def test_entitlement_rounds_to_half_days():
    """Half-days are the smallest unit anybody grants. Whole-day rounding would
    silently take half a day off people on monthly accrual; two decimals would
    print 1.67 on a payslip, which nobody can act on."""
    assert quantize(D("1.67")) == D("1.5")
    assert quantize(D("1.75")) == D("2.0")
    assert quantize(D("1.24")) == D("1.0")


def test_an_awkward_quota_still_lands_on_a_half():
    # 20 days, five months served = 8.333... → 8.5
    assert earned("20", joined_on=date(2026, 8, 1)) == D("8.5")


# --- degenerate cases --------------------------------------------------------


def test_a_zero_quota_earns_nothing_rather_than_dividing():
    assert earned("0", joined_on=date(2026, 1, 1)) == D("0")


def test_somebody_joining_after_the_year_ends_earns_nothing():
    assert earned("12", joined_on=date(2027, 1, 1)) == D("0")


# --- which policy applies to whom -------------------------------------------
#
# Same shape as calendar resolution, deliberately. Two different answers to
# "which rule applies to whom" would be two different sets of bugs.

import uuid  # noqa: E402

from app.modules.leave.policy import Candidate, pick_policy  # noqa: E402

EST = uuid.uuid4()
DEPT = uuid.uuid4()
ON = date(2026, 8, 15)


def cand(scope_type, *, scope_id=None, scope_value=None, days="12",
         frm="2020-01-01", to=None):
    return Candidate(
        scope_type=scope_type, scope_id=scope_id, scope_value=scope_value,
        annual_days=days, accrual_method="annual",
        prorate_on_joining=True, prorate_on_exit=True,
        effective_from=date.fromisoformat(frm),
        effective_to=date.fromisoformat(to) if to else None,
    )


def pick(candidates, *, est=EST, dept=DEPT, worker="white_collar", on=ON):
    return pick_policy(
        candidates, establishment_id=est, department_id=dept,
        worker_type=worker, on=on,
    )


def test_company_policy_applies_when_nothing_more_specific():
    got = pick([cand("company", days="12")])
    assert got.annual_days == "12" and got.source == "company"


def test_department_beats_establishment_beats_company():
    got = pick([
        cand("company", days="12"),
        cand("establishment", scope_id=EST, days="15"),
        cand("department", scope_id=DEPT, days="18"),
    ])
    assert got.annual_days == "18" and got.source == "department"


def test_worker_type_is_the_most_specific_thing_we_can_express_today():
    """Blue-collar leave is a different animal, and worker_type exists while
    grade does not."""
    got = pick(
        [cand("company", days="12"), cand("worker_type", scope_value="blue_collar", days="21")],
        worker="blue_collar",
    )
    assert got.annual_days == "21" and got.source == "worker_type"


def test_a_scope_that_does_not_match_is_inherited_past():
    """Somebody in another establishment falls through to company, not to
    nothing."""
    got = pick(
        [cand("company", days="12"), cand("establishment", scope_id=uuid.uuid4(), days="15")]
    )
    assert got.annual_days == "12"


def test_specificity_beats_recency():
    """A company-wide policy written in July must not take over a department
    that has had its own since 2020."""
    got = pick([
        cand("department", scope_id=DEPT, days="18", frm="2020-01-01"),
        cand("company", days="25", frm="2026-07-01"),
    ])
    assert got.annual_days == "18"


def test_policies_are_effective_dated():
    got = pick([
        cand("company", days="12", frm="2020-01-01", to="2026-03-31"),
        cand("company", days="15", frm="2026-04-01"),
    ])
    assert got.annual_days == "15"
    assert pick([
        cand("company", days="12", frm="2020-01-01", to="2026-03-31"),
        cand("company", days="15", frm="2026-04-01"),
    ], on=date(2026, 2, 1)).annual_days == "12"


def test_no_policy_returns_none_so_the_caller_can_fall_back():
    """The difference between "no policy" and "no leave". Granting zero days
    here would quietly cancel everybody's holiday."""
    assert pick([cand("company", frm="2027-01-01")]) is None
