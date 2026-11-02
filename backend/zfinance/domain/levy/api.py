"""``/api/levy``: levy reserve, required revenue, free amount and chart data of UC-ZF-015.

``/settings`` (get/put: rate in basis points, base, due date of the back-payment, levy pot,
account), ``/status`` (target, actual and gap per open year with the back-payment items and the
warnings; syncs the back-payment items first), ``/free-amount`` (the core rule, with its terms),
``/required-revenue`` (per month, split by block), ``/charts/income`` and ``/charts/expenses``
(24 months, data only — the screen is task -024) and ``/backpayments/{year}/archive`` (UC 3a).

Amounts are integer cents (``*_cents``) and refused as floats; dates are ``YYYY-MM-DD``, due dates
``MM-DD``. "Today" is the server's local date.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, StrictInt, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.levy import service
from zfinance.domain.recurring.service import RecurringError
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException

router = APIRouter(prefix="/api/levy", tags=["levy"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
DueMonthDay = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5)]
Errors = (service.LevyError, AccountsError, TransactionsError, RecurringError)


def _fail(exc: service.LevyError | AccountsError | TransactionsError | RecurringError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class LevyIn(BaseModel):
    """Body of PUT (full replacement). ``levy_rate_bp`` in basis points: 3500 = 35 %, at most 9000."""

    levy_rate_bp: StrictInt
    levy_base: service.Base = "income"
    due_month_day: DueMonthDay = service.DEFAULT_DUE_MONTH_DAY
    levy_pot_id: int | None = None
    account_id: int | None = None


class LevyOut(BaseModel):
    """The stored settings; ``settings_missing`` while nothing was saved (rate 0)."""

    levy_rate_bp: int
    levy_base: service.Base
    due_month_day: str
    levy_pot_id: int | None
    account_id: int | None
    settings_missing: bool


class YearOut(BaseModel):
    """One open year: base, target, prepayments, the pot's share, actual, gap and the back-payment."""

    year: int
    base_cents: int
    target_cents: int
    prepayments_cents: int
    pot_share_cents: int
    actual_cents: int
    gap_cents: int
    backpayment_cents: int
    backpayment_date: date
    backpayment_transaction_id: int | None
    backpayment_state: str


class StatusOut(BaseModel):
    """UC step 2 with the warnings: no levy pot (2a), gap above the free balance (2b)."""

    settings: LevyOut
    levy_pot_id: int | None
    levy_pot_missing: bool
    levy_pot_ambiguous: bool
    pot_balance_cents: int
    years: list[YearOut]
    levy_gap_cents: int
    free_balance_cents: int
    levy_uncovered: bool
    backpayment_account_missing: bool


class FreeAmountOut(BaseModel):
    """``free_cents`` = accounts + planned - reserve - levy gap (planned is signed, usually negative)."""

    as_of: date
    accounts_cents: int
    planned_cents: int
    reserve_cents: int
    levy_gap_cents: int
    free_cents: int


class RequiredOut(BaseModel):
    """Revenue needed per month; the parts sum to ``required_cents``."""

    required_cents: int
    fixed_costs_cents: int
    installments_cents: int
    payoff_budget_cents: int
    pot_rates_cents: int
    reserve_build_cents: int
    levy_cents: int
    levy_rate_bp: int


class IncomeMonthOut(BaseModel):
    """One month of the income chart."""

    month: str
    actual_cents: int
    planned_cents: int
    required_cents: int
    below_required: bool


class CategoryOut(BaseModel):
    """Expenses of one category in a month, positive cents."""

    category_id: int | None
    category_name: str | None
    amount_cents: int


class ExpenseMonthOut(BaseModel):
    """One month of the expense chart."""

    month: str
    planned: bool
    categories: list[CategoryOut]
    liabilities_cents: int
    remaining_debt_cents: int


def _settings_out(settings: service.LevySettings) -> LevyOut:
    return LevyOut(
        levy_rate_bp=settings.levy_rate_bp,
        levy_base=settings.levy_base,
        due_month_day=settings.due_month_day,
        levy_pot_id=settings.levy_pot_id,
        account_id=settings.account_id,
        settings_missing=settings.settings_missing,
    )


# --- routes ------------------------------------------------------------------------------------


@router.get("/settings")
def get_levy_settings(conn: Conn) -> LevyOut:
    """The levy settings (rate 0 while nothing is saved)."""
    return _settings_out(service.get_settings(conn))


@router.put("/settings")
def put_levy_settings(body: LevyIn, conn: Conn) -> LevyOut:
    """Store the levy settings; the back-payment items follow at once."""
    fields = service.LevyFields(
        body.levy_rate_bp, body.levy_base, body.due_month_day, body.levy_pot_id, body.account_id
    )
    try:
        return _settings_out(service.save_settings(conn, fields))
    except Errors as exc:
        _fail(exc)


@router.get("/status")
def get_levy_status(conn: Conn) -> StatusOut:
    """Sync the back-payment items, then target, actual and gap per open year."""
    try:
        service.sync_backpayments(conn)
        status = service.levy_status(conn)
    except Errors as exc:
        _fail(exc)
    return StatusOut(
        settings=_settings_out(status.settings),
        levy_pot_id=status.levy_pot_id,
        levy_pot_missing=status.levy_pot_missing,
        levy_pot_ambiguous=status.levy_pot_ambiguous,
        pot_balance_cents=status.pot_balance_cents,
        years=[YearOut(**vars(year)) for year in status.years],
        levy_gap_cents=status.levy_gap_cents,
        free_balance_cents=status.free_balance_cents,
        levy_uncovered=status.levy_uncovered,
        backpayment_account_missing=status.backpayment_account_missing,
    )


@router.get("/free-amount")
def get_free_amount(conn: Conn, as_of: Annotated[date | None, Query()] = None) -> FreeAmountOut:
    """The free amount up to ``as_of`` (default: the end of the current month), net of the levy gap."""
    today = date.today()
    as_of = as_of or service.month_end(today)
    try:
        result = service.free_amount_breakdown(conn, as_of, today=today)
    except Errors as exc:
        _fail(exc)
    return FreeAmountOut(**vars(result))


@router.get("/required-revenue")
def get_required_revenue(conn: Conn) -> RequiredOut:
    """Revenue needed per month at the current rate, split by block."""
    return RequiredOut(**vars(service.required_revenue_monthly(conn)))


@router.get("/charts/income")
def get_income_chart(conn: Conn) -> list[IncomeMonthOut]:
    """24 months of business income against the required revenue."""
    return [IncomeMonthOut(**vars(month)) for month in service.income_chart(conn)]


@router.get("/charts/expenses")
def get_expense_chart(conn: Conn, area: service.ChartArea = "all") -> list[ExpenseMonthOut]:
    """24 months of expenses by category, the liabilities line and the remaining debt."""
    return [
        ExpenseMonthOut(
            month=month.month,
            planned=month.planned,
            categories=[CategoryOut(**vars(category)) for category in month.categories],
            liabilities_cents=month.liabilities_cents,
            remaining_debt_cents=month.remaining_debt_cents,
        )
        for month in service.expense_chart(conn, area)
    ]


@router.post("/backpayments/{year}/archive", status_code=204)
def archive_backpayment(year: int, conn: Conn) -> Response:
    """The assessment is in: keep the estimate, remove the planned back-payment (UC 3a)."""
    try:
        service.archive_backpayment(conn, year)
    except Errors as exc:
        _fail(exc)
    return Response(status_code=204)
