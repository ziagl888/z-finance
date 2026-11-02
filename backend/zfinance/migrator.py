"""Minimal forward-only migration runner for the SQLite schema.

Each feature owns plain SQL files in ``backend/zfinance/migrations/`` named ``NNN_<slug>.sql``
(three digits, lowercase slug). Files are applied in lexical order, each inside its own
transaction together with its row in ``schema_migrations``, so a failing file leaves neither a
partial schema nor a version row. Applied files are skipped on the next run.

Invariants:
    * A migration number is used by exactly one file; duplicates abort before anything runs.
    * A ``.sql`` file that does not match ``NNN_<slug>.sql`` aborts before anything runs (a typo
      must not silently skip a migration).
    * Applied migrations are never edited; schema changes ship as a new file.
    * Migration files contain no ``BEGIN``/``COMMIT`` of their own; the runner owns the transaction.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(?P<number>\d{3})_(?P<slug>[a-z0-9]+(?:_[a-z0-9]+)*)\.sql$")

_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    number     INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    applied_at TEXT    NOT NULL
)
"""


class MigrationError(RuntimeError):
    """The migrations directory is inconsistent or a migration failed to apply."""


@dataclass(frozen=True)
class Migration:
    """One SQL migration file."""

    number: int
    name: str
    path: Path


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """List the migrations in ``directory`` sorted by number, validating names and uniqueness."""
    migrations: list[Migration] = []
    seen: dict[int, str] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _NAME_RE.match(path.name)
        if match is None:
            raise MigrationError(f"invalid migration file name {path.name!r}: expected NNN_<slug>.sql")
        number = int(match["number"])
        if number in seen:
            raise MigrationError(f"migration number {number:03d} used twice: {seen[number]} and {path.name}")
        seen[number] = path.name
        migrations.append(Migration(number=number, name=path.name, path=path))
    return migrations


def schema_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration number, 0 for an empty database."""
    conn.execute(_VERSION_TABLE)
    row = conn.execute("SELECT MAX(number) FROM schema_migrations").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply all pending migrations from ``directory``; return the file names applied this run.

    The connection must be in autocommit mode (``zfinance.db.connect``). Each file runs as
    ``BEGIN; <file>`` via ``executescript()`` (multi-statement SQL; it would commit an already open
    transaction, so BEGIN is part of the script), then its ``schema_migrations`` row is inserted
    and the transaction committed. The first error rolls the whole file back.
    """
    migrations = discover(directory)
    conn.execute(_VERSION_TABLE)
    applied = {row[0] for row in conn.execute("SELECT number FROM schema_migrations")}
    done: list[str] = []
    for migration in migrations:
        if migration.number in applied:
            continue
        sql = migration.path.read_text(encoding="utf-8")
        try:
            conn.executescript(f"BEGIN;\n{sql}\n;")
            conn.execute(
                "INSERT INTO schema_migrations (number, name, applied_at) VALUES (?, ?, ?)",
                (migration.number, migration.name, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"migration {migration.name} failed: {exc}") from exc
        logger.info("applied migration %s", migration.name)
        done.append(migration.name)
    return done
