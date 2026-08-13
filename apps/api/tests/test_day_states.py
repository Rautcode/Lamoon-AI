"""The day-state vocabulary.

One question — "what happened on this day" — with one answer, shared by
presence, the heatmap and (next) the payroll bridge that turns days into work
facts.

The distinction these tests exist to protect: an empty day is not one thing.
A Sunday, Diwali, approved leave, and somebody who simply didn't turn up are
four different facts, and only the last is a candidate for loss of pay.
Reporting all four as "absent" is what let this product tell an HR team their
entire company failed to show up on a public holiday.
"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.modules.attendance.service import DAY_STATES, DaySummary, Punch, day_state, pair_events

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 8, 10)  # a Monday


def ist(h, m=0):
    return datetime(2026, 8, 10, h, m, tzinfo=IST)


def worked(punches, *, now=None):
    return pair_events(
        punches, day=DAY, tz=IST, workday_start=time(9, 30),
        expected_minutes=480, grace_minutes=15, now=now,
    )


def empty(**kw):
    """A day with no punches at all — the case that was wrong."""
    s = DaySummary(day=DAY)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# --- the four kinds of empty day --------------------------------------------


def test_holiday_is_not_absent():
    assert day_state(empty(holiday="Diwali", working_day=False)) == "holiday"


def test_weekly_off_is_not_absent():
    assert day_state(empty(working_day=False)) == "weekly_off"


def test_approved_paid_leave_is_not_absent():
    assert day_state(empty(), leave="paid_leave") == "paid_leave"


def test_unpaid_leave_is_its_own_state():
    """Unpaid leave IS loss of pay, but it is EXPLAINED loss of pay. Payroll
    treats it differently from an unexplained gap, which needs a human."""
    assert day_state(empty(), leave="unpaid_leave") == "unpaid_leave"


def test_nothing_at_all_is_absent():
    assert day_state(empty()) == "absent"


# --- precedence -------------------------------------------------------------


def test_holiday_beats_leave():
    """Nobody spends annual leave on Diwali. If both are recorded the holiday
    is the true fact, and charging the person a leave day would be theft."""
    assert day_state(empty(holiday="Diwali", working_day=False), leave="paid_leave") == "holiday"


def test_holiday_beats_punches():
    """Working ON a holiday is still a holiday for calendar purposes — the
    premium for having worked it is a payroll rule, not a day state."""
    s = worked([Punch("in", ist(10)), Punch("out", ist(18))])
    s.holiday = "Diwali"
    assert day_state(s) == "holiday"


def test_punches_beat_leave():
    """Somebody who cancelled their leave and came in was at work. The punch
    is evidence; the leave record is a stale intention."""
    s = worked([Punch("in", ist(9, 30)), Punch("out", ist(18))])
    assert day_state(s, leave="paid_leave") == "present"


# --- the distinction that protects people's pay -----------------------------


def test_missing_punch_is_not_absent():
    """Punched in, never out, and the day is over. Somebody worked and the
    record is incomplete. Treating this as absence docks a day's pay for a
    failed biometric reader."""
    s = worked([Punch("in", ist(9, 30))], now=ist(23, 59))
    assert day_state(s, today=date(2026, 8, 11)) == "missing_punch"


def test_still_on_the_clock_is_present_not_missing_punch():
    """Mid-afternoon with no punch-out yet is normal, not a data problem —
    the same summary, read on the day itself rather than afterwards."""
    s = worked([Punch("in", ist(9, 30))], now=ist(14))
    assert s.open is True
    assert day_state(s, today=DAY) == "present"


def test_every_state_is_in_the_vocabulary():
    """Nothing may invent a state the rest of the product doesn't know."""
    cases = [
        day_state(empty(holiday="X", working_day=False)),
        day_state(empty(working_day=False)),
        day_state(empty(), leave="paid_leave"),
        day_state(empty(), leave="unpaid_leave"),
        day_state(empty()),
        day_state(worked([Punch("in", ist(9, 30)), Punch("out", ist(18))])),
        day_state(worked([Punch("in", ist(9, 30))], now=ist(23, 59)),
                  today=date(2026, 8, 11)),
    ]
    assert set(cases) <= set(DAY_STATES)
