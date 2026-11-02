"""``/api/debts``: debts of UC-ZF-010.

The overview under ``/api/debts`` (list with sums per area; it syncs all debts first), one debt
under ``/api/debts/{id}`` (get/put/delete), its bookings under ``/{id}/history``, a special
payment under ``/{id}/payments`` and the payoff budget under ``/api/debts/settings``. Fixed paths
are declared before ``/{debt_id}``.

Amounts are integer cents (``*_cents``) and refused as floats; dates are ``YYYY-MM-DD``. "Today"
is the server's local date.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError, Area
from zfinance.domain.debts import service
from zfinance.domain.recurring.service import RecurringError
from zfinance.domain.transactions.service import Status, TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import NonNegativeCents, PositiveCents

router = APIRouter(prefix="/api/debts", tags=["debts"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Creditor = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Rate = Annotated[str, StringConstraints(strip_whitespace=True, max_length=12)]
# A field named ``date`` with a default shadows the type inside its class body.
Day = date
Errors = (service.DebtsError, AccountsError, TransactionsError, RecurringError)


def _fail(exc: service.DebtsError | AccountsError | TransactionsError | RecurringError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class DebtIn(BaseModel):
    """Body of update (full replacement). ``installment_cents``, ``interval`` and ``first_due``
    come together or not at all; with them ``pay_account_id`` is required."""

    creditor: Creditor
    area: Area
    total_amount_cents: PositiveCents
    installment_cents: PositiveCents | None = None
    interval: service.DebtInterval | None = None
    first_due: date | None = None
    pay_account_id: int | None = None
    interest_rate_pct: Rate | None = None
    contract_end: date | None = None
    target_end: date | None = None

    def fields(self) -> service.DebtFields:
        """The service's input value."""
        return service.DebtFields(**self.model_dump(exclude={"repaid_cents"}))


class DebtCreateIn(DebtIn):
    """Body of create: the fields plus the amount already repaid (the liability's starting point)."""

    repaid_cents: NonNegativeCents = 0


class PlanOut(BaseModel):
    """Open instalments: count, next and last due date, amount of the last one."""

    count: int
    next_due: date
    end: date
    last_installment_cents: int


class DebtOut(BaseModel):
    """A debt with its figures; ``plan`` for an open debt with instalment, ``required_monthly_cents``
    for an open debt without instalment that has a target end."""

    id: int
    creditor: str
    area: Area
    total_amount_cents: int
    installment_cents: int | None
    interval: service.DebtInterval | None
    first_due: date | None
    pay_account_id: int | None
    interest_rate_pct: str | None
    contract_end: date | None
    target_end: date | None
    account_id: int
    recurring_id: int | None
    closed: bool
    closed_at: str | None
    remaining_cents: int
    paid_cents: int
    plan: PlanOut | None
    required_monthly_cents: int | None


class OverviewOut(BaseModel):
    """All debts, remaining amount per area and in total."""

    debts: list[DebtOut]
    remaining_by_area: dict[Area, int]
    remaining_total_cents: int


class PaymentIn(BaseModel):
    """A special payment; ``date`` defaults to today, ``from_account_id`` to the paying account."""

    amount_cents: PositiveCents
    date: Day | None = None
    from_account_id: int | None = None


class PaymentOut(BaseModel):
    """The debt after the payment, the booked amount and whether it was capped at the remaining amount."""

    debt: DebtOut
    booked_cents: int
    capped: bool


class HistoryOut(BaseModel):
    """One booking on the liability account (+ = paid off, - = debt raised)."""

    id: int
    date: date
    amount_cents: int
    status: Status
    text: str
    transfer_account_id: int | None


class SettingsOut(BaseModel):
    """The monthly payoff budget in cents."""

    payoff_budget_cents: NonNegativeCents


