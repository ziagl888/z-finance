"""``/api/due``: the due-items review of UC-ZF-004.

``GET /api/due`` lists the planned items up to today + 14 days (overdue first); a single item is
confirmed with ``POST /api/due/{id}/confirm``, skipped with ``POST /api/due/{id}/skip`` and moved
with ``POST /api/due/{id}/postpone``. The
``{id}`` is the item's booking id as listed (a transfer: its outgoing leg).

Amounts are integer cents (``*_cents``) and refused as floats; dates are ``YYYY-MM-DD``. "Today" is
the server's local date.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.due import service
from zfinance.domain.transactions.service import Status, Transaction, TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import PositiveCents

router = APIRouter(prefix="/api/due", tags=["due"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Day = date
Errors = (service.DueError, AccountsError, TransactionsError)


def _fail(exc: service.DueError | AccountsError | TransactionsError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class DueItemOut(BaseModel):
    """One planned item to review; ``amount_cents`` is signed.

    ``days_overdue`` is 0 unless the date is before today, ``long_overdue`` is set above 30 days;
    ``confirm_date`` is the date a confirm without a date books on (never after today).
    """

    id: int
    due_date: date | None
    confirm_date: date
    amount_cents: int
    account_id: int
    transfer_account_id: int | None
    category_id: int | None
    text: str
    counterparty: str | None
    recurring_id: int | None
    moved: bool
    days_overdue: int
    long_overdue: bool
    date: date  # declared last: the name shadows the type in the annotations below it


class ConfirmIn(BaseModel):
    """Optional overrides of a confirm: a positive amount (the sign of the item stays) and a booking
    date of today or earlier. Omitted values are the planned ones."""

    amount_cents: PositiveCents | None = None
    # The field is named ``date``: its default would shadow the type inside the class body.
    date: Day | None = None


class PostponeIn(BaseModel):
    """The new date of a postponed item; it must be after today."""

    date: date


class BookingOut(BaseModel):
    """The item after the change: booked (confirm) or still planned on its new date (postpone)."""

    id: int
    date: date
    amount_cents: int
    account_id: int
    status: Status
    recurring_id: int | None
    transfer_group_id: str | None


def _due_out(item: service.DueItem) -> DueItemOut:
    return DueItemOut(
        id=item.id,
        date=item.date,
        due_date=item.due_date,
        amount_cents=item.amount_cents,
        account_id=item.account_id,
        transfer_account_id=item.transfer_account_id,
        category_id=item.category_id,
        text=item.text,
        counterparty=item.counterparty,
        recurring_id=item.recurring_id,
        moved=item.moved,
        days_overdue=item.days_overdue,
        long_overdue=item.long_overdue,
        confirm_date=item.confirm_date,
    )


def _booking_out(item: Transaction) -> BookingOut:
    return BookingOut(
        id=item.id,
        date=item.date,
        amount_cents=item.amount_cents,
        account_id=item.account_id,
        status=item.status,
        recurring_id=item.recurring_id,
        transfer_group_id=item.transfer_group_id,
    )


# --- routes ------------------------------------------------------------------------------------


@router.get("")
def list_due(conn: Conn) -> list[DueItemOut]:
    """Planned items up to today + 14 days, overdue ones first."""
    return [_due_out(item) for item in service.list_due(conn)]


@router.post("/{planned_id}/confirm")
def confirm(planned_id: int, body: ConfirmIn, conn: Conn) -> BookingOut:
    """Book the item (optionally with another amount or date); the recurring item stays as it is."""
    try:
        booked = service.confirm(conn, planned_id, amount_cents=body.amount_cents, on_date=body.date)
    except Errors as exc:
        _fail(exc)
    return _booking_out(booked)


@router.post("/{planned_id}/skip")
def skip(planned_id: int, conn: Conn) -> BookingOut:
    """Skip the item (it will not happen); the recurring item goes on without that date."""
    try:
        skipped = service.skip(conn, planned_id)
    except Errors as exc:
        _fail(exc)
    return _booking_out(skipped)


@router.post("/{planned_id}/postpone")
def postpone(planned_id: int, body: PostponeIn, conn: Conn) -> BookingOut:
    """Move the item to a later date; it stays planned."""
    try:
        moved = service.postpone(conn, planned_id, body.date)
    except Errors as exc:
        _fail(exc)
    return _booking_out(moved)
