"""``db.transaction()``: commit/rollback and nesting as savepoints (services inside a caller's transaction)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from zfinance.db import BUSY_TIMEOUT_MS, connect, transaction

# How long the second writer gets to reach the first one's open transaction, and how long the test
# waits for it to finish afterwards (well above the busy timeout it may spend waiting).
_SECOND_WRITER_HEAD_START_S = 0.5
_WORKER_JOIN_TIMEOUT_S = 30


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "test.sqlite3")
    conn.execute("CREATE TABLE item (name TEXT NOT NULL)")
    return conn


def _names(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT name FROM item ORDER BY rowid")]


class Boom(Exception):
    pass


def test_outer_rollback_discards_nested_block(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — db.py docstring: a nested block commits only with the outermost transaction."""
    with pytest.raises(Boom), transaction(conn):
        conn.execute("INSERT INTO item VALUES ('outer')")
        with transaction(conn):
            conn.execute("INSERT INTO item VALUES ('inner')")
        raise Boom

    assert _names(conn) == []
    assert not conn.in_transaction


def test_failing_nested_block_rolls_back_only_itself(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — db.py docstring: a failing savepoint rolls back the block and re-raises."""
    with transaction(conn):
        conn.execute("INSERT INTO item VALUES ('kept')")
        with pytest.raises(Boom), transaction(conn):
            conn.execute("INSERT INTO item VALUES ('dropped')")
            raise Boom
        conn.execute("INSERT INTO item VALUES ('after')")

    assert _names(conn) == ["kept", "after"]
    assert not conn.in_transaction


def test_top_level_transaction_commits_and_rolls_back(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — db.py docstring: commit on success, roll back on any exception."""
    with transaction(conn):
        conn.execute("INSERT INTO item VALUES ('a')")
    with pytest.raises(Boom), transaction(conn):
        conn.execute("INSERT INTO item VALUES ('b')")
        raise Boom

    assert _names(conn) == ["a"]


def test_second_writer_waits_for_the_first_instead_of_failing(tmp_path: Path) -> None:
    """Oracle: spec-anchored — task T-2026-CU-9130-038 (F-3 of docs/qa/acceptance-1.0.md).

    Two connections run the shape of a GET endpoint that writes while answering: read, then write,
    each in one ``transaction()``. The second one must wait for the first one's commit (busy
    timeout) and then read its row — not fail with ``database is locked``. Hand-derived expectation:
    the first transaction reads 0 rows and writes ``seen=0``; the second one runs after its commit,
    reads 1 row and writes ``seen=1``.
    """
    path = tmp_path / "test.sqlite3"
    setup = connect(path)
    setup.execute("CREATE TABLE item (name TEXT NOT NULL, seen INTEGER NOT NULL)")
    setup.close()
    first, second = connect(path), connect(path)
    errors: list[BaseException] = []
    second_done = threading.Event()

    def second_writer() -> None:
        try:
            with transaction(second):
                seen = second.execute("SELECT count(*) FROM item").fetchone()[0]
                second.execute("INSERT INTO item VALUES ('second', ?)", (seen,))
        except BaseException as exc:  # reported to the main thread below
            errors.append(exc)
        finally:
            second_done.set()

    with transaction(first):
        seen = first.execute("SELECT count(*) FROM item").fetchone()[0]
        worker = threading.Thread(target=second_writer)
        worker.start()
        # Give the second writer the chance to run into the open transaction. Without the fix it
        # commits here and the first writer's INSERT below fails; with it, it waits at its BEGIN.
        second_done.wait(timeout=_SECOND_WRITER_HEAD_START_S)
        first.execute("INSERT INTO item VALUES ('first', ?)", (seen,))
    worker.join(timeout=_WORKER_JOIN_TIMEOUT_S)

    assert not worker.is_alive()
    assert errors == []
    rows = [tuple(row) for row in first.execute("SELECT name, seen FROM item ORDER BY rowid")]
    assert rows == [("first", 0), ("second", 1)]
    first.close()
    second.close()


def test_reader_is_not_blocked_by_an_open_write_transaction(tmp_path: Path) -> None:
    """Oracle: spec-anchored — task T-2026-CU-9130-038, option a): WAL keeps plain readers parallel.

    While one connection holds a write transaction, another connection reads the last committed
    state at once (no wait, no error): the committed row, not the uncommitted one.
    """
    path = tmp_path / "test.sqlite3"
    writer, reader = connect(path), connect(path)
    writer.execute("CREATE TABLE item (name TEXT NOT NULL)")
    writer.execute("INSERT INTO item VALUES ('committed')")
    reader.execute("PRAGMA busy_timeout = 0")  # a wait would fail at once instead of hiding

    with transaction(writer):
        writer.execute("INSERT INTO item VALUES ('pending')")
        assert _names(reader) == ["committed"]

    assert _names(reader) == ["committed", "pending"]
    writer.close()
    reader.close()


def test_connection_sets_an_explicit_busy_timeout(tmp_path: Path) -> None:
    """Oracle: spec-anchored — task T-2026-CU-9130-038: an explicit busy timeout, not the driver default."""
    conn = connect(tmp_path / "test.sqlite3")

    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    conn.close()
