"""Recurring items and expected one-off items (UC-ZF-003) as plain functions over a connection.

A recurring item (table ``recurring``) is a template: text, amount, account, category, interval
and day. The generation engine (``generate_planned``) turns every due date within the next 12
months into a planned row of the booking table (``source='recurring'``, ``status='planned'``,
``recurring_id`` and ``due_date`` set). A recurring transfer (loan or savings instalment,
``transfer_target_account_id`` set) becomes a transfer pair, created through the transactions
service like a hand-entered transfer.

An expected one-off item is a manual booking dated after today: the transactions service already
stores it as ``planned`` without a ``recurring_id``; ``create_expected`` only refuses dates that
would make it a booked row.

Invariants:

* Generation is idempotent: the key (recurring_id, due_date) exists at most once (unique index,
  outgoing leg for transfers), and the engine never creates a key that exists, whatever the row's
  status, date or amount is now. Running it twice changes nothing.
* A row the user moved or changed (``manually_moved = 1`` or ``date != due_date``) is never
  rewritten or deleted by generation or by editing the recurring item; only deactivation, the end
  date and deleting the item remove planned rows regardless.
* Only ``planned`` rows are ever deleted here; booked and skipped rows stay.
* ``replace_planned`` hands the (recurring_id, due_date) key to the real booking, so the due date
  counts as done and is not generated again.

Errors are ``RecurringError`` subclasses carrying an ``N_()`` msgid, like the other features;
``AccountsError`` and ``TransactionsError`` from the services called here pass through.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from zfinance.config import Settings
from zfinance.db import connect, transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.recurring.schedule import Interval, Schedule, add_months, due_dates
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransactionsError, TransferFields
from zfinance.i18n import N_

logger = logging.getLogger(__name__)

#: Months ahead the engine fills (UC-ZF-003 assumption: fixed, not configurable).
HORIZON_MONTHS = 12

# A planned row counts as moved when the user flagged it or its date left the due date.
_MOVED = "(manually_moved = 1 OR date != due_date)"
# One row per occurrence: a plain row, or the outgoing leg of a transfer (same filter as the index).
_OCCURRENCE = "(transfer_group_id IS NULL OR amount_cents < 0)"


# --- errors ------------------------------------------------------------------------------------


class RecurringError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(RecurringError):
    """The recurring item or planned row does not exist."""

    status_code = 404


class Conflict(RecurringError):
    """The request contradicts the state of the rows involved."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecurringFields:
    """What the user enters for a recurring item (create and full update).

    ``amount_cents`` is signed for income/expense and positive for a transfer to
    ``transfer_target_account_id``.
    """

    description: str
    amount_cents: int
    account_id: int
    interval: Interval
    start_date: date
    category_id: int | None = None
    transfer_target_account_id: int | None = None
    day_of_month: int | None = None
    weekday: int | None = None
    end_date: date | None = None

    def schedule(self) -> Schedule:
        """The due-date rule of these fields."""
        return Schedule(self.interval, self.start_date, self.end_date, self.day_of_month, self.weekday)


@dataclass(frozen=True, kw_only=True)
class Recurring(RecurringFields):
    """A stored recurring item with its next open due date and number of planned rows."""

    id: int
    active: bool
    pot_id: int | None
    created_at: str
    next_due_date: date | None
    planned_count: int


@dataclass(frozen=True)
class PlannedItem:
    """One occurrence of a recurring item (for a transfer: its outgoing leg)."""

    id: int
    due_date: date  # declared before ``date``, whose name shadows the type below it
    date: date
    amount_cents: int
    account_id: int
    status: transactions.Status
    moved: bool
    transfer_group_id: str | None


@dataclass(frozen=True)
class GenerationReport:
    """Result of a generation run: rows created and recurring items that could not be generated."""

    created: int
    failed: list[int]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


