"""Bookings (UC-ZF-002): income, expense and transfers as plain functions over a connection.

A booking is one row of the ``transaction`` table: a signed amount in cents (income positive,
expense negative, never 0) on an account and a day, with an optional category, a text, an
optional counterparty and memo. A transfer is two rows sharing a ``transfer_group_id``: minus on
the source account, plus on the target account, so the total over all accounts does not change
(also for an owner draw between a business and a private account, UC TBD-6).

Rules every write follows:

* Each insert and each change of date or account passes ``accounts.ensure_bookable`` (archived
  account, date before the opening date).
* A manual booking dated after ``today`` is ``planned``, otherwise ``booked``; the status is
  derived again whenever a manual booking is saved. ``skipped`` rows and rows written by other
  sources (import, recurring, receipt, allocation) keep their status.
* The two legs of a transfer change and disappear together.

Suggestions come from the history: the most recent non-transfer booking per text
(case-insensitive) gives category and counterparty.

Errors are ``TransactionsError`` subclasses carrying an ``N_()`` msgid and its parameters, like
``AccountsError`` from ``ensure_bookable`` and ``get_category``; the API translates both.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from zfinance.db import transaction
from zfinance.domain.accounts import service as accounts
from zfinance.i18n import N_

Status = Literal["booked", "planned", "skipped"]
Source = Literal["manual", "import", "recurring", "receipt", "allocation"]

#: Most suggestions ``suggest`` returns.
SUGGESTION_LIMIT = 8


# --- errors ------------------------------------------------------------------------------------


class TransactionsError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``;
    ``str(exc)`` is the English text.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(TransactionsError):
    """The booking or transfer does not exist."""

    status_code = 404


class Conflict(TransactionsError):
    """The request contradicts the rules of a transfer."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BookingFields:
    """What the user enters for an income or expense (create and full update)."""

    date: date
    amount_cents: int
    account_id: int
    category_id: int | None = None
    text: str = ""
    counterparty: str | None = None
    memo: str | None = None


@dataclass(frozen=True)
class TransferFields:
    """What the user enters for a transfer; ``amount_cents`` is positive (the direction is from → to)."""

    date: date
    amount_cents: int
    from_account_id: int
    to_account_id: int
    category_id: int | None = None
    text: str = ""
    memo: str | None = None


@dataclass(frozen=True, kw_only=True)
class Transaction(BookingFields):
    """A stored booking. ``transfer_account_id`` is the other leg's account for a transfer leg."""

    id: int
    status: Status
    source: Source
    transfer_group_id: str | None = None
    transfer_account_id: int | None = None
    recurring_id: int | None = None
    import_hash: str | None = None
    created_at: str


@dataclass(frozen=True)
class Suggestion:
    """Category and counterparty last used for a booking text, and the day of that booking."""

    text: str
    category_id: int | None
    counterparty: str | None
    last_date: date


