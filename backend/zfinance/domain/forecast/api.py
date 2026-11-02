"""``/api/forecast``: the liquidity forecast of UC-ZF-006 (data only — the screen is task -024).

Every route takes the view as query parameters: ``account_id`` (one account), else ``area``
(``private``/``business``), else all payment accounts; ``horizon`` is 3, 6 or 12 months (400
otherwise).

``GET /api/forecast`` (curve and figures; brings the levy back-payment items up to date first, like
``/api/levy/status``), ``POST /api/forecast/what-if`` (the same with hypothetical items, nothing is
stored), ``GET /api/forecast/items?date=`` (the rows acting on one day) and
``GET /api/forecast/emergency`` (pots in emergency order for the shortfall, ``null`` without one).

Amounts are integer cents (``*_cents``) and refused as floats; dates are ``YYYY-MM-DD``. "Today" is
the server's local date.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError, Area
from zfinance.domain.forecast import service
from zfinance.domain.levy import service as levy
from zfinance.domain.levy.service import LevyError
from zfinance.domain.pots.service import PotsError
from zfinance.domain.recurring.service import RecurringError
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import AmountCents

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Errors = (service.ForecastError, AccountsError, PotsError, LevyError, TransactionsError, RecurringError)
#: Most what-if items per request; a planning aid, not a bulk import.
MAX_WHAT_IF_ITEMS = 50


def _fail(
    exc: service.ForecastError | AccountsError | PotsError | LevyError | TransactionsError | RecurringError,
) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class DayOut(BaseModel):
    """One day of the curve: balance and the line after levies."""

    date: date
    balance_cents: int
    available_cents: int


class ForecastOut(BaseModel):
    """Curve plus UC step 3: minimum and first breach refer to ``available_cents``."""

    scope: service.Scope
    account_id: int | None
    area: Area | None
    horizon_months: int
    start: date
    end: date
    days: list[DayOut]
    minimum_date: date
    minimum_cents: int
    reserve_cents: int
    reserve_missing: bool
    first_breach_date: date | None
    shortfall_cents: int
    planned_income_cents: int
    planned_expense_cents: int
    overdue_count: int
    levy_applies: bool
    levy_gap_cents: int


class WhatIfItemIn(BaseModel):
    """A hypothetical item: signed cents, a day from today on, an account."""

    date: date
    amount_cents: AmountCents
    account_id: int
    text: Text = ""


class WhatIfIn(BaseModel):
    """Body of ``POST /what-if``: the view, the horizon and the items."""

    account_id: int | None = None
    area: Area | None = None
    horizon: int = service.DEFAULT_HORIZON
    items: list[WhatIfItemIn] = Field(max_length=MAX_WHAT_IF_ITEMS)


class ItemOut(BaseModel):
    """A row acting on the chosen day; ``kind`` is ``planned``, ``booked`` or ``what_if``."""

    transaction_id: int | None
    kind: str
    effective_date: date
    date: date
    account_id: int
    amount_cents: int
    text: str
    transfer_group_id: str | None
    overdue: bool


class EmergencyLineOut(BaseModel):
    """One pot of a proposal: what it gives and its target date afterwards."""

    pot_id: int
    name: str
    rank: int
    amount_cents: int
    new_balance_cents: int
    target_date: date | None
    shifted_target_date: date | None


class EmergencyProposalOut(BaseModel):
    """The proposal for one account; confirm it with ``POST /api/pots/emergency``."""

    account_id: int
    amount_cents: int
    lines: list[EmergencyLineOut]
    covered_cents: int
    shortfall_cents: int


class EmergencyOut(BaseModel):
    """UC 3b: the shortfall and which pots would cover it."""

    shortfall_cents: int
    proposals: list[EmergencyProposalOut]
    covered_cents: int
    uncovered_cents: int


def _out(result: service.Forecast) -> ForecastOut:
    fields = vars(result) | {"days": [DayOut(**vars(day)) for day in result.days]}
    return ForecastOut(**fields)


# --- routes ------------------------------------------------------------------------------------


@router.get("")
def get_forecast(
    conn: Conn,
    account_id: Annotated[int | None, Query()] = None,
    area: Annotated[Area | None, Query()] = None,
    horizon: Annotated[int, Query()] = service.DEFAULT_HORIZON,
) -> ForecastOut:
    """Bring the levy back-payment items up to date, then the curve of the view."""
    try:
        levy.sync_backpayments(conn)
        return _out(service.forecast(conn, service.View(account_id, area), horizon))
    except Errors as exc:
        _fail(exc)


@router.post("/what-if")
def post_what_if(body: WhatIfIn, conn: Conn) -> ForecastOut:
    """The curve with hypothetical items; nothing is stored (the back-payment items are synced first)."""
    items = [service.WhatIfItem(i.date, i.amount_cents, i.account_id, i.text) for i in body.items]
    try:
        levy.sync_backpayments(conn)
        return _out(service.what_if(conn, items, service.View(body.account_id, body.area), body.horizon))
    except Errors as exc:
        _fail(exc)


@router.get("/items")
def get_items(
    conn: Conn,
    day: Annotated[date, Query(alias="date")],
    account_id: Annotated[int | None, Query()] = None,
    area: Annotated[Area | None, Query()] = None,
) -> list[ItemOut]:
    """The rows acting on one day of the curve (overdue ones on today), back-payment items synced first."""
    try:
        levy.sync_backpayments(conn)
        return [ItemOut(**vars(item)) for item in service.items_at(conn, day, service.View(account_id, area))]
    except Errors as exc:
        _fail(exc)


@router.get("/emergency")
def get_emergency(
    conn: Conn,
    account_id: Annotated[int | None, Query()] = None,
    area: Annotated[Area | None, Query()] = None,
    horizon: Annotated[int, Query()] = service.DEFAULT_HORIZON,
) -> EmergencyOut | None:
    """Pots in emergency order for the view's shortfall; ``null`` while the reserve holds."""
    try:
        levy.sync_backpayments(conn)
        suggestion = service.emergency_suggestion(conn, service.forecast(conn, service.View(account_id, area), horizon))
    except Errors as exc:
        _fail(exc)
    if suggestion is None:
        return None
    return EmergencyOut(
        shortfall_cents=suggestion.shortfall_cents,
        proposals=[
            EmergencyProposalOut(
                account_id=p.account_id,
                amount_cents=p.amount_cents,
                lines=[EmergencyLineOut(**vars(line)) for line in p.lines],
                covered_cents=p.covered_cents,
                shortfall_cents=p.shortfall_cents,
            )
            for p in suggestion.proposals
        ],
        covered_cents=suggestion.covered_cents,
        uncovered_cents=suggestion.uncovered_cents,
    )