_SELECT = (
    "SELECT r.*,"
    " (SELECT min(t.date) FROM \"transaction\" t WHERE t.recurring_id = r.id AND t.status = 'planned') AS next_due,"
    " (SELECT count(*) FROM \"transaction\" t WHERE t.recurring_id = r.id AND t.status = 'planned'"
    " AND (t.transfer_group_id IS NULL OR t.amount_cents < 0)) AS planned_count"
    " FROM recurring r"
)


def _recurring(row: sqlite3.Row) -> Recurring:
    return Recurring(
        id=row["id"],
        description=row["description"],
        amount_cents=row["amount_cents"],
        account_id=row["account_id"],
        interval=row["interval"],
        start_date=date.fromisoformat(row["start_date"]),
        category_id=row["category_id"],
        transfer_target_account_id=row["transfer_target_account_id"],
        day_of_month=row["day_of_month"],
        weekday=row["weekday"],
        end_date=_day(row["end_date"]),
        active=bool(row["active"]),
        pot_id=row["pot_id"],
        created_at=row["created_at"],
        next_due_date=_day(row["next_due"]),
        planned_count=row["planned_count"],
    )


# --- reading -----------------------------------------------------------------------------------


def get_recurring(conn: sqlite3.Connection, recurring_id: int) -> Recurring:
    """Return the recurring item or raise ``NotFound``."""
    row = conn.execute(f"{_SELECT} WHERE r.id = ?", (recurring_id,)).fetchone()  # nosec B608 — constant
    if row is None:
        raise NotFound(N_("Recurring item not found."))
    return _recurring(row)


def list_recurring(conn: sqlite3.Connection) -> list[Recurring]:
    """All recurring items, active ones first, then by text."""
    rows = conn.execute(f"{_SELECT} ORDER BY r.active DESC, r.description COLLATE NOCASE, r.id")  # nosec B608 — constant
    return [_recurring(row) for row in rows]


def list_items(conn: sqlite3.Connection, recurring_id: int) -> list[PlannedItem]:
    """The occurrences of a recurring item (planned, booked and skipped), by date."""
    get_recurring(conn, recurring_id)
    rows = conn.execute(
        f'SELECT *, {_MOVED} AS moved FROM "transaction" WHERE recurring_id = ? AND {_OCCURRENCE}'  # noqa: S608  # nosec B608 — constants
        " ORDER BY date, id",
        (recurring_id,),
    )
    return [
        PlannedItem(
            id=row["id"],
            date=date.fromisoformat(row["date"]),
            due_date=date.fromisoformat(row["due_date"]),
            amount_cents=row["amount_cents"],
            account_id=row["account_id"],
            status=row["status"],
            moved=bool(row["moved"]),
            transfer_group_id=row["transfer_group_id"],
        )
        for row in rows
    ]


# --- recurring items ---------------------------------------------------------------------------


def _check(conn: sqlite3.Connection, fields: RecurringFields) -> RecurringFields:
    """Validate and normalize the fields (text trimmed, the unused day field cleared)."""
    description = fields.description.strip()
    if not description:
        raise RecurringError(N_("Enter a text for the recurring item."))
    if fields.amount_cents == 0:
        raise RecurringError(N_("Enter an amount other than 0."))
    if fields.interval == "weekly":
        if fields.weekday is None or not 0 <= fields.weekday <= 6:
            raise RecurringError(N_("Choose a weekday."))
        fields = replace(fields, day_of_month=None)
    else:
        if fields.day_of_month is None or not 1 <= fields.day_of_month <= 31:
            raise RecurringError(N_("Choose a day of the month between 1 and 31."))
        fields = replace(fields, weekday=None)
    if fields.end_date is not None and fields.end_date < fields.start_date:
        raise RecurringError(N_("The end date must not be before the start date."))
    for account_id in (fields.account_id, fields.transfer_target_account_id):
        if account_id is None:
            continue
        account = accounts.get_account(conn, account_id)
        if account.archived_at is not None:
            raise Conflict(N_("The account %(name)s is archived."), name=account.name)
    if fields.transfer_target_account_id is not None:
        if fields.amount_cents < 0:
            raise RecurringError(N_("Enter a transfer amount greater than 0."))
        if fields.transfer_target_account_id == fields.account_id:
            raise Conflict(N_("Choose two different accounts for a transfer."))
    if fields.category_id is not None:
        accounts.get_category(conn, fields.category_id)
    return replace(fields, description=description)