_SELECT = (
    'SELECT t.*, (SELECT o.account_id FROM "transaction" o'
    " WHERE o.transfer_group_id = t.transfer_group_id AND o.id != t.id) AS transfer_account_id"
    ' FROM "transaction" t'
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _transaction(row: sqlite3.Row) -> Transaction:
    return Transaction(
        id=row["id"],
        date=date.fromisoformat(row["date"]),
        amount_cents=row["amount_cents"],
        account_id=row["account_id"],
        category_id=row["category_id"],
        text=row["text"],
        counterparty=row["counterparty"],
        memo=row["memo"],
        status=row["status"],
        source=row["source"],
        transfer_group_id=row["transfer_group_id"],
        transfer_account_id=row["transfer_account_id"],
        recurring_id=row["recurring_id"],
        import_hash=row["import_hash"],
        created_at=row["created_at"],
    )


def status_for(booking_date: date, today: date) -> Status:
    """Status of a manual booking: ``planned`` for a future date (UC 3c), else ``booked``."""
    return "planned" if booking_date > today else "booked"


def _optional(value: str | None) -> str | None:
    """Blank optional text is stored as NULL."""
    if value is None:
        return None
    return value.strip() or None


def _check_amount(cents: int) -> None:
    if cents == 0:
        raise TransactionsError(N_("Enter an amount other than 0."))


def _check_category(conn: sqlite3.Connection, category_id: int | None) -> None:
    if category_id is not None:
        accounts.get_category(conn, category_id)


# --- reading -----------------------------------------------------------------------------------


def get_transaction(conn: sqlite3.Connection, transaction_id: int) -> Transaction:
    """Return the booking or raise ``NotFound``."""
    row = conn.execute(f"{_SELECT} WHERE t.id = ?", (transaction_id,)).fetchone()  # nosec B608 — constant
    if row is None:
        raise NotFound(N_("Booking not found."))
    return _transaction(row)


def list_transactions(
    conn: sqlite3.Connection, *, limit: int = 100, account_id: int | None = None
) -> list[Transaction]:
    """The most recent bookings (by date, then entry order), optionally of one account."""
    rows = conn.execute(
        f"{_SELECT} WHERE ? IS NULL OR t.account_id = ? ORDER BY t.date DESC, t.id DESC LIMIT ?",  # nosec B608 — constant
        (account_id, account_id, limit),
    )
    return [_transaction(row) for row in rows]


def _group(conn: sqlite3.Connection, group_id: str) -> list[Transaction]:
    rows = conn.execute(f"{_SELECT} WHERE t.transfer_group_id = ? ORDER BY t.amount_cents", (group_id,))  # nosec B608 — constant
    return [_transaction(row) for row in rows]


def get_transfer(conn: sqlite3.Connection, group_id: str) -> tuple[Transaction, Transaction]:
    """The two legs of a transfer as (source leg, target leg), or ``NotFound``."""
    legs = _group(conn, group_id)
    if len(legs) != 2:
        raise NotFound(N_("Transfer not found."))
    return legs[0], legs[1]


# --- bookings ----------------------------------------------------------------------------------


def _insert(
    conn: sqlite3.Connection,
    *,
    fields: BookingFields,
    status: Status,
    group_id: str | None = None,
) -> int:
    row = conn.execute(
        'INSERT INTO "transaction" (date, amount_cents, account_id, category_id, text, counterparty, memo,'
        " status, source, transfer_group_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)"
        " RETURNING id",
        (
            fields.date.isoformat(),
            fields.amount_cents,
            fields.account_id,
            fields.category_id,
            fields.text.strip(),
            _optional(fields.counterparty),
            _optional(fields.memo),
            status,
            group_id,
            _now(),
        ),
    ).fetchone()
    return int(row["id"])


def _write(conn: sqlite3.Connection, transaction_id: int, fields: BookingFields, status: Status) -> None:
    conn.execute(
        'UPDATE "transaction" SET date = ?, amount_cents = ?, account_id = ?, category_id = ?, text = ?,'
        " counterparty = ?, memo = ?, status = ? WHERE id = ?",
        (
            fields.date.isoformat(),
            fields.amount_cents,
            fields.account_id,
            fields.category_id,
            fields.text.strip(),
            _optional(fields.counterparty),
            _optional(fields.memo),
            status,
            transaction_id,
        ),
    )


def _saved_status(existing: Transaction, booking_date: date, today: date) -> Status:
    """Manual rows follow their date; skipped rows and other sources keep their status."""
    if existing.source == "manual" and existing.status != "skipped":
        return status_for(booking_date, today)
    return existing.status


def create_transaction(conn: sqlite3.Connection, fields: BookingFields, *, today: date | None = None) -> Transaction:
    """Store an income or expense entered by hand and return it (``planned`` if dated after today)."""
    _check_amount(fields.amount_cents)
    accounts.ensure_bookable(conn, fields.account_id, fields.date)
    _check_category(conn, fields.category_id)
    status = status_for(fields.date, today or date.today())
    return get_transaction(conn, _insert(conn, fields=fields, status=status))


def update_transaction(
    conn: sqlite3.Connection, transaction_id: int, fields: BookingFields, *, today: date | None = None
) -> Transaction:
    """Replace a booking's fields and return it.

    A new date or account passes ``ensure_bookable`` again. For a leg of a transfer the whole
    transfer is rewritten: this leg gets ``fields`` (its sign says whether it is the source or the
    target), the other leg keeps its account and gets the mirrored amount (UC 3a).
    """
    existing = get_transaction(conn, transaction_id)
    _check_amount(fields.amount_cents)
    if existing.transfer_group_id is not None and existing.transfer_account_id is not None:
        other = existing.transfer_account_id
        source, target = (fields.account_id, other) if fields.amount_cents < 0 else (other, fields.account_id)
        update_transfer(
            conn,
            existing.transfer_group_id,
            TransferFields(
                date=fields.date,
                amount_cents=abs(fields.amount_cents),
                from_account_id=source,
                to_account_id=target,
                category_id=fields.category_id,
                text=fields.text,
                memo=fields.memo,
            ),
            today=today,
        )
        return get_transaction(conn, transaction_id)
    if fields.date != existing.date or fields.account_id != existing.account_id:
        accounts.ensure_bookable(conn, fields.account_id, fields.date)
    _check_category(conn, fields.category_id)
    _write(conn, transaction_id, fields, _saved_status(existing, fields.date, today or date.today()))
    return get_transaction(conn, transaction_id)


def delete_transaction(conn: sqlite3.Connection, transaction_id: int) -> int:
    """Delete a booking; a transfer leg takes its other leg with it (UC 3a). Return rows deleted.

    A planned occurrence of a recurring item is skipped instead (``skip_planned``) and ``0`` is
    returned: deleting it would free its (recurring_id, due_date) key, and the next generation run
    would create it again. An occurrence that is already skipped stays as it is for the same reason.
    """
    existing = get_transaction(conn, transaction_id)
    if existing.recurring_id is not None and existing.status in ("planned", "skipped"):
        if existing.status == "planned":
            skip_planned(conn, transaction_id)
        return 0
    if existing.transfer_group_id is not None:
        cursor = conn.execute('DELETE FROM "transaction" WHERE transfer_group_id = ?', (existing.transfer_group_id,))
    else:
        cursor = conn.execute('DELETE FROM "transaction" WHERE id = ?', (transaction_id,))
    return cursor.rowcount


def skip_planned(conn: sqlite3.Connection, transaction_id: int) -> Transaction:
    """Mark a planned booking as ``skipped`` (it did not happen, UC-ZF-004) and return it.

    A skipped row counts towards no balance and is not listed as due. ``recurring_id`` and
    ``due_date`` stay, so the recurring engine does not create that due date again. A transfer leg
    takes its other leg with it, both in one transaction. Only ``planned`` rows can be skipped;
    anything else raises ``Conflict`` and changes nothing. Skipping is final here: a skipped row
    stays skipped when it is saved again (``_saved_status``).
    """
    existing = get_transaction(conn, transaction_id)
    if existing.status != "planned":
        raise Conflict(N_("Only planned items can be changed here."))
    with transaction(conn):
        if existing.transfer_group_id is not None:
            conn.execute(
                "UPDATE \"transaction\" SET status = 'skipped' WHERE transfer_group_id = ? AND status = 'planned'",
                (existing.transfer_group_id,),
            )
        else:
            conn.execute("UPDATE \"transaction\" SET status = 'skipped' WHERE id = ?", (transaction_id,))
    return get_transaction(conn, transaction_id)


# --- transfers ---------------------------------------------------------------------------------


def _check_transfer(conn: sqlite3.Connection, fields: TransferFields) -> None:
    if fields.amount_cents <= 0:
        raise TransactionsError(N_("Enter a transfer amount greater than 0."))
    if fields.from_account_id == fields.to_account_id:
        raise Conflict(N_("Choose two different accounts for a transfer."))
    _check_category(conn, fields.category_id)


def _legs(fields: TransferFields) -> tuple[BookingFields, BookingFields]:
    def leg(account_id: int, cents: int) -> BookingFields:
        return BookingFields(fields.date, cents, account_id, fields.category_id, fields.text, None, fields.memo)

    return leg(fields.from_account_id, -fields.amount_cents), leg(fields.to_account_id, fields.amount_cents)


def create_transfer(
    conn: sqlite3.Connection, fields: TransferFields, *, today: date | None = None
) -> tuple[Transaction, Transaction]:
    """Store a transfer as two linked bookings (source -amount, target +amount) in one transaction."""
    _check_transfer(conn, fields)
    accounts.ensure_bookable(conn, fields.from_account_id, fields.date)
    accounts.ensure_bookable(conn, fields.to_account_id, fields.date)
    status = status_for(fields.date, today or date.today())
    group_id = uuid.uuid4().hex
    source, target = _legs(fields)
    with transaction(conn):
        _insert(conn, fields=source, status=status, group_id=group_id)
        _insert(conn, fields=target, status=status, group_id=group_id)
    return get_transfer(conn, group_id)


def update_transfer(
    conn: sqlite3.Connection, group_id: str, fields: TransferFields, *, today: date | None = None
) -> tuple[Transaction, Transaction]:
    """Rewrite both legs of a transfer; a leg whose date or account changes passes ``ensure_bookable``."""
    old_source, old_target = get_transfer(conn, group_id)
    _check_transfer(conn, fields)
    new_source, new_target = _legs(fields)
    today = today or date.today()
    for old, new in ((old_source, new_source), (old_target, new_target)):
        if new.date != old.date or new.account_id != old.account_id:
            accounts.ensure_bookable(conn, new.account_id, new.date)
    with transaction(conn):
        _write(conn, old_source.id, new_source, _saved_status(old_source, fields.date, today))
        _write(conn, old_target.id, new_target, _saved_status(old_target, fields.date, today))
    return get_transfer(conn, group_id)


# --- suggestions -------------------------------------------------------------------------------


def suggest(conn: sqlite3.Connection, query: str, *, limit: int = SUGGESTION_LIMIT) -> list[Suggestion]:
    """Earlier booking texts matching ``query`` with the category and counterparty last used for them.

    Texts compare case-insensitively; per text the most recently entered booking wins (UC AK3:
    from the second time on, the last category used for "Billa" is offered). Order: exact match,
    then texts starting with the query, then texts containing it, most recent first in each group.
    Transfers are left out; they have no counterparty and their own form.
    """
    needle = query.strip().casefold()
    if not needle:
        return []
    rows = conn.execute(
        'SELECT text, category_id, counterparty, date FROM "transaction" WHERE id IN'
        " (SELECT max(id) FROM \"transaction\" WHERE text != '' AND transfer_group_id IS NULL GROUP BY text)"
        " ORDER BY id DESC"
    )
    latest: dict[str, Suggestion] = {}
    for row in rows:
        key = row["text"].casefold()
        if needle in key and key not in latest:
            latest[key] = Suggestion(
                row["text"], row["category_id"], row["counterparty"], date.fromisoformat(row["date"])
            )

    def rank(item: tuple[str, Suggestion]) -> int:
        key = item[0]
        return 0 if key == needle else 1 if key.startswith(needle) else 2

    # sorted() is stable, so recency order survives inside each rank.
    return [suggestion for _, suggestion in sorted(latest.items(), key=rank)][:limit]
