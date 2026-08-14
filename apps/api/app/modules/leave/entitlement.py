"""How much leave somebody is actually entitled to.

`LeaveType.annual_quota` said "days/year, same for every employee in V1", and
that comment was the whole problem: no real company gives the same leave to a
probationer and a ten-year employee, or to a factory and a head office.

Everything here is a pure function of dates and numbers — no session, no clock,
no database — because entitlement arithmetic is where an off-by-one costs
somebody a day of their life, and it should be testable without a tenant.

Two rules that look like details and are not:

  JOINING   somebody who joins in July is not owed a full year. Proration is by
            completed months of SERVICE, not by calendar position, so a 1 July
            joiner and a 31 July joiner do not get the same answer.
  EXIT      the mirror. Entitlement stops accruing at the last working day, and
            the difference against leave already taken is what F&F settles.
"""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

#: Entitlement is carried as Decimal, not float and not int. Half-day leave is
#: coming (C4b), monthly accrual produces thirds, and a leave balance is a
#: number people argue about — it must round the same way every time.
ZERO = Decimal("0")

#: annual  — the whole year's quota is available from day one (or from joining)
#: monthly — it accrues, 1/12 per completed month, which is what most Indian
#:           SMEs actually operate and what makes a mid-year exit computable
ACCRUAL_METHODS = ("annual", "monthly")


def quantize(value: Decimal) -> Decimal:
    """Half-days are the smallest unit anybody grants, so round to 0.5.

    Rounding to whole days would silently take half a day off people on monthly
    accrual; rounding to two decimals would show 1.67 days on a payslip, which
    nobody can act on. 0.5 is the unit the policy is actually written in.
    """
    return (value * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2


@dataclass(frozen=True)
class Window:
    """The part of a leave year somebody was actually employed for."""

    start: date
    end: date
    months: int  # completed months of service inside the year


def leave_year(year: int) -> tuple[date, date]:
    """Calendar year. ponytail: an April–March fiscal leave year is common in
    India and is a policy field this does not have yet — when it arrives, it
    changes only this function and its callers keep working."""
    return date(year, 1, 1), date(year, 12, 31)


def completed_months(start: date, end: date) -> int:
    """Whole months between two dates, inclusive of the starting month only
    once it has been served.

    A joiner on 1 July has served July by 31 July; a joiner on 31 July has not.
    Counting calendar months instead would hand somebody a month of entitlement
    for one day of work.
    """
    if end < start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months + 1) if months >= 0 else 0


def employment_window(
    year: int, *, joined_on: date | None, exited_on: date | None
) -> Window:
    """The slice of the leave year this person was employed for."""
    year_start, year_end = leave_year(year)
    start = max(year_start, joined_on) if joined_on else year_start
    end = min(year_end, exited_on) if exited_on else year_end
    if end < start:
        return Window(start=start, end=start, months=0)
    return Window(start=start, end=end, months=completed_months(start, end))


def entitlement(
    *,
    annual_days: Decimal,
    method: str,
    year: int,
    joined_on: date | None = None,
    exited_on: date | None = None,
    as_of: date | None = None,
) -> Decimal:
    """Days earned in this leave year, for this person, by this policy.

    `as_of` bounds MONTHLY accrual to what has actually been earned — asking in
    March must not hand over December's days. It does not bound annual accrual,
    because "the whole year up front" is precisely what annual means; a company
    that wants otherwise is asking for monthly.
    """
    if annual_days <= ZERO:
        return ZERO
    window = employment_window(year, joined_on=joined_on, exited_on=exited_on)
    if window.months == 0:
        return ZERO

    if method == "monthly":
        earned_end = min(window.end, as_of) if as_of else window.end
        months = completed_months(window.start, earned_end)
        return quantize(annual_days * Decimal(months) / Decimal(12))

    # Annual, prorated by the share of the year actually employed. A full year
    # gives the full quota exactly, with no rounding applied at all.
    if window.months >= 12:
        return quantize(annual_days)
    return quantize(annual_days * Decimal(window.months) / Decimal(12))
