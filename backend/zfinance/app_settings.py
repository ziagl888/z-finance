"""Read and write rows of the ``app_setting`` key/value table (migration 001).

Keys are namespaced by feature (``"ui.language"``, ``"payoff.budget_cents"``) so features never
clash. Values are stored as JSON.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """Return the decoded value for ``key``, or ``default`` if it is not set."""
    row = conn.execute("SELECT value FROM app_setting WHERE key = ?", (key,)).fetchone()
    return default if row is None else json.loads(row[0])


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """Insert or replace ``key`` with the JSON-encoded ``value``."""
    conn.execute(
        "INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value), datetime.now(UTC).isoformat(timespec="seconds")),
    )
