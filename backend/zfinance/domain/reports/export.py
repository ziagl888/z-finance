"""CSV export of a month report, meant for a program or a tax advisor (UC-ZF-007 assumption).

Machine-readable on purpose: ``,`` delimiter, ``.`` as decimal separator, amounts exact to the
cent (``-12.30``), UTF-8 without a byte-order mark, English header. One row per report line
(``income`` / ``expense``), then the totals and the movements. Text cells that a spreadsheet
could read as a formula (leading ``=``, ``+``, ``-``, ``@``, tab or carriage return) get a
leading ``'``; amounts are never altered.

Columns: ``month, area, row_type, category_id, category, parent_category, amount,
previous_month, average_6_months, bookings``. ``category_id`` is empty for the uncategorised line
and for totals; ``previous_month`` / ``average_6_months`` are empty for the movement rows.
"""

from __future__ import annotations

import csv
import io

from zfinance.domain.reports.service import Comparison, MonthReport
from zfinance.money import from_cents

HEADER = (
    "month",
    "area",
    "row_type",
    "category_id",
    "category",
    "parent_category",
    "amount",
    "previous_month",
    "average_6_months",
    "bookings",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value: str | None) -> str:
    """A text cell that no spreadsheet reads as a formula."""
    text = value or ""
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _amount(cents: int) -> str:
    return str(from_cents(cents))


def month_csv(report: MonthReport) -> str:
    """The report as CSV text (CRLF line ends, as csv.writer produces by default)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER)

    def write(
        row_type: str,
        comparison: Comparison | None,
        *,
        category_id: int | None = None,
        category: str | None = None,
        parent: str | None = None,
        bookings: int | None = None,
        compare: bool = True,
    ) -> None:
        amount = comparison.amount_cents if comparison else 0
        writer.writerow(
            [
                report.month,
                report.area,
                row_type,
                "" if category_id is None else category_id,
                safe_text(category),
                safe_text(parent),
                _amount(amount),
                _amount(comparison.previous_cents) if comparison and compare else "",
                _amount(comparison.average_6m_cents) if comparison and compare else "",
                "" if bookings is None else bookings,
            ]
        )

    for line in report.lines:
        write(
            line.flow,
            line.comparison,
            category_id=line.category_id,
            category=line.category_name,
            parent=line.parent_name,
            bookings=line.booking_count,
        )
    write("total_income", report.income, bookings=sum(x.booking_count for x in report.lines if x.flow == "income"))
    write("total_expense", report.expense, bookings=sum(x.booking_count for x in report.lines if x.flow == "expense"))
    write("balance", report.balance)
    write("movement_in", Comparison(report.movement_in_cents, 0, 0), compare=False)
    write("movement_out", Comparison(report.movement_out_cents, 0, 0), compare=False)
    return out.getvalue()