def _delete_planned(
    conn: sqlite3.Connection, recurring_id: int, condition: str = "1", params: tuple[Any, ...] = ()
) -> int:
    """Delete planned rows of the item matching ``condition``; transfer legs go as pairs."""
    groups = conn.execute(
        f"SELECT DISTINCT transfer_group_id FROM \"transaction\" WHERE recurring_id = ? AND status = 'planned'"  # noqa: S608  # nosec B608 — constant conditions
        f" AND transfer_group_id IS NOT NULL AND ({condition})",
        (recurring_id, *params),
    ).fetchall()
    deleted = conn.execute(
        f"DELETE FROM \"transaction\" WHERE recurring_id = ? AND status = 'planned' AND ({condition})",  # noqa: S608  # nosec B608
        (recurring_id, *params),
    ).rowcount
    for group in groups:
        deleted += conn.execute(
            "DELETE FROM \"transaction\" WHERE transfer_group_id = ? AND status = 'planned'", (group[0],)
        ).rowcount
    return deleted


def create_recurring(conn: sqlite3.Connection, fields: RecurringFields, *, today: date | None = None) -> Recurring:
    """Store a recurring item and generate its planned rows for the horizon right away (UC step 2)."""
    fields = _check(conn, fields)
    row = conn.execute(
        "INSERT INTO recurring (description, amount_cents, account_id, category_id, transfer_target_account_id,"
        " interval, day_of_month, weekday, start_date, end_date, active, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?) RETURNING id",
        (
            fields.description,
            fields.amount_cents,
            fields.account_id,
            fields.category_id,
            fields.transfer_target_account_id,
            fields.interval,
            fields.day_of_month,
            fields.weekday,
            fields.start_date.isoformat(),
            None if fields.end_date is None else fields.end_date.isoformat(),
            _now(),
        ),
    ).fetchone()
    recurring_id = int(row["id"])
    generate_planned(conn, today=today, recurring_id=recurring_id)
    return get_recurring(conn, recurring_id)


def update_recurring(
    conn: sqlite3.Connection, recurring_id: int, fields: RecurringFields, *, today: date | None = None
) -> Recurring:
    """Replace a recurring item's fields and bring its planned rows in line.

    Planned rows from today on that the user did not move are regenerated with the new values;
    planned rows outside the new start/end dates go, moved or not; overdue planned rows, moved
    rows inside the dates and booked rows stay (UC 1b, 2b).
    """
    existing = get_recurring(conn, recurring_id)
    fields = _check(conn, fields)
    today = today or date.today()
    end = None if fields.end_date is None else fields.end_date.isoformat()
    with transaction(conn):
        conn.execute(
            "UPDATE recurring SET description = ?, amount_cents = ?, account_id = ?, category_id = ?,"
            " transfer_target_account_id = ?, interval = ?, day_of_month = ?, weekday = ?, start_date = ?,"
            " end_date = ? WHERE id = ?",
            (
                fields.description,
                fields.amount_cents,
                fields.account_id,
                fields.category_id,
                fields.transfer_target_account_id,
                fields.interval,
                fields.day_of_month,
                fields.weekday,
                fields.start_date.isoformat(),
                end,
                recurring_id,
            ),
        )
        _delete_planned(
            conn,
            recurring_id,
            f"(NOT {_MOVED} AND due_date >= ?) OR due_date < ? OR (? IS NOT NULL AND due_date > ?)",
            (today.isoformat(), fields.start_date.isoformat(), end, end),
        )
    if existing.active:
        generate_planned(conn, today=today, recurring_id=recurring_id)
    return get_recurring(conn, recurring_id)


