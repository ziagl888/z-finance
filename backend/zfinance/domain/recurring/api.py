"""``/api/recurring``: recurring items and expected one-off items of UC-ZF-003.

Recurring items under ``/api/recurring`` (``/{id}`` get/put/delete, ``/{id}/activate``,
``/{id}/deactivate``, ``/{id}/items``), one planned occurrence under ``/api/recurring/items/{id}``
(move), a generation run under ``/api/recurring/generate``, expected one-off items under
``/api/recurring/expected``. Fixed paths are declared before ``/{recurring_id}``. Editing or
deleting an expected item goes through ``/api/transactions/{id}`` (it is an ordinary booking).

Amounts are integer cents (``*_cents``) and refused as floats; dates are ``YYYY-MM-DD``. "Today"
is the server's local date.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.recurring import service
from zfinance.domain.recurring.schedule import Interval
from zfinance.domain.transactions.service import BookingFields, Status, Transaction, TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import AmountCents, PositiveCents

router = APIRouter(prefix="/api/recurring", tags=["recurring"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Counterparty = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Errors = (service.RecurringError, AccountsError, TransactionsError)

#: A check another feature adds in ``create_app`` (``app.state.recurring_delete_guards``); it
#: raises ``RecurringError`` to refuse deleting an item that feature owns. The recurring feature
#: imports none of them, so the engine dependencies stay free of cycles.
DeleteGuard = Callable[[sqlite3.Connection, int], None]


def _fail(exc: service.RecurringError | AccountsError | TransactionsError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class RecurringIn(BaseModel):
    """Body of create and update (full replacement). Weekly needs ``weekday`` (0 = Monday), the
    other intervals ``day_of_month``; a transfer (``transfer_target_account_id``) a positive amount."""

    description: Text
    amount_cents: AmountCents
    account_id: int
    category_id: int | None = None
    transfer_target_account_id: int | None = None
    interval: Interval
    day_of_month: Annotated[int, Field(ge=1, le=31)] | None = None
    weekday: Annotated[int, Field(ge=0, le=6)] | None = None
    start_date: date
    end_date: date | None = None

    def fields(self) -> service.RecurringFields:
        """The service's input value."""
        return service.RecurringFields(**self.model_dump())


class RecurringOut(BaseModel):
    """A stored recurring item; ``next_due_date`` is its earliest planned row, if any."""

    id: int
    description: str
    amount_cents: int
    account_id: int
    category_id: int | None
    transfer_target_account_id: int | None
    interval: Interval
    day_of_month: int | None
    weekday: int | None
    start_date: date
    end_date: date | None
    active: bool
    next_due_date: date | None
    planned_count: int
    created_at: str


class ItemOut(BaseModel):
    """One occurrence; ``moved`` = changed by hand, generation leaves it alone."""

    id: int
    due_date: date  # declared before ``date``, whose name shadows the type below it
    date: date
    amount_cents: int
    account_id: int
    status: Status
    moved: bool
    transfer_group_id: str | None


class MoveIn(BaseModel):
    """New date and positive amount of a planned occurrence (the sign stays)."""

    date: date
    amount_cents: PositiveCents


class GenerationOut(BaseModel):
    """Rows created by a generation run and items that could not be generated."""

    created: int
    failed: list[int]


class ExpectedIn(BaseModel):
    """An expected one-off income or expense; the date must be after today."""

    date: date
    amount_cents: AmountCents
    account_id: int
    category_id: int | None = None
    text: Text = ""
    counterparty: Counterparty | None = None


class ExpectedOut(BaseModel):
    """A stored expected item (a planned booking)."""

    id: int
    date: date
    amount_cents: int
    account_id: int
    category_id: int | None
    text: str
    counterparty: str | None


def _recurring_out(item: service.Recurring) -> RecurringOut:
    return RecurringOut(
        id=item.id,
        description=item.description,
        amount_cents=item.amount_cents,
        account_id=item.account_id,
        category_id=item.category_id,
        transfer_target_account_id=item.transfer_target_account_id,
        interval=item.interval,
        day_of_month=item.day_of_month,
        weekday=item.weekday,
        start_date=item.start_date,
        end_date=item.end_date,
        active=item.active,
        next_due_date=item.next_due_date,
        planned_count=item.planned_count,
        created_at=item.created_at,
    )


def _item_out(item: service.PlannedItem) -> ItemOut:
    return ItemOut(
        id=item.id,
        date=item.date,
        due_date=item.due_date,
        amount_cents=item.amount_cents,
        account_id=item.account_id,
        status=item.status,
        moved=item.moved,
        transfer_group_id=item.transfer_group_id,
    )


