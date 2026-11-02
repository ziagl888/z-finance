"""``/api/dashboard``: everything the start page shows, in one response (UC NF5, SPEC AK8).

One route, no query parameters: the three figures of the weekly routine (free until the end of the
month with the levy share reserved beside it, remaining debt with its trend, overdue items), the
due items of the next 14 days, the account balances, the running month, the 90-day mini curve, the
open allocation proposals, the number of receipts to check and the two hints. Every figure comes
from the service that owns it (see ``service``); reading brings the levy back-payment items, the
debts and the allocation proposals up to date, like the screens of those features do.

Amounts are integer cents (``*_cents``); dates are ``YYYY-MM-DD``. "Today" is the server's local
date.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.allocation.service import AllocationError
from zfinance.domain.dashboard import service
from zfinance.domain.debts.service import DebtsError
from zfinance.domain.due.service import DueError
from zfinance.domain.forecast.service import ForecastError
from zfinance.domain.levy.service import LevyError
from zfinance.domain.pots.service import PotsError
from zfinance.domain.recurring.service import RecurringError
from zfinance.domain.reports.service import ReportsError
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Errors = (
    AccountsError,
    AllocationError,
    DebtsError,
    DueError,
    ForecastError,
    LevyError,
    PotsError,
    RecurringError,
    ReportsError,
    TransactionsError,
)
AnyError = (
    AccountsError
    | AllocationError
    | DebtsError
    | DueError
    | ForecastError
    | LevyError
    | PotsError
    | RecurringError
    | ReportsError
    | TransactionsError
)


def _fail(exc: AnyError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class FreeOut(BaseModel):
    """The one figure and its terms; ``levy_gap_cents`` is the sub line "reserved for levies"."""

    as_of: date
    accounts_cents: int
    planned_cents: int
    reserve_cents: int
    levy_gap_cents: int
    free_cents: int


class DebtOut(BaseModel):
    """Remaining debt now and at the end of the previous month; ``trend_cents`` < 0 = shrinking."""

    total_cents: int
    previous_month_cents: int
    trend_cents: int


class BalancesOut(BaseModel):
    """Balances of the active payment accounts per area and in total."""

    private_cents: int
    business_cents: int
    total_cents: int


class DueOut(BaseModel):
    """A planned item of the next 14 days; ``days_overdue`` > 0 means it is late."""

    id: int
    date: date
    amount_cents: int
    text: str
    account_id: int
    category_id: int | None
    days_overdue: int
    long_overdue: bool


class MonthLineOut(BaseModel):
    """One category line of the month card, signed (expenses negative)."""

    flow: str
    category_id: int | None
    category_name: str | None
    amount_cents: int


class MonthOut(BaseModel):
    """The running month over all areas with its largest lines (the Month screen has all of them)."""

    month: str
    income_cents: int
    expense_cents: int
    balance_cents: int
    uncategorized_count: int
    top_lines: list[MonthLineOut]


class CurveDayOut(BaseModel):
    """One day of the mini curve: balance and the line after levies."""

    date: date
    balance_cents: int
    available_cents: int


class ProposalOut(BaseModel):
    """An open allocation proposal; ``overdue`` is the 7-day highlight (UC-ZF-014 4b)."""

    id: int
    date: date
    text: str
    amount_cents: int
    bookable_cents: int
    rule_name: str | None
    days_open: int
    overdue: bool


class HintsOut(BaseModel):
    """The hints of UC-ZF-015 2b (gap above the free balance) and UC-ZF-008 1a (no tax plan)."""

    levies_not_covered: bool
    tax_plan_missing: bool


class DashboardOut(BaseModel):
    """The whole start page. ``overdue_count`` counts the overdue items among ``due_items``."""

    today: date
    month_end: date
    free: FreeOut
    debt: DebtOut
    overdue_count: int
    due_items: list[DueOut]
    balances: BalancesOut
    month: MonthOut
    mini_curve: list[CurveDayOut]
    proposals: list[ProposalOut]
    receipts_review_count: int
    hints: HintsOut


def _out(result: service.Dashboard) -> DashboardOut:
    return DashboardOut(
        today=result.today,
        month_end=result.month_end,
        free=FreeOut(**vars(result.free)),
        debt=DebtOut(**vars(result.debt)),
        overdue_count=result.overdue_count,
        due_items=[
            DueOut(
                id=item.id,
                date=item.date,
                amount_cents=item.amount_cents,
                text=item.text,
                account_id=item.account_id,
                category_id=item.category_id,
                days_overdue=item.days_overdue,
                long_overdue=item.long_overdue,
            )
            for item in result.due_items
        ],
        balances=BalancesOut(
            private_cents=result.balances.by_area["private"],
            business_cents=result.balances.by_area["business"],
            total_cents=result.balances.total_cents,
        ),
        month=MonthOut(
            month=result.month.month,
            income_cents=result.month.income_cents,
            expense_cents=result.month.expense_cents,
            balance_cents=result.month.balance_cents,
            uncategorized_count=result.month.uncategorized_count,
            top_lines=[MonthLineOut(**vars(line)) for line in result.month.top_lines],
        ),
        mini_curve=[CurveDayOut(**vars(day)) for day in result.mini_curve],
        proposals=[ProposalOut(**vars(proposal)) for proposal in result.proposals],
        receipts_review_count=result.receipts_review_count,
        hints=HintsOut(**vars(result.hints)),
    )


# --- routes ------------------------------------------------------------------------------------


@router.get("")
def get_dashboard(conn: Conn) -> DashboardOut:
    """The three figures of the weekly routine and the cards below them, in one read."""
    try:
        return _out(service.dashboard(conn))
    except Errors as exc:
        _fail(exc)
