"""Ledger extension point: the only place the accounts feature reads or moves bookings.

The bookings live in the ``transaction`` table of the transactions feature (task -008, migration
``020_transactions.sql``); these functions are plain queries on it. Only ``status = 'booked'``
rows count towards a balance — planned and skipped items never do. Counts, the earliest date and
category moves consider rows of every status, because each of them references the account or
category. Tests may still substitute an in-memory fake with ``monkeypatch``.

The service calls these functions through the module (``ledger.booked_sum_until(...)``), never
through ``from ... import``, so a substitute is seen everywhere.
"""

from __future__ import annotations

import sqlite3
from datetime import date


def booked_sum_until(conn: sqlite3.Connection, account_id: int, on_date: date) -> int:
    """Sum of the account's ``booked`` amounts dated on or before ``on_date``, in cents.

    Planned and skipped items never count.
    """
    row = conn.execute(
        'SELECT coalesce(sum(amount_cents), 0) FROM "transaction"'
        " WHERE account_id = ? AND status = 'booked' AND date <= ?",
        (account_id, on_date.isoformat()),
    ).fetchone()
    return int(row[0])


def earliest_booking_date(conn: sqlite3.Connection, account_id: int) -> date | None:
    """Date of the account's earliest booking of any status, or ``None`` without bookings."""
    row = conn.execute('SELECT min(date) FROM "transaction" WHERE account_id = ?', (account_id,)).fetchone()
    return date.fromisoformat(row[0]) if row[0] is not None else None


def account_booking_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """Number of bookings (any status) per account id; accounts without bookings are absent."""
    rows = conn.execute('SELECT account_id, count(*) FROM "transaction" GROUP BY account_id')
    return {int(account_id): int(count) for account_id, count in rows}


def category_booking_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """Number of bookings (any status) per category id; categories without bookings are absent."""
    rows = conn.execute(
        'SELECT category_id, count(*) FROM "transaction" WHERE category_id IS NOT NULL GROUP BY category_id'
    )
    return {int(category_id): int(count) for category_id, count in rows}


def move_category_bookings(conn: sqlite3.Connection, source_id: int, target_id: int) -> int:
    """Re-assign every booking of category ``source_id`` to ``target_id``; return how many moved.

    Called inside the caller's transaction.
    """
    cursor = conn.execute('UPDATE "transaction" SET category_id = ? WHERE category_id = ?', (target_id, source_id))
    return cursor.rowcount
