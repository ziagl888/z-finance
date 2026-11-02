"""``/api/payoff``: the payoff plan of UC-ZF-012.

``GET /api/payoff`` returns the plan with its figures (it syncs the planned special payments
first), ``PUT /api/payoff`` saves budget, strategy, order and income override and plans, and
``GET /api/payoff/what-if?delta_cents=`` computes the plan with another budget without saving.

Amounts are integer cents (``*_cents``) and refused as floats; months are ``YYYY-MM``. "Today" is
the server's local date.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StrictInt

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.debts.service import DebtsError
from zfinance.domain.payoff import service
from zfinance.domain.payoff.plan import Strategy
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import NonNegativeCents

router = APIRouter(prefix="/api/payoff", tags=["payoff"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Errors = (service.PayoffError, DebtsError, AccountsError, TransactionsError)


def _fail(exc: service.PayoffError | DebtsError | AccountsError | TransactionsError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class PlanIn(BaseModel):
    """Body of PUT: ``budget_cents`` is the amount per month on top of the instalments."""

    strategy: Strategy
    budget_cents: NonNegativeCents
    manual_order: list[StrictInt] = Field(default_factory=list, max_length=200)
    income_override_cents: NonNegativeCents | None = None


class AvailabilityOut(BaseModel):
    """The available amount per month and its terms (UC step 2)."""

    income_cents: int
    income_months: list[str]
    income_overridden: bool
    fixed_costs_cents: int
    installments_cents: int
    pot_rates_cents: int
    levy_cents: int
    levy_rate_bp: int
    available_cents: int


class DebtLineOut(BaseModel):
    """An open debt in plan order; ``end_month`` = month of its last payment (null: no plan)."""

    id: int
    creditor: str
    remaining_cents: int
    installment_cents: int | None
    interval: str | None
    interest_rate_pct: str | None
    end_month: str | None


class MonthLineOut(BaseModel):
    """One plan month; ``extra_to`` lists [debt id, special payment] pairs."""

    month: str
    installments_cents: int
    extra_cents: int
    extra_to: list[tuple[int, int]]
    remaining_cents: int


class ProgressOut(BaseModel):
    """Planned against booked repayment of a month of the saved plan."""

    month: str
    planned_cents: int
    actual_cents: int
    difference_cents: int
    completed: bool


class StatusOut(BaseModel):
    """What the payoff screen shows."""

    saved: bool
    strategy: Strategy
    manual_order: list[int]
    budget_cents: int
    total_monthly_cents: int
    income_override_cents: int | None
    availability: AvailabilityOut
    remaining_total_cents: int
    has_plan: bool
    shortfall_cents: int
    debts: list[DebtLineOut]
    months: list[MonthLineOut]
    debt_free_month: str | None
    baseline_end_month: str | None
    shift_months: int | None
    progress: list[ProgressOut]
    behind: bool
    catch_up_cents: int
    over_budget: bool
    suggested_budget_cents: int | None
    free_now_cents: int
    missing_pay_account: list[int]


class WhatIfOut(BaseModel):
    """The plan with another budget; nothing is saved. ``ends`` maps debt id to its last month."""

    budget_cents: int
    total_monthly_cents: int
    has_plan: bool
    debt_free_month: str | None
    ends: dict[int, str | None]


def _status_out(result: service.Status) -> StatusOut:
    data = asdict(result)
    data["progress"] = [
        ProgressOut(
            month=row.month,
            planned_cents=row.planned_cents,
            actual_cents=row.actual_cents,
            difference_cents=row.difference_cents,
            completed=row.completed,
        )
        for row in result.progress
    ]
    return StatusOut.model_validate(data)


# --- routes ------------------------------------------------------------------------------------


@router.get("")
def get_plan(conn: Conn) -> StatusOut:
    """The plan with its figures, order, months and progress."""
    try:
        return _status_out(service.status(conn))
    except Errors as exc:
        _fail(exc)


@router.put("")
def put_plan(body: PlanIn, conn: Conn) -> StatusOut:
    """Save budget, strategy, order and income override; plan the special payments."""
    fields = service.PlanFields(
        strategy=body.strategy,
        budget_cents=body.budget_cents,
        manual_order=tuple(body.manual_order),
        income_override_cents=body.income_override_cents,
    )
    try:
        return _status_out(service.save_plan(conn, fields))
    except Errors as exc:
        _fail(exc)


@router.get("/what-if")
def get_what_if(conn: Conn, delta_cents: Annotated[int, Query(ge=-100_000_000, le=100_000_000)] = 0) -> WhatIfOut:
    """The debt-free month with the budget changed by ``delta_cents``; nothing is saved (AK5)."""
    return WhatIfOut.model_validate(asdict(service.what_if(conn, delta_cents)))
