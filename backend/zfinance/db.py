"""SQLite access: connection factory, explicit transactions, FastAPI dependency.

Conventions every feature follows:
    * Money is ``INTEGER`` cents (see ``zfinance.money``); never REAL.
    * Dates are ISO-8601 ``TEXT`` (``YYYY-MM-DD``), timestamps ISO-8601 UTC ``TEXT``.
    * Services take a ``sqlite3.Connection`` as first argument and never open their own.
    * Multi-statement writes go through ``transaction(conn)`` so they commit or roll back as one.

Connections run in autocommit mode (``isolation_level=None``): nothing is implicitly held open,
and ``transaction()`` issues ``BEGIN IMMEDIATE``/``COMMIT``/``ROLLBACK`` itself. ``transaction()``
nests: inside an open transaction it runs as a ``SAVEPOINT``, so a service that wraps its own writes
can be called by a caller that needs several services to commit as one (e.g. an import).

Why ``IMMEDIATE``: a transaction here typically reads and then writes, and several endpoints write
while answering a ``GET`` that a screen sends in parallel. A deferred ``BEGIN`` takes the write lock
only at the first write; if another connection committed in between, SQLite cannot let the
transaction wait without invalidating what it already read and fails at once with ``database is
locked`` — the busy timeout never applies. ``BEGIN IMMEDIATE`` takes the write lock up front, so a
second writer waits at its ``BEGIN`` (up to ``BUSY_TIMEOUT_MS``) and then reads the first one's
result. Plain reads outside ``transaction()`` are not blocked (WAL).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from itertools import count
from pathlib import Path

from fastapi import Request

# How long a connection waits for another connection's write lock before it gives up with
# ``database is locked``. Set explicitly instead of relying on the driver default (5 s): a screen
# should rather wait for a long writer (a CSV import commit) than fail.
BUSY_TIMEOUT_MS = 10_000

# Savepoint names only need to be unique among the savepoints open on one connection.
_savepoint_ids = count(1)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with the project defaults (Row factory, foreign keys, WAL, autocommit).

    ``check_same_thread=False`` because FastAPI may run a request's dependency and its endpoint on
    different worker threads; a connection is still only ever used by one request at a time.
    """
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Connection]:
    """Run the block in one transaction: commit on success, roll back on any exception.

    The outermost level holds the database's write lock from its start (``BEGIN IMMEDIATE``), also
    for a block that only reads; for a read-only query, use the connection without a transaction.

    Called while a transaction is already open (a service used inside a caller's transaction),
    the block runs as a savepoint: a failure rolls back only the block and re-raises, success
    leaves the commit to the outermost ``transaction()``.
    """
    if conn.in_transaction:
        name = f"zf_sp_{next(_savepoint_ids)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def get_connection(request: Request) -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, closed afterwards.

    Usage in a feature router: ``conn: Annotated[sqlite3.Connection, Depends(get_connection)]``.
    """
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()