def _debt_out(debt: service.Debt) -> DebtOut:
    plan = debt.plan
    return DebtOut(
        id=debt.id,
        creditor=debt.creditor,
        area=debt.area,
        total_amount_cents=debt.total_amount_cents,
        installment_cents=debt.installment_cents,
        interval=debt.interval,
        first_due=debt.first_due,
        pay_account_id=debt.pay_account_id,
        interest_rate_pct=debt.interest_rate_pct,
        contract_end=debt.contract_end,
        target_end=debt.target_end,
        account_id=debt.account_id,
        recurring_id=debt.recurring_id,
        closed=debt.closed_at is not None,
        closed_at=debt.closed_at,
        remaining_cents=debt.remaining_cents,
        paid_cents=debt.paid_cents,
        plan=None
        if plan is None
        else PlanOut(
            count=plan.count, next_due=plan.next_due, end=plan.end, last_installment_cents=plan.last_installment_cents
        ),
        required_monthly_cents=debt.required_monthly_cents,
    )


# --- fixed paths -------------------------------------------------------------------------------


@router.get("/settings")
def get_settings(conn: Conn) -> SettingsOut:
    """The monthly payoff budget."""
    return SettingsOut(payoff_budget_cents=service.payoff_budget(conn))


@router.put("/settings")
def put_settings(body: SettingsOut, conn: Conn) -> SettingsOut:
    """Store the monthly payoff budget."""
    try:
        cents = service.set_payoff_budget(conn, body.payoff_budget_cents)
    except Errors as exc:
        _fail(exc)
    return SettingsOut(payoff_budget_cents=cents)


# --- debts -------------------------------------------------------------------------------------


@router.get("")
def list_debts(conn: Conn) -> OverviewOut:
    """All debts (open first) with the remaining amount per area and in total."""
    result = service.overview(conn)
    return OverviewOut(
        debts=[_debt_out(debt) for debt in result.debts],
        remaining_by_area=result.remaining_by_area,
        remaining_total_cents=result.remaining_total_cents,
    )


@router.post("", status_code=201)
def create_debt(body: DebtCreateIn, conn: Conn) -> DebtOut:
    """Create a debt: liability account, and with an instalment its planned instalments."""
    try:
        debt = service.create_debt(conn, body.fields(), repaid_cents=body.repaid_cents)
    except Errors as exc:
        _fail(exc)
    return _debt_out(debt)


@router.get("/{debt_id}")
def get_debt(debt_id: int, conn: Conn) -> DebtOut:
    """One debt."""
    try:
        debt = service.get_debt(conn, debt_id)
    except Errors as exc:
        _fail(exc)
    return _debt_out(debt)


@router.put("/{debt_id}")
def update_debt(debt_id: int, body: DebtIn, conn: Conn) -> DebtOut:
    """Replace a debt's fields; a changed total is booked as the difference, instalments follow."""
    try:
        debt = service.update_debt(conn, debt_id, body.fields())
    except Errors as exc:
        _fail(exc)
    return _debt_out(debt)


@router.get("/{debt_id}/history")
def get_history(debt_id: int, conn: Conn) -> list[HistoryOut]:
    """Bookings on the debt's liability account, newest first."""
    try:
        rows = service.history(conn, debt_id)
    except Errors as exc:
        _fail(exc)
    return [
        HistoryOut(
            id=row.id,
            date=row.date,
            amount_cents=row.amount_cents,
            status=row.status,
            text=row.text,
            transfer_account_id=row.transfer_account_id,
        )
        for row in rows
    ]


@router.post("/{debt_id}/payments", status_code=201)
def pay_extra(debt_id: int, body: PaymentIn, conn: Conn) -> PaymentOut:
    """Book a special payment; an amount above the remaining amount is capped (``capped``)."""
    try:
        payment = service.pay_extra(
            conn, debt_id, body.amount_cents, on=body.date, from_account_id=body.from_account_id
        )
    except Errors as exc:
        _fail(exc)
    return PaymentOut(debt=_debt_out(payment.debt), booked_cents=payment.booked_cents, capped=payment.capped)


@router.delete("/{debt_id}", status_code=204)
def delete_debt(debt_id: int, conn: Conn) -> Response:
    """Delete a debt: instalments go; the liability account is deleted if unused, else archived."""
    try:
        service.delete_debt(conn, debt_id)
    except Errors as exc:
        _fail(exc)
    return Response(status_code=204)
