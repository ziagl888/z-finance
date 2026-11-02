"""Monthly report (UC-ZF-007): what came in and went out of one month, by category and area.

Everything reads the ``transaction`` table; nothing is stored. Rules of the report:

* Only ``booked`` rows count — in the figures, the comparison values, the uncategorised list and
  the drilldown. ``planned`` and ``skipped`` rows never do.
* The area of a booking is the area of its account. ``all`` is every account.
* A transfer leg (``transfer_group_id`` set) is neither income nor expense and never
  "uncategorised". A leg is a *movement* of an area when the other leg's account lies outside
  that area (owner draw/contribution seen from one side); in ``all`` nothing is a movement.
  Movements are reported next to the balance, not inside it.
* A line is (flow, category): ``income`` rows have a positive amount, ``expense`` rows a negative
  one, so a refund on an expense category is an income line of that category and every line's
  drilldown sums exactly to its amount. Each line carries the same line one month earlier and the
  average of the six months before the selected month (divisor always 6, rounded half away from
  zero); totals are computed from the raw sums, so they can differ by a cent from the sum of the
  rounded line averages.

Invariants: every function reads ``booked`` rows only; ``income + expense == balance`` and each
``bookings`` list sums to its line's amount.

Errors are ``ReportsError`` carrying an ``N_()`` msgid; the API translates it per request.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, get_args

from zfinance.domain.accounts import service as accounts
from zfinance.i18n import N_

Area = Literal["private", "business", "all"]
Flow = Literal["income", "expense"]

AREAS: tuple[Area, ...] = get_args(Area)
#: Months before the selected one that make up the average.
AVERAGE_MONTHS = 6


class ReportsError(Exception):
    """A request the report cannot answer (400); ``msgid`` is an ``N_()`` catalog id."""

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Comparison:
    """An amount with the same figure one month earlier and the six-month average before the month."""

    amount_cents: int
    previous_cents: int
    average_6m_cents: int


@dataclass(frozen=True)
class Line:
    """Income or expense of one category (or of no category) in the month."""

    flow: Flow
    category_id: int | None
    category_name: str | None
    parent_name: str | None
    comparison: Comparison
    booking_count: int


@dataclass(frozen=True)
class MonthReport:
    """The report of one month and area; ``month`` is ``YYYY-MM``.

    Amounts are signed cents (expenses negative). ``movement_in_cents`` / ``movement_out_cents``
    are the cross-area transfers of the month (owner draw/contribution), not part of the balance.
    """

    month: str
    area: Area
    income: Comparison
    expense: Comparison
    balance: Comparison
    movement_in_cents: int
    movement_out_cents: int
    uncategorized_count: int
    lines: list[Line]


@dataclass(frozen=True)
class Booking:
    """A booking behind a line or in the uncategorised list; carries what an edit needs."""

    id: int
    date: date
    amount_cents: int
    account_id: int
    account_name: str
    category_id: int | None
    text: str
    counterparty: str | None
    memo: str | None


# --- months ------------------------------------------------------------------------------------


def parse_month(month: str) -> tuple[int, int]:
    """``"2026-09"`` → ``(2026, 9)``; anything else raises ``ReportsError``."""
    try:
        year_text, month_text = month.split("-")
        year, number = int(year_text), int(month_text)
        if len(year_text) != 4 or len(month_text) != 2 or not 1 <= number <= 12 or year < 1:
            raise ValueError(month)
    except ValueError as exc:
        raise ReportsError(N_("Enter the month as YYYY-MM.")) from exc
    return year, number


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _first_day(year: int, month: int) -> str:
    return f"{_key(year, month)}-01"


def _div_round(total: int, divisor: int) -> int:
    """Integer division rounded half away from zero (-3 / 6 -> -1, 3 / 6 -> 1)."""
    quotient, remainder = divmod(abs(total), divisor)
    if remainder * 2 >= divisor:
        quotient += 1
    return quotient if total >= 0 else -quotient


class _Window:
    """The selected month, the month before and the six months before it, as ``YYYY-MM`` keys."""

    def __init__(self, month: str) -> None:
        self.year, self.number = parse_month(month)
        self.current = month
        self.previous = _key(*_shift(self.year, self.number, -1))
        self.average = [_key(*_shift(self.year, self.number, -offset)) for offset in range(1, AVERAGE_MONTHS + 1)]
        # Bookings from the first day of the oldest average month up to the end of the selected month.
        self.start = _first_day(*_shift(self.year, self.number, -AVERAGE_MONTHS))
        self.end = _first_day(*_shift(self.year, self.number, 1))  # exclusive
        self.month_start = _first_day(self.year, self.number)

    def comparison(self, monthly: dict[str, int]) -> Comparison:
        """Amount, previous month and average from per-month sums (a missing month is 0)."""
        average = _div_round(sum(monthly.get(key, 0) for key in self.average), AVERAGE_MONTHS)
        return Comparison(monthly.get(self.current, 0), monthly.get(self.previous, 0), average)


# --- the report --------------------------------------------------------------------------------

# Booked, non-transfer rows of an area ("all" or one area); the two "?" are the area, twice.
_SCOPE = "(? = 'all' OR a.area = ?)"
_COUNTED = "t.status = 'booked' AND t.transfer_group_id IS NULL"

# Sums per calendar month, flow and category over the whole comparison window.
_SUMS_SQL = (
    "SELECT substr(t.date, 1, 7) AS ym,"  # noqa: S608 — every part is a module constant
    " CASE WHEN t.amount_cents > 0 THEN 'income' ELSE 'expense' END AS flow,"
    ' t.category_id AS category_id, sum(t.amount_cents) AS total, count(*) AS n FROM "transaction" t'
    " JOIN account a ON a.id = t.account_id"
    f" WHERE {_COUNTED} AND t.date >= ? AND t.date < ? AND {_SCOPE} GROUP BY ym, flow, t.category_id"  # nosec B608
)
_UNCATEGORIZED_COUNT_SQL = (
    'SELECT count(*) FROM "transaction" t'  # noqa: S608 — every part is a module constant
    " JOIN account a ON a.id = t.account_id"
    f" WHERE {_COUNTED} AND t.category_id IS NULL AND t.date >= ? AND t.date < ? AND {_SCOPE}"  # nosec B608
)
# The bookings behind a line; the flow narrows by the sign of the amount.
_FLOW_SIGN = {"income": " AND t.amount_cents > 0", "expense": " AND t.amount_cents < 0", None: ""}
_BOOKINGS_SQL = {
    flow: (
        "SELECT t.id, t.date, t.amount_cents, t.account_id,"  # noqa: S608 — every part is a module constant
        " a.name AS account_name, t.category_id, t.text,"
        ' t.counterparty, t.memo FROM "transaction" t JOIN account a ON a.id = t.account_id'
        f" WHERE {_COUNTED} AND t.category_id IS ? AND t.date >= ? AND t.date < ? AND {_SCOPE}{sign}"
        " ORDER BY t.date, t.id"  # nosec B608
    )
    for flow, sign in _FLOW_SIGN.items()
}


def month_report(conn: sqlite3.Connection, month: str, area: Area) -> MonthReport:
    """Income, expense and balance of ``month`` in ``area`` with a line per category and flow."""
    window = _Window(month)
    rows = conn.execute(_SUMS_SQL, (window.start, window.end, area, area))
    per_line: dict[tuple[Flow, int | None], dict[str, int]] = {}
    per_flow: dict[Flow, dict[str, int]] = {"income": {}, "expense": {}}
    balance: dict[str, int] = {}
    counts: dict[tuple[Flow, int | None], int] = {}
    for row in rows:
        flow: Flow = row["flow"]
        line_key = (flow, row["category_id"])
        ym, total = row["ym"], row["total"]
        per_line.setdefault(line_key, {})[ym] = total
        per_flow[flow][ym] = per_flow[flow].get(ym, 0) + total
        balance[ym] = balance.get(ym, 0) + total
        if ym == window.current:
            counts[line_key] = row["n"]

    categories = {c.id: c for c in accounts.list_categories(conn)}

    def line(line_key: tuple[Flow, int | None], monthly: dict[str, int]) -> Line:
        flow, category_id = line_key
        category = categories.get(category_id) if category_id is not None else None
        parent = categories.get(category.parent_id) if category and category.parent_id is not None else None
        return Line(
            flow,
            category_id,
            category.name if category else None,
            parent.name if parent else None,
            window.comparison(monthly),
            counts.get(line_key, 0),
        )

    lines = [line(line_key, monthly) for line_key, monthly in per_line.items()]
    # Income first, largest first; expenses largest (most negative) first; uncategorised last within a flow.
    lines.sort(
        key=lambda item: (
            item.flow != "income",
            item.category_id is None,
            -abs(item.comparison.amount_cents),
            (item.category_name or "").casefold(),
        )
    )
    movement_in, movement_out = _movements(conn, window, area)
    return MonthReport(
        month=month,
        area=area,
        income=window.comparison(per_flow["income"]),
        expense=window.comparison(per_flow["expense"]),
        balance=window.comparison(balance),
        movement_in_cents=movement_in,
        movement_out_cents=movement_out,
        uncategorized_count=_uncategorized_count(conn, window, area),
        lines=lines,
    )


def _movements(conn: sqlite3.Connection, window: _Window, area: Area) -> tuple[int, int]:
    """(in, out) of the month's transfer legs on ``area`` accounts whose other leg is in another area."""
    if area == "all":
        return 0, 0
    row = conn.execute(
        "SELECT coalesce(sum(CASE WHEN t.amount_cents > 0 THEN t.amount_cents END), 0),"
        " coalesce(sum(CASE WHEN t.amount_cents < 0 THEN t.amount_cents END), 0)"
        ' FROM "transaction" t JOIN account a ON a.id = t.account_id'
        ' JOIN "transaction" o ON o.transfer_group_id = t.transfer_group_id AND o.id != t.id'
        " JOIN account oa ON oa.id = o.account_id"
        " WHERE t.status = 'booked' AND t.date >= ? AND t.date < ? AND a.area = ? AND oa.area != ?",
        (window.month_start, window.end, area, area),
    ).fetchone()
    return int(row[0]), int(row[1])


def _uncategorized_count(conn: sqlite3.Connection, window: _Window, area: Area) -> int:
    row = conn.execute(_UNCATEGORIZED_COUNT_SQL, (window.month_start, window.end, area, area)).fetchone()
    return int(row[0])


def bookings(
    conn: sqlite3.Connection, month: str, area: Area, *, category_id: int | None, flow: Flow | None = None
) -> list[Booking]:
    """The booked, non-transfer bookings of ``month`` in ``area`` with ``category_id``.

    ``category_id=None`` selects the uncategorised ones (the list of UC step 3). ``flow`` narrows
    to income or expense rows — the drilldown of one line. Ordered by date, then entry order.
    """
    window = _Window(month)
    rows = conn.execute(_BOOKINGS_SQL[flow], (category_id, window.month_start, window.end, area, area))
    return [
        Booking(
            id=row["id"],
            date=date.fromisoformat(row["date"]),
            amount_cents=row["amount_cents"],
            account_id=row["account_id"],
            account_name=row["account_name"],
            category_id=row["category_id"],
            text=row["text"],
            counterparty=row["counterparty"],
            memo=row["memo"],
        )
        for row in rows
    ]
