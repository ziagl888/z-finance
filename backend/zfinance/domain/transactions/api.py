"""``/api/transactions``: quick entry of UC-ZF-002.

Bookings (income/expense) under ``/api/transactions``, transfers under
``/api/transactions/transfers`` (by their group id), suggestions under
``/api/transactions/suggest``; the fixed paths are declared before ``/{transaction_id}``.

Amounts are integer cents (``*_cents``) and refused as floats; dates are ``YYYY-MM-DD``. "Today"
(planned vs. booked) is the server's local date.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.transactions import service
from zfinance.domain.transactions.service import Source, Status
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import AmountCents, PositiveCents

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Counterparty = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


def _fail(exc: service.TransactionsError | AccountsError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class BookingIn(BaseModel):
    """Body of create and update of an income or expense (full replacement). 0 is refused (400)."""

    date: date
    amount_cents: AmountCents
    account_id: int
    category_id: int | None = None
    text: Text = ""
    counterparty: Counterparty | None = None
    memo: Memo | None = None

    def fields(self) -> service.BookingFields:
        """The service's input value."""
        return service.BookingFields(**self.model_dump())


class TransferIn(BaseModel):
    """Body of create and update of a transfer; ``amount_cents`` is positive, from → to."""

    date: date
    amount_cents: PositiveCents
    from_account_id: int
    to_account_id: int
    category_id: int | None = None
    text: Text = ""
    memo: Memo | None = None

    def fields(self) -> service.TransferFields:
        """The service's input value."""
        return service.TransferFields(**self.model_dump())


class TransactionOut(BaseModel):
    """A stored booking; ``transfer_account_id`` is the other leg's account of a transfer."""

    id: int
    date: date
    amount_cents: int
    account_id: int
    category_id: int | None
    text: str
    counterparty: str | None
    memo: str | None
    status: Status
    source: Source
    transfer_group_id: str | None
    transfer_account_id: int | None
    recurring_id: int | None
    created_at: str


class SuggestionOut(BaseModel):
    """What earlier bookings with this text used."""

    text: str
    category_id: int | None
    counterparty: str | None
    last_date: date


def _out(item: service.Transaction) -> TransactionOut:
    return TransactionOut(
        id=item.id,
        date=item.date,
        amount_cents=item.amount_cents,
        account_id=item.account_id,
        category_id=item.category_id,
        text=item.text,
        counterparty=item.counterparty,
        memo=item.memo,
        status=item.status,
        source=item.source,
        transfer_group_id=item.transfer_group_id,
        transfer_account_id=item.transfer_account_id,
        recurring_id=item.recurring_id,
        created_at=item.created_at,
    )


# --- suggestions and transfers -----------------------------------------------------------------


@router.get("/suggest")
def suggest(conn: Conn, q: Annotated[str, Query(max_length=200)] = "") -> list[SuggestionOut]:
    """Earlier texts matching ``q`` with the category and counterparty last used for them."""
    return [
        SuggestionOut(text=s.text, category_id=s.category_id, counterparty=s.counterparty, last_date=s.last_date)
        for s in service.suggest(conn, q)
    ]


@router.post("/transfers", status_code=201)
def create_transfer(body: TransferIn, conn: Conn) -> list[TransactionOut]:
    """Create a transfer; returns its two legs (source first)."""
    try:
        legs = service.create_transfer(conn, body.fields())
    except (service.TransactionsError, AccountsError) as exc:
        _fail(exc)
    return [_out(leg) for leg in legs]


@router.put("/transfers/{group_id}")
def update_transfer(group_id: str, body: TransferIn, conn: Conn) -> list[TransactionOut]:
    """Rewrite both legs of a transfer; returns them (source first)."""
    try:
        legs = service.update_transfer(conn, group_id, body.fields())
    except (service.TransactionsError, AccountsError) as exc:
        _fail(exc)
    return [_out(leg) for leg in legs]


# --- bookings ----------------------------------------------------------------------------------


@router.get("")
def list_transactions(
    conn: Conn,
    account_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[TransactionOut]:
    """The most recent bookings, newest first, optionally of one account."""
    return [_out(item) for item in service.list_transactions(conn, limit=limit, account_id=account_id)]


@router.post("", status_code=201)
def create_transaction(body: BookingIn, conn: Conn) -> TransactionOut:
    """Create an income or expense (``planned`` when dated after today)."""
    try:
        item = service.create_transaction(conn, body.fields())
    except (service.TransactionsError, AccountsError) as exc:
        _fail(exc)
    return _out(item)


@router.get("/{transaction_id}")
def get_transaction(transaction_id: int, conn: Conn) -> TransactionOut:
    """One booking."""
    try:
        item = service.get_transaction(conn, transaction_id)
    except service.TransactionsError as exc:
        _fail(exc)
    return _out(item)


@router.put("/{transaction_id}")
def update_transaction(transaction_id: int, body: BookingIn, conn: Conn) -> TransactionOut:
    """Replace a booking's fields; for a transfer leg the other leg follows."""
    try:
        item = service.update_transaction(conn, transaction_id, body.fields())
    except (service.TransactionsError, AccountsError) as exc:
        _fail(exc)
    return _out(item)


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, conn: Conn) -> Response:
    """Delete a booking; a transfer leg takes the other leg with it."""
    try:
        service.delete_transaction(conn, transaction_id)
    except service.TransactionsError as exc:
        _fail(exc)
    return Response(status_code=204)