def _expected_out(item: Transaction) -> ExpectedOut:
    return ExpectedOut(
        id=item.id,
        date=item.date,
        amount_cents=item.amount_cents,
        account_id=item.account_id,
        category_id=item.category_id,
        text=item.text,
        counterparty=item.counterparty,
    )


# --- fixed paths -------------------------------------------------------------------------------


@router.post("/generate")
def generate(conn: Conn) -> GenerationOut:
    """Fill the 12-month horizon of every active recurring item now (also runs at app start)."""
    report = service.generate_planned(conn)
    return GenerationOut(created=report.created, failed=report.failed)


@router.get("/expected")
def list_expected(conn: Conn) -> list[ExpectedOut]:
    """Expected one-off items, earliest first."""
    return [_expected_out(item) for item in service.list_expected(conn)]


@router.post("/expected", status_code=201)
def create_expected(body: ExpectedIn, conn: Conn) -> ExpectedOut:
    """Create an expected one-off item (a planned booking dated after today)."""
    try:
        item = service.create_expected(conn, BookingFields(**body.model_dump()))
    except Errors as exc:
        _fail(exc)
    return _expected_out(item)


@router.put("/items/{transaction_id}")
def move_item(transaction_id: int, body: MoveIn, conn: Conn) -> ItemOut:
    """Move a planned occurrence to another date and/or amount; generation keeps it from now on."""
    try:
        item = service.move_item(conn, transaction_id, body.date, body.amount_cents)
    except Errors as exc:
        _fail(exc)
    return _item_out(item)


# --- recurring items ---------------------------------------------------------------------------


@router.get("")
def list_recurring(conn: Conn) -> list[RecurringOut]:
    """All recurring items, active first."""
    return [_recurring_out(item) for item in service.list_recurring(conn)]


@router.post("", status_code=201)
def create_recurring(body: RecurringIn, conn: Conn) -> RecurringOut:
    """Create a recurring item; its planned rows for the next 12 months exist on return."""
    try:
        item = service.create_recurring(conn, body.fields())
    except Errors as exc:
        _fail(exc)
    return _recurring_out(item)


@router.get("/{recurring_id}")
def get_recurring(recurring_id: int, conn: Conn) -> RecurringOut:
    """One recurring item."""
    try:
        item = service.get_recurring(conn, recurring_id)
    except Errors as exc:
        _fail(exc)
    return _recurring_out(item)


@router.put("/{recurring_id}")
def update_recurring(recurring_id: int, body: RecurringIn, conn: Conn) -> RecurringOut:
    """Replace a recurring item; unmoved planned rows from today on follow the new values."""
    try:
        item = service.update_recurring(conn, recurring_id, body.fields())
    except Errors as exc:
        _fail(exc)
    return _recurring_out(item)


@router.post("/{recurring_id}/deactivate")
def deactivate(recurring_id: int, conn: Conn) -> RecurringOut:
    """Pause: every planned row of the item goes, booked rows stay."""
    try:
        item = service.set_active(conn, recurring_id, False)
    except Errors as exc:
        _fail(exc)
    return _recurring_out(item)


@router.post("/{recurring_id}/activate")
def activate(recurring_id: int, conn: Conn) -> RecurringOut:
    """Resume: planned rows are generated again from today."""
    try:
        item = service.set_active(conn, recurring_id, True)
    except Errors as exc:
        _fail(exc)
    return _recurring_out(item)


@router.get("/{recurring_id}/items")
def list_items(recurring_id: int, conn: Conn) -> list[ItemOut]:
    """The item's occurrences (planned, booked, skipped), by date."""
    try:
        items = service.list_items(conn, recurring_id)
    except Errors as exc:
        _fail(exc)
    return [_item_out(item) for item in items]


@router.delete("/{recurring_id}", status_code=204)
def delete_recurring(recurring_id: int, conn: Conn, request: Request) -> Response:
    """Delete a recurring item; planned rows go, booked rows stay as ordinary bookings.

    A ``DeleteGuard`` may refuse first, e.g. 409 for an item of the tax plan.
    """
    guards: tuple[DeleteGuard, ...] = getattr(request.app.state, "recurring_delete_guards", ())
    try:
        for guard in guards:
            guard(conn, recurring_id)
        service.delete_recurring(conn, recurring_id)
    except Errors as exc:
        _fail(exc)
    return Response(status_code=204)
