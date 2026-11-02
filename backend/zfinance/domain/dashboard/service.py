"""The dashboard of UC NF5 / SPEC AK8: one read over the services that own the figures.

The weekly routine asks three questions and the dashboard answers them at the top, without
scrolling: how much is free until the end of the month (net of the open levy gap — the core rule),
how large the remaining debt is and whether it is shrinking, and how many planned items are
overdue. Below them the cards summarise what the other screens can act on: the due items of the
next 14 days, the account balances, the running month, the next 90 days of the forecast, the open
allocation proposals and the receipts waiting to be checked, plus the two hints of UC-ZF-015 2b and
UC-ZF-008 1a.

**This module computes nothing that another service owns.** Every figure is the answer of the
service that owns it — `levy.free_amount_breakdown`, `debts.overview`, `due.list_due`,
`reports.month_report`, `forecast.forecast`, `allocation.overview`, `receipts.list_receipts`,
`tax.tax_plan_missing`. The single exception is the debt trend, which NF5 asks for and no service
carries: it compares today's remaining debt with the same debts' liability balances at the end of
the previous month (`accounts.balance_at`).

Reading syncs, like everywhere else in this project: `levy.sync_backpayments` runs first (as
`/api/forecast` and `/api/levy/status` do), `debts.overview` and `allocation.overview` bring their
own state up to date. Nothing else is written.

Errors of the consumed services pass through unchanged; this module raises none of its own.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import Area
from zfinance.domain.allocation import service as allocation
from zfinance.domain.debts import service as debts
from zfinance.domain.due import service as due
from zfinance.domain.forecast import service as forecast
from zfinance.domain.levy import service as levy
from zfinance.domain.receipts import service as receipts
from zfinance.domain.reports import service as reports
from zfinance.domain.tax import service as tax

#: Days of the mini curve (SPEC AK8). The 3-month horizon is longer, so it is truncated.
CURVE_DAYS = 90
#: Category lines of the month card; the Month screen shows the rest.
MONTH_LINES = 6
#: Most receipts counted for the "to check" figure — a personal ledger takes ~20 a day (UC-ZF-013).
REVIEW_LIMIT = 500


@dataclass(frozen=True)
class DebtFigure:
    """Remaining debt now and at the end of the previous month; ``trend_cents`` < 0 = shrinking."""

    total_cents: int
    previous_month_cents: int
    trend_cents: int


@dataclass(frozen=True)
class Balances:
    """Balances of the payment accounts per area and in total (liabilities are the debt figure)."""

    by_area: dict[Area, int]
    total_cents: int


@dataclass(frozen=True)
class MonthLine:
    """One category line of the month card, signed like the report (expenses negative)."""

    flow: reports.Flow
    category_id: int | None
    category_name: str | None
    amount_cents: int


@dataclass(frozen=True)
class MonthOverview:
    """The running month over all areas: the totals and its largest category lines."""

    month: str
    income_cents: int
    expense_cents: int
    balance_cents: int
    uncategorized_count: int
    top_lines: list[MonthLine]


@dataclass(frozen=True)
class ProposalEntry:
    """An open allocation proposal as the card shows it; ``overdue`` is the 7-day highlight (4b)."""

    id: int
    date: date
    text: str
    amount_cents: int
    bookable_cents: int
    rule_name: str | None
    days_open: int
    overdue: bool


@dataclass(frozen=True)
class Hints:
    """The two hints of the dashboard, each the flag of the service that decides it."""

    levies_not_covered: bool
    tax_plan_missing: bool


@dataclass(frozen=True)
class Dashboard:
    """Everything the start page shows (UC NF5, SPEC AK8).

    ``free`` carries the one figure and its terms; ``free.free_cents`` is what
    ``levy.free_amount`` returns and ``free.levy_gap_cents`` the sub line "of which reserved for
    levies". ``overdue_count`` counts the overdue items among ``due_items``.
    """

    today: date
    month_end: date
    free: levy.FreeAmount
    debt: DebtFigure
    overdue_count: int
    due_items: list[due.DueItem]
    balances: Balances
    month: MonthOverview
    mini_curve: list[forecast.ForecastDay]
    proposals: list[ProposalEntry]
    receipts_review_count: int
    hints: Hints


def previous_month_end(today: date) -> date:
    """The last day of the month before ``today`` (the reference date of the debt trend)."""
    return today.replace(day=1) - timedelta(days=1)


def debt_figure(conn: sqlite3.Connection, *, today: date) -> DebtFigure:
    """Remaining debt now (``debts.overview``) against the same debts at the end of last month.

    A debt's remaining amount is minus the balance of its liability account, so the figure of an
    earlier day is ``accounts.balance_at`` on that day. A debt whose account was opened later
    counts as 0 then — it is new, and the trend says so.
    """
    overview = debts.overview(conn, today=today)
    reference = previous_month_end(today)
    previous = sum(-accounts.balance_at(conn, debt.account_id, reference) for debt in overview.debts)
    return DebtFigure(
        total_cents=overview.remaining_total_cents,
        previous_month_cents=previous,
        trend_cents=overview.remaining_total_cents - previous,
    )


def balances(conn: sqlite3.Connection, *, today: date) -> Balances:
    """Balances of the active payment accounts (liabilities are the debt figure) per area."""
    by_area: dict[Area, int] = dict.fromkeys(accounts.AREAS, 0)
    for account in accounts.list_accounts(conn):
        if account.type == "liability":
            continue
        by_area[account.area] += accounts.balance_at(conn, account.id, today)
    return Balances(by_area=by_area, total_cents=sum(by_area.values()))


def month_overview(conn: sqlite3.Connection, *, today: date) -> MonthOverview:
    """The running month over all areas; the report's own order decides which lines are shown."""
    report = reports.month_report(conn, f"{today.year:04d}-{today.month:02d}", "all")
    return MonthOverview(
        month=report.month,
        income_cents=report.income.amount_cents,
        expense_cents=report.expense.amount_cents,
        balance_cents=report.balance.amount_cents,
        uncategorized_count=report.uncategorized_count,
        top_lines=[
            MonthLine(line.flow, line.category_id, line.category_name, line.comparison.amount_cents)
            for line in report.lines[:MONTH_LINES]
        ],
    )


