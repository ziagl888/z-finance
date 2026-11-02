"""Due items (UC-ZF-004): the weekly review of planned bookings as plain functions over a connection.

A planned row (recurring occurrence or expected one-off item) is *due* from its date on. ``list_due``
shows every planned item up to today + 14 days, overdue ones first; ``confirm`` turns one into a
real booking, ``skip`` marks one that will not happen, ``postpone`` moves it to a later date.
Nothing is booked automatically (UC assumption). After a confirmation or a skip the debts are
synced (``debts.sync_debts``), so the last instalment of a debt closes it.

This feature owns no table and writes no row of ``"transaction"`` itself: every change goes
through the transactions and recurring services, whose rules (bookable dates, transfer pairs, the
recurring key) therefore hold here too. The only SQL on the booking table is the read query of the
list.

Invariants:

* ``confirm`` replaces the planned row by a booked one that carries the item's ``recurring_id`` and
  ``due_date``; the recurring item itself is never written, so its other occurrences and the due
  date of the confirmed one stay as they were.
* A booking is never dated after today (a later date would make the transactions service store it
  as planned again); such a date is refused, and an item that is not due yet is booked today.
* A refused call changes nothing: ``confirm`` and ``skip`` run in one transaction, ``postpone``
  delegates to one service call that validates before it writes.
* A skipped row keeps its ``recurring_id`` and ``due_date``, so its due date is never generated
  again.

Errors are ``DueError`` subclasses carrying an ``N_()`` msgid, like the other features;
``AccountsError`` and ``TransactionsError`` from the services called here pass through.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from zfinance.db import transaction
from zfinance.domain.debts import service as debts
from zfinance.domain.recurring import service as recurring
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, Transaction
from zfinance.i18n import N_

#: Days ahead of today the list reaches (UC-ZF-004 step 1: "bis heute + 14 Tage").
HORIZON_DAYS = 14
#: An item overdue by more than this many days is highlighted (UC-ZF-004 extension 1a).
LONG_OVERDUE_DAYS = 30


# --- errors ------------------------------------------------------------------------------------


class DueError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class Conflict(DueError):
    """The request contradicts the state of the item (it is not planned)."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DueItem:
    """One planned occurrence to review (for a transfer: its outgoing leg).

    ``amount_cents`` is signed like a booking. ``days_overdue`` is 0 unless the date is before
    today; ``long_overdue`` is set above ``LONG_OVERDUE_DAYS``. ``confirm_date`` is the date a plain
    confirm books on: the planned date, or today when the item is not due yet.
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


# --- reading -----------------------------------------------------------------------------------

_DUE_SELECT = (
    'SELECT t.*, (SELECT o.account_id FROM "transaction" o'
    " WHERE o.transfer_group_id = t.transfer_group_id AND o.id != t.id) AS transfer_account_id"
    ' FROM "transaction" t'
    " WHERE t.status = 'planned' AND t.date <= ? AND (t.transfer_group_id IS NULL OR t.amount_cents < 0)"
    " ORDER BY t.date, t.id"
)


def list_due(conn: sqlite3.Connection, *, today: date | None = None, horizon_days: int = HORIZON_DAYS) -> list[DueItem]:
    """Planned items dated up to ``today + horizon_days``, oldest first (so overdue ones lead).

    A transfer is one entry (its outgoing leg). Booked and skipped rows, and planned rows that the
    CSV import replaced (they no longer exist as planned), are not listed (UC 2a).
    """
    today = today or date.today()
    rows = conn.execute(_DUE_SELECT, ((today + timedelta(days=horizon_days)).isoformat(),))  # nosec B608 — constant
    items: list[DueItem] = []
    for row in rows:
        planned_on = date.fromisoformat(row["date"])
        due = None if row["due_date"] is None else date.fromisoformat(row["due_date"])
        overdue = max(0, (today - planned_on).days)
        items.append(
            DueItem(
                id=row["id"],
                date=planned_on,
                due_date=due,
                amount_cents=row["amount_cents"],
                account_id=row["account_id"],
                transfer_account_id=row["transfer_account_id"],
                category_id=row["category_id"],
                text=row["text"],
                counterparty=row["counterparty"],
                recurring_id=row["recurring_id"],
                moved=bool(row["manually_moved"]) or (due is not None and due != planned_on),
                days_overdue=overdue,
                long_overdue=overdue > LONG_OVERDUE_DAYS,
                confirm_date=min(planned_on, today),
            )
        )
    return items


# --- changes -----------------------------------------------------------------------------------


def _planned(conn: sqlite3.Connection, planned_id: int) -> Transaction:
    """The planned row, or ``TransactionsError`` (unknown id) / ``Conflict`` (not planned)."""
    row = transactions.get_transaction(conn, planned_id)
    if row.status != "planned":
        raise Conflict(N_("Only planned items can be changed here."))
    return row


def confirm(
    conn: sqlite3.Connection,
    planned_id: int,
    *,
    amount_cents: int | None = None,
    on_date: date | None = None,
    today: date | None = None,
) -> Transaction:
    """Book a planned item and return the booking (UC steps 2-5).

    ``amount_cents`` is the positive amount to book (default: the planned one; the sign of the item
    stays), ``on_date`` the booking date (default: the planned date, at most today). The item's
    account, category, text, counterparty and memo carry over. The booking takes over the item's
    ``recurring_id`` and ``due_date`` through ``recurring.replace_planned``, so the recurring item
    is untouched and the due date counts as done; a transfer's other leg is booked mirrored.

    Refused, nothing changes: unknown id, item not planned (``Conflict``), amount ≤ 0, a date after
    today (postpone the item instead), and whatever the services refuse (archived account, date
    before the account's opening date).
    """
    today = today or date.today()
    planned = _planned(conn, planned_id)
    if amount_cents is not None and amount_cents <= 0:
        raise DueError(N_("Enter an amount greater than 0."))
    booked_on = min(planned.date, today) if on_date is None else on_date
    if booked_on > today:
        raise DueError(N_("A confirmed item needs a date of today or earlier; to wait, postpone it."))
    magnitude = abs(planned.amount_cents) if amount_cents is None else amount_cents
    fields = BookingFields(
        booked_on,
        magnitude if planned.amount_cents > 0 else -magnitude,
        planned.account_id,
        planned.category_id,
        planned.text,
        planned.counterparty,
        planned.memo,
    )
    with transaction(conn):
        real = transactions.create_transaction(conn, fields, today=today)
        booked = recurring.replace_planned(conn, planned.id, real.id)
        # A confirmed instalment lowers the remaining amount of its debt; the last one closes it.
        debts.sync_debts(conn, today=today)
    return booked


def skip(conn: sqlite3.Connection, planned_id: int, *, today: date | None = None) -> Transaction:
    """Skip a planned item that will not happen, e.g. a paused subscription (UC steps 6-7). Return it.

    The item becomes ``skipped`` through ``transactions.skip_planned`` (a transfer with both legs):
    it counts towards no balance and leaves the list. Its ``recurring_id`` and ``due_date`` stay, so
    the recurring item goes on with its other occurrences and never creates the skipped date again.
    A skipped instalment counts as done for its debt (``debts.sync_debts``), so the debt's end moves
    one due date later. Refused, nothing changes: unknown id, item not planned (``Conflict``), and
    an item with a receipt attached (UC-ZF-013 5b): the receipt shows the payment happened, so the
    item is confirmed instead.
    """
    if conn.execute("SELECT 1 FROM receipt WHERE transaction_id = ?", (planned_id,)).fetchone():
        raise Conflict(N_("A receipt is attached to this item, so it was paid: confirm it instead."))
    with transaction(conn):
        skipped = transactions.skip_planned(conn, planned_id)
        debts.sync_debts(conn, today=today or date.today())
    return skipped


def postpone(conn: sqlite3.Connection, planned_id: int, new_date: date, *, today: date | None = None) -> Transaction:
    """Move a planned item to a later date; it stays planned (UC 4a). Return the item.

    A recurring occurrence moves through ``recurring.move_item``, which flags it ``manually_moved``
    so generation and editing the recurring item leave it alone; an expected one-off item is an
    ordinary booking and moves through ``transactions.update_transaction`` (nothing regenerates it,
    so it needs no flag). A transfer moves with its pair. ``new_date`` must be after today: a
    payment that already happened is confirmed, not postponed.
    """
    today = today or date.today()
    planned = _planned(conn, planned_id)
    if new_date <= today:
        raise DueError(N_("Choose a date after today to postpone an item; a payment that happened is confirmed."))
    if planned.recurring_id is not None:
        recurring.move_item(conn, planned.id, new_date, abs(planned.amount_cents), today=today)
    else:
        fields = BookingFields(
            new_date,
            planned.amount_cents,
            planned.account_id,
            planned.category_id,
            planned.text,
            planned.counterparty,
            planned.memo,
        )
        transactions.update_transaction(conn, planned.id, fields, today=today)
    return transactions.get_transaction(conn, planned.id)