def set_active(conn: sqlite3.Connection, recurring_id: int, active: bool, *, today: date | None = None) -> Recurring:
    """Pause or resume a recurring item.

    Pausing deletes every planned row of the item, moved ones included; booked and skipped rows
    stay (UC 1c, AK3). Resuming generates the horizon again from today.
    """
    get_recurring(conn, recurring_id)
    with transaction(conn):
        conn.execute("UPDATE recurring SET active = ? WHERE id = ?", (int(active), recurring_id))
        if not active:
            _delete_planned(conn, recurring_id)
    if active:
        generate_planned(conn, today=today, recurring_id=recurring_id)
    return get_recurring(conn, recurring_id)


def delete_recurring(conn: sqlite3.Connection, recurring_id: int) -> None:
    """Delete a recurring item: its planned rows go, booked and skipped rows stay unlinked."""
    get_recurring(conn, recurring_id)
    with transaction(conn):
        _delete_planned(conn, recurring_id)
        conn.execute(
            'UPDATE "transaction" SET recurring_id = NULL, due_date = NULL WHERE recurring_id = ?', (recurring_id,)
        )
        conn.execute("DELETE FROM recurring WHERE id = ?", (recurring_id,))


# --- generation --------------------------------------------------------------------------------


def _existing_due_dates(conn: sqlite3.Connection, recurring_id: int) -> set[date]:
    rows = conn.execute(
        'SELECT due_date FROM "transaction" WHERE recurring_id = ? AND due_date IS NOT NULL', (recurring_id,)
    )
    return {date.fromisoformat(row[0]) for row in rows}


def _tag(conn: sqlite3.Connection, ids: list[int], recurring_id: int, due: date) -> None:
    """Mark fresh rows as the planned occurrence ``due`` of the item."""
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE \"transaction\" SET status = 'planned', source = 'recurring', recurring_id = ?, due_date = ?"  # noqa: S608  # nosec B608 — placeholders only
        f" WHERE id IN ({marks})",
        (recurring_id, due.isoformat(), *ids),
    )


def _create_occurrence(conn: sqlite3.Connection, item: Recurring, due: date, today: date) -> None:
    """One planned row (or transfer pair) for ``due``, written through the transactions service."""
    with transaction(conn):
        if item.transfer_target_account_id is None:
            fields = BookingFields(due, item.amount_cents, item.account_id, item.category_id, item.description)
            ids = [transactions.create_transaction(conn, fields, today=today).id]
        else:
            # create_transfer's own transaction runs as a savepoint inside this one.
            legs = transactions.create_transfer(
                conn,
                TransferFields(
                    due,
                    item.amount_cents,
                    item.account_id,
                    item.transfer_target_account_id,
                    item.category_id,
                    item.description,
                ),
                today=today,
            )
            ids = [leg.id for leg in legs]
        _tag(conn, ids, item.id, due)


def _generate_one(conn: sqlite3.Connection, item: Recurring, first: date, last: date, today: date) -> int:
    existing = _existing_due_dates(conn, item.id)
    account = accounts.get_account(conn, item.account_id)
    opening = account.opening_date
    if item.transfer_target_account_id is not None:
        opening = max(opening, accounts.get_account(conn, item.transfer_target_account_id).opening_date)
    created = 0
    for due in due_dates(item.schedule(), first, last):
        # Before an opening date nothing can be booked (UC-ZF-001 1a): such due dates do not exist.
        if due in existing or due < opening:
            continue
        _create_occurrence(conn, item, due, today)
        created += 1
    return created


