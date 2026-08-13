"""Rendering a run into a file somebody can open.

Pure functions over rows already loaded — no session, no storage, no clock.
That keeps them testable without a database and means `ArtifactService` can
call them from a request today and a Celery task tomorrow without change.

CSV first because it is the format an Indian payroll team actually forwards to
their CA, it streams, and it needs no dependency. XLSX and PDF land when
somebody needs formatting rather than data.
"""
import csv
import io
from decimal import Decimal

from app.modules.payroll.models import Payslip

#: Deductions are pulled out of the payslip breakdown by code, so the register
#: has a stable column set even though the breakdown is JSONB and varies by
#: what applied to each person.
#:
#: These codes MUST match the ones service.compute_payslip writes. They are
#: not a naming choice here — a mismatch does not fail, it silently prints
#: 0.00 in a column a chartered accountant reconciles against a challan. This
#: shipped wrong once with "PF" instead of "EPF"; test_register_uses_the_codes
#: _the_engine_actually_writes exists so it cannot happen again quietly.
DEDUCTION_CODES = (
    ("EPF", "PF"),
    ("ESI", "ESI"),
    ("PT", "Professional tax"),
    ("TDS", "TDS"),
)


def _line(payslip: Payslip, code: str) -> str:
    """One deduction, or "0.00" when the scheme did not apply to this person.

    Absent and zero really are the same here — somebody outside ESI and
    somebody inside it earning nothing both owe nothing — so a blank would
    only make the column harder to sum.
    """
    for line in payslip.breakdown.get("deductions", []):
        if line.get("code") == code:
            # The engine writes str(Decimal), so an exact zero arrives as "0"
            # rather than "0.00". Normalise, or a column of money has two
            # different spellings of nothing in it.
            return f"{Decimal(str(line.get('amount', '0'))):.2f}"
    return "0.00"


def payroll_register(payslips: list[Payslip], *, period_label: str) -> tuple[bytes, str, str]:
    """One row per person, the columns a payroll reviewer checks in order.

    Money is written as the stored decimal STRING. Formatting it with rupee
    symbols or thousands separators would make the file useless in a
    spreadsheet, which is the only place it is going.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "Employee", "Period", "Working days", "Paid days", "Unpaid days",
        "Gross", *[label for _, label in DEDUCTION_CODES],
        "Total deductions", "Net", "Employer cost",
    ])

    totals = {"gross": Decimal("0.00"), "deductions": Decimal("0.00"),
              "net": Decimal("0.00"), "employer": Decimal("0.00")}
    for p in sorted(payslips, key=lambda x: x.employee_name):
        writer.writerow([
            p.employee_name, period_label, p.working_days, p.paid_days, p.lop_days,
            str(p.gross), *[_line(p, code) for code, _ in DEDUCTION_CODES],
            str(p.deductions), str(p.net), str(p.employer_cost),
        ])
        totals["gross"] += p.gross
        totals["deductions"] += p.deductions
        totals["net"] += p.net
        totals["employer"] += p.employer_cost

    # A total row, because the first thing anybody does with a register is
    # check it against the bank transfer.
    writer.writerow([])
    writer.writerow([
        f"{len(payslips)} employees", period_label, "", "", "",
        str(totals["gross"]), *["" for _ in DEDUCTION_CODES],
        str(totals["deductions"]), str(totals["net"]), str(totals["employer"]),
    ])

    # utf-8-sig: Excel on Windows reads a plain UTF-8 CSV as the ANSI codepage
    # and mangles every non-ASCII name in the file.
    return buffer.getvalue().encode("utf-8-sig"), "text/csv", "csv"