def mini_curve(conn: sqlite3.Connection, *, today: date) -> list[forecast.ForecastDay]:
    """The first ``CURVE_DAYS`` days of the total forecast (balance and available after levies)."""
    curve = forecast.forecast(conn, forecast.View(), 3, today=today)
    return curve.days[:CURVE_DAYS]


def open_proposals(conn: sqlite3.Connection, *, today: date) -> list[ProposalEntry]:
    """The open allocation proposals of UC-ZF-014, waiting for a click on the Allocation screen."""
    return [
        ProposalEntry(
            id=proposal.id,
            date=proposal.date,
            text=proposal.text,
            amount_cents=proposal.amount_cents,
            bookable_cents=proposal.bookable_cents,
            rule_name=proposal.rule_name,
            days_open=proposal.days_open,
            overdue=proposal.overdue,
        )
        for proposal in allocation.overview(conn, today=today).proposals
    ]


def dashboard(conn: sqlite3.Connection, *, today: date | None = None) -> Dashboard:
    """Everything the start page shows, in one read (UC NF5, SPEC AK8).

    The levy back-payment items are brought up to date first, like every other reader of the levy
    state; the debt and proposal overviews sync their own. The free amount is
    ``levy.free_amount_breakdown`` on the end of the month — the core rule, read, never recomputed.
    """
    today = today or date.today()
    levy.sync_backpayments(conn, today=today)
    month_end = levy.month_end(today)
    items = due.list_due(conn, today=today)
    status = levy.levy_status(conn, today=today)
    return Dashboard(
        today=today,
        month_end=month_end,
        free=levy.free_amount_breakdown(conn, month_end, today=today),
        debt=debt_figure(conn, today=today),
        overdue_count=sum(1 for item in items if item.days_overdue > 0),
        due_items=items,
        balances=balances(conn, today=today),
        month=month_overview(conn, today=today),
        mini_curve=mini_curve(conn, today=today),
        proposals=open_proposals(conn, today=today),
        receipts_review_count=len(receipts.list_receipts(conn, ["review"], limit=REVIEW_LIMIT)),
        hints=Hints(levies_not_covered=status.levy_uncovered, tax_plan_missing=tax.tax_plan_missing(conn)),
    )