def generate_planned(
    conn: sqlite3.Connection,
    *,
    today: date | None = None,
    horizon_months: int = HORIZON_MONTHS,
    recurring_id: int | None = None,
) -> GenerationReport:
    """Create the missing planned rows of active recurring items from today to the horizon.

    Every due date in the half-open window ``[today, today + horizon_months)`` without a row
    carrying its (recurring_id, due_date) key gets one, dated on the due date. The window's end is
    exclusive, so a due date that falls on today is not also generated a year ahead: an item holds
    each anniversary at most once in the window (T-2026-CU-9130-047). Earlier due dates are not
    created (a new item starting in the past does not produce overdue rows). Idempotent: a second
    run creates nothing (UC 2a). ``recurring_id`` limits the run to one item.

    An item whose rows cannot be written (an account archived since, a booking rule refusing the
    date) is logged, listed in ``failed`` and skipped; the others are generated. Rows already
    created for it in this run stay.
    """
    today = today or date.today()
    # due_dates() takes an inclusive bound: the day before the horizon makes the window half-open.
    last = add_months(today, horizon_months) - timedelta(days=1)
    items = [item for item in list_recurring(conn) if item.active]
    if recurring_id is not None:
        items = [item for item in items if item.id == recurring_id]
    created = 0
    failed: list[int] = []
    for item in items:
        try:
            created += _generate_one(conn, item, today, last, today)
        except (AccountsError, TransactionsError) as exc:
            logger.warning("recurring item %s not generated: %s", item.id, exc)
            failed.append(item.id)
    if created or failed:
        logger.info("recurring generation: %s rows created, %s items failed", created, len(failed))
    return GenerationReport(created=created, failed=failed)


def run_startup_generation(settings: Settings, *, today: date | None = None) -> GenerationReport | None:
    """Fill the horizon at app start (UC step 5). Never raises: a failure is logged, the app starts."""
    try:
        conn = connect(settings.db_path)
        try:
            return generate_planned(conn, today=today)
        finally:
            conn.close()
    except Exception:
        logger.exception("recurring generation at startup failed; the app starts without it")
        return None


# --- planned rows ------------------------------------------------------------------------------


