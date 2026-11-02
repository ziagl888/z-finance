"""AK3: the SQL migration runner (order, idempotency, atomic failure, name validation)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zfinance.app_settings import get_setting, set_setting
from zfinance.db import connect
from zfinance.migrator import MigrationError, discover, migrate, schema_version


def _write(directory: Path, name: str, sql: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(sql, encoding="utf-8")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "test.sqlite3")


def test_applies_in_lexical_order_and_records_versions(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Oracle: spec-anchored — task "NNN_<slug>.sql, lexikalisch angewandt" (020 depends on 010)."""
    migrations = tmp_path / "m"
    _write(migrations, "020_child.sql", "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id REFERENCES parent(id));")
    _write(migrations, "010_parent.sql", "CREATE TABLE parent (id INTEGER PRIMARY KEY);")

    assert migrate(conn, migrations) == ["010_parent.sql", "020_child.sql"]
    assert schema_version(conn) == 20
    assert {"parent", "child"} <= _tables(conn)


def test_second_run_applies_nothing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Oracle: spec-anchored — task "idempotent über eine Versions-Tabelle"."""
    migrations = tmp_path / "m"
    _write(migrations, "010_parent.sql", "CREATE TABLE parent (id INTEGER PRIMARY KEY);")
    migrate(conn, migrations)

    assert migrate(conn, migrations) == []
    _write(migrations, "020_later.sql", "CREATE TABLE later (id INTEGER PRIMARY KEY);")
    assert migrate(conn, migrations) == ["020_later.sql"]


def test_failing_migration_leaves_no_trace(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Oracle: spec-anchored — migrator.py contract: a failing file leaves no partial schema, no version row."""
    migrations = tmp_path / "m"
    _write(migrations, "010_ok.sql", "CREATE TABLE ok (id INTEGER PRIMARY KEY);")
    _write(migrations, "020_broken.sql", "CREATE TABLE half (id INTEGER PRIMARY KEY);\nTHIS IS NOT SQL;")

    with pytest.raises(MigrationError, match=r"020_broken\.sql"):
        migrate(conn, migrations)

    assert "half" not in _tables(conn)
    assert schema_version(conn) == 10
    assert not conn.in_transaction


@pytest.mark.parametrize("bad_name", ["10_short.sql", "010-dash.sql", "010_Upper.sql", "010_.sql"])
def test_bad_file_name_rejected_before_anything_runs(conn: sqlite3.Connection, tmp_path: Path, bad_name: str) -> None:
    """Oracle: spec-anchored — migrator.py invariant: a malformed name aborts before any file is applied."""
    migrations = tmp_path / "m"
    _write(migrations, "000_first.sql", "CREATE TABLE first (id INTEGER PRIMARY KEY);")
    _write(migrations, bad_name, "SELECT 1;")

    with pytest.raises(MigrationError, match="invalid migration file name"):
        migrate(conn, migrations)
    assert "first" not in _tables(conn)


def test_duplicate_number_rejected(tmp_path: Path) -> None:
    """Oracle: spec-anchored — migrator.py invariant: one file per number (parallel feature branches collide loudly)."""
    migrations = tmp_path / "m"
    _write(migrations, "010_accounts.sql", "SELECT 1;")
    _write(migrations, "010_other.sql", "SELECT 1;")

    with pytest.raises(MigrationError, match="used twice"):
        discover(migrations)


def test_shipped_migrations_apply_to_fresh_database(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — the repository's own migrations must apply cleanly on an empty file."""
    applied = migrate(conn)

    assert applied == [m.name for m in discover()]
    assert schema_version(conn) >= 1  # 0 is reserved for "empty database"

    assert get_setting(conn, "ui.language", "en") == "en"
    set_setting(conn, "ui.language", "de")
    set_setting(conn, "ui.language", "de-AT")
    assert get_setting(conn, "ui.language") == "de-AT"