def _planned_row(conn: sqlite3.Connection, transaction_id: int) -> sqlite3.Row:
    row = conn.execute('SELECT * FROM "transaction" WHERE id = ?', (transaction_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Planned item not found."))
    if row["status"] != "planned":
        raise Conflict(N_("Only planned items can be changed here."))
    return row


def move_item(
    conn: sqlite3.Connection, transaction_id: int, new_date: date, amount_cents: int, *, today: date | None = None
) -> PlannedItem:
    """Change date and amount of one planned occurrence and protect it from regeneration (UC 2b).

    ``amount_cents`` is the positive amount; the sign stays that of the occurrence. A transfer leg
    moves with its pair. The row keeps its due date, so the schedule date counts as taken.
    """
    row = _planned_row(conn, transaction_id)
    if row["recurring_id"] is None:
        raise Conflict(N_("This planned item does not belong to a recurring item."))
    if amount_cents <= 0:
        raise RecurringError(N_("Enter an amount greater than 0."))
    signed = amount_cents if row["amount_cents"] > 0 else -amount_cents
    fields = BookingFields(
        new_date, signed, row["account_id"], row["category_id"], row["text"], row["counterparty"], row["memo"]
    )
    group_id = row["transfer_group_id"]
    with transaction(conn):
        # For a transfer leg update_transaction rewrites both legs (as a savepoint of this one).
        transactions.update_transaction(conn, transaction_id, fields, today=today)
        conn.execute(
            'UPDATE "transaction" SET manually_moved = 1 WHERE id = ? OR (? IS NOT NULL AND transfer_group_id = ?)',
            (transaction_id, group_id, group_id),
        )
    return next(
        item
        for item in list_items(conn, row["recurring_id"])
        if item.id == transaction_id or (group_id is not None and item.transfer_group_id == group_id)
    )


def replace_planned(conn: sqlite3.Connection, planned_id: int, real_txn_id: int) -> transactions.Transaction:
    """Replace a planned row by a real booking and return the real booking (contract for UC-ZF-005).

    The CSV import (task -011/-012) calls this when an imported booking matches a planned row; the
    matching itself (account, amount ±5 %, date ±5 days) is the import's job. Effects, in one
    database transaction:

    * A receipt attached to the planned row (UC-ZF-013 5b) moves to the real booking.
    * The planned row is deleted.
    * The real booking takes over the planned row's ``recurring_id`` and ``due_date``: the
      recurring item remembers the due date as done and generation never creates it again.
      For an expected one-off item (no ``recurring_id``) nothing is carried over.
    * If the planned row is a leg of a transfer, the real booking becomes that leg (it joins the
      ``transfer_group_id``), and the other leg is booked with the real booking's date and the
      mirrored amount, so the transfer still sums to 0.

    Refused (``Conflict``, nothing changes): the planned row is not ``planned``; the real booking
    is not ``booked``, is itself a transfer leg or already linked to a recurring item; the two rows
    are on different accounts; for a transfer leg, the amounts have opposite signs. The other leg's
    account must accept the date (``ensure_bookable``). ``NotFound`` for an unknown id.
    """
    planned = _planned_row(conn, planned_id)
    real = transactions.get_transaction(conn, real_txn_id)
    if real.id == planned_id or real.status != "booked":
        raise Conflict(N_("Only a booked booking can replace a planned item."))
    if real.transfer_group_id is not None or real.recurring_id is not None:
        raise Conflict(N_("This booking already belongs to a transfer or a recurring item."))
    if real.account_id != planned["account_id"]:
        raise Conflict(N_("The booking and the planned item are on different accounts."))
    group_id = planned["transfer_group_id"]
    other: sqlite3.Row | None = None
    if group_id is not None:
        if (real.amount_cents > 0) != (planned["amount_cents"] > 0):
            raise Conflict(N_("The booking and the planned transfer go in opposite directions."))
        other = conn.execute(
            'SELECT * FROM "transaction" WHERE transfer_group_id = ? AND id != ?', (group_id, planned_id)
        ).fetchone()
        if other is not None:
            accounts.ensure_bookable(conn, other["account_id"], real.date)
    with transaction(conn):
        # A receipt photo attached to the planned row (UC-ZF-013 5b) moves to the real booking first;
        # deleting the planned row would otherwise set its link to NULL (ON DELETE SET NULL).
        conn.execute("UPDATE receipt SET transaction_id = ? WHERE transaction_id = ?", (real.id, planned_id))
        conn.execute('DELETE FROM "transaction" WHERE id = ?', (planned_id,))
        conn.execute(
            'UPDATE "transaction" SET recurring_id = ?, due_date = ?, transfer_group_id = ? WHERE id = ?',
            (planned["recurring_id"], planned["due_date"], group_id, real.id),
        )
        if other is not None:
            conn.execute(
                "UPDATE \"transaction\" SET status = 'booked', date = ?, amount_cents = ? WHERE id = ?",
                (real.date.isoformat(), -real.amount_cents, other["id"]),
            )
    return transactions.get_transaction(conn, real.id)


# --- expected one-off items --------------------------------------------------------------------


def list_expected(conn: sqlite3.Connection) -> list[transactions.Transaction]:
    """Expected one-off items: planned income and expenses without a recurring item, by date."""
    ids = conn.execute(
        "SELECT id FROM \"transaction\" WHERE status = 'planned' AND recurring_id IS NULL"
        " AND transfer_group_id IS NULL ORDER BY date, id"
    ).fetchall()
    return [transactions.get_transaction(conn, row[0]) for row in ids]


def create_expected(
    conn: sqlite3.Connection, fields: BookingFields, *, today: date | None = None
) -> transactions.Transaction:
    """Store an expected one-off item (UC step 3): a manual booking dated after today, hence planned.

    A date of today or earlier would be a booked row, so it is refused.
    """
    today = today or date.today()
    if fields.date <= today:
        raise RecurringError(N_("An expected item needs a date after today; enter earlier ones as bookings."))
    return transactions.create_transaction(conn, fields, today=today)
