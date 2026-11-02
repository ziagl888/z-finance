"""UC-ZF-009 backup and restore: schema-driven export, snapshots, rotation, restore rules, HTTP API.

The shipped schema has no feature tables yet, so the service tests run on their own migration
directories: a ``ledger`` (accounts + transactions, hand-computed balances) and a later
``notes`` migration that plays the part of "a feature table that arrives later". Every value is
synthetic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import stat
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zfinance.app import create_app
from zfinance.app_settings import get_setting, set_setting
from zfinance.config import DB_FILENAME, Settings
from zfinance.db import connect
from zfinance.domain.backup import files, service
from zfinance.migrator import MIGRATIONS_DIR, discover, migrate

LEDGER_SQL = """
CREATE TABLE account (id INTEGER PRIMARY KEY, name TEXT NOT NULL, opening_cents INTEGER NOT NULL);
CREATE TABLE txn (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES account(id),
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('booked', 'planned'))
);
"""
NOTES_SQL = "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);"
LOOPBACK = "http://127.0.0.1:8765"
FIXED_APPLIED_AT = "2020-01-01T00:00:00+00:00"


# --- helpers ---------------------------------------------------------------------------------


def _migrations(directory: Path, extra: dict[str, str]) -> Path:
    """The shipped core migration plus the given test migrations in ``directory``."""
    directory.mkdir(parents=True)
    shutil.copy2(MIGRATIONS_DIR / "001_core.sql", directory)
    for name, sql in extra.items():
        (directory / name).write_text(sql, encoding="utf-8")
    return directory


@pytest.fixture
def v1(tmp_path: Path) -> Path:
    """Schema version 10: core + ledger."""
    return _migrations(tmp_path / "v1", {"010_ledger.sql": LEDGER_SQL})


@pytest.fixture
def v2(tmp_path: Path) -> Path:
    """Schema version 20: v1 + a later ``notes`` table."""
    return _migrations(tmp_path / "v2", {"010_ledger.sql": LEDGER_SQL, "020_notes.sql": NOTES_SQL})


@pytest.fixture
def make_db(tmp_path: Path) -> Iterator[Callable[[str, Path], sqlite3.Connection]]:
    """Factory for migrated databases; all connections are closed afterwards."""
    opened: list[sqlite3.Connection] = []

    def factory(name: str, migrations: Path) -> sqlite3.Connection:
        conn = connect(tmp_path / f"{name}.sqlite3")
        migrate(conn, migrations)
        opened.append(conn)
        return conn

    yield factory
    for conn in opened:
        conn.close()


def _seed_ledger(conn: sqlite3.Connection) -> None:
    conn.executemany("INSERT INTO account VALUES (?, ?, ?)", [(1, "Checking A", 100_000), (2, "Cash B", 5_000)])
    conn.executemany(
        "INSERT INTO txn VALUES (?, ?, ?, ?)",
        [
            (1, 1, 250_000, "booked"),
            (2, 1, -12_345, "booked"),
            (3, 1, -99_999, "planned"),
            (4, 2, -1_550, "booked"),
            (5, 2, 20_000, "planned"),
        ],
    )


def _balances(conn: sqlite3.Connection) -> dict[int, int]:
    """Account balance = opening + sum of booked transactions."""
    rows = conn.execute(
        "SELECT a.id, a.opening_cents + COALESCE(SUM(CASE WHEN t.status = 'booked' THEN t.amount_cents END), 0) "
        "FROM account a LEFT JOIN txn t ON t.account_id = a.id GROUP BY a.id ORDER BY a.id"
    )
    return {row[0]: row[1] for row in rows}


def _state(conn: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    """Row count and checksum per table: what "the database is unchanged" is measured with."""
    return {name: (t["row_count"], t["sha256"]) for name, t in service.export_data(conn)["tables"].items()}


def _rows(conn: sqlite3.Connection, sql: str) -> list[tuple[Any, ...]]:
    """Query result as plain tuples (the project's connections return ``sqlite3.Row``)."""
    return [tuple(row) for row in conn.execute(sql)]


def _raw(conn: sqlite3.Connection) -> str:
    return service.dumps_export(service.export_data(conn))


def _start(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), base_url=LOOPBACK)


def _snapshots(settings: Settings, trigger: str | None = None) -> list[service.Snapshot]:
    found = service.list_snapshots(service.backups_dir(settings.data_dir))
    return [s for s in found if trigger is None or s.trigger == trigger]


# --- AK1: round trip ---------------------------------------------------------------------------


def test_roundtrip_reproduces_every_table(make_db: Callable[..., sqlite3.Connection], v1: Path) -> None:
    """Oracle: spec-anchored — UC-ZF-009 AK1: export → empty instance → restore gives the same data."""
    source, target = make_db("source", v1), make_db("target", v1)
    _seed_ledger(source)
    set_setting(source, "ui.language", "de")

    result = service.restore_export(target, _raw(source), v1)

    assert _state(target) == _state(source)
    assert result.rows == {"account": 2, "app_setting": 1, "txn": 5}


def test_roundtrip_reproduces_balances_and_transaction_count(
    make_db: Callable[..., sqlite3.Connection], v1: Path
) -> None:
    """Oracle: spec-anchored — UC AK1; hand-computed A = 1000.00 + 2500.00 - 123.45, B = 50.00 - 15.50.

    Planned transactions (-999.99 on A, +200.00 on B) do not count towards the balance.
    """
    source, target = make_db("source", v1), make_db("target", v1)
    _seed_ledger(source)
    assert _balances(source) == {1: 337_655, 2: 3_450}

    service.restore_export(target, _raw(source), v1)

    assert _balances(target) == {1: 337_655, 2: 3_450}
    assert target.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == 5


# --- AK2: snapshots at start, rotation ---------------------------------------------------------


def test_startup_writes_automatic_snapshot(settings: Settings) -> None:
    """Oracle: spec-anchored — UC-ZF-009 AK2: a backup is created at start (export.json + SQLite copy)."""
    settings.data_dir.mkdir(parents=True)
    with closing(connect(settings.db_path)) as conn:
        migrate(conn)
        set_setting(conn, "ui.language", "de")

    with _start(settings):
        pass

    (snapshot,) = _snapshots(settings, "auto")
    assert (snapshot.path / service.EXPORT_FILENAME).is_file()
    export = json.loads((snapshot.path / service.EXPORT_FILENAME).read_text(encoding="utf-8"))
    assert export["tables"]["app_setting"]["row_count"] == 1
    with closing(sqlite3.connect(snapshot.path / DB_FILENAME)) as copy:  # opens on its own, no -wal beside it
        assert copy.execute("SELECT value FROM app_setting WHERE key = 'ui.language'").fetchone() == ('"de"',)
    assert sorted(p.name for p in snapshot.path.iterdir()) == [service.EXPORT_FILENAME, DB_FILENAME]


def test_rotation_keeps_newest_ten_automatic(settings: Settings) -> None:
    """Oracle: spec-anchored — UC-ZF-009 AK2: never more than 10 automatic backups; 12 starts leave the newest 10."""
    settings.data_dir.mkdir(parents=True)
    with closing(connect(settings.db_path)) as conn:
        migrate(conn)
    created: list[str] = []
    for _ in range(12):
        service.run_startup_backup(settings)
        created.append(_snapshots(settings)[0].name)  # newest first

    remaining = [s.name for s in _snapshots(settings, "auto")]
    assert len(remaining) == 10
    assert remaining == sorted(created, reverse=True)[:10]


def test_rotation_spares_manual_and_foreign_entries(settings: Settings, make_db: Callable[..., Any], v1: Path) -> None:
    """Oracle: spec-anchored — UC AK2 limits the "automatische Sicherungen"; manual ones are not part of it."""
    directory = service.backups_dir(settings.data_dir)
    conn = make_db("live", v1)
    manual = [service.create_snapshot(conn, directory, "manual").name for _ in range(3)]
    for _ in range(12):
        service.create_snapshot(conn, directory, "auto")
    (directory / "my-notes").mkdir()
    (directory / "readme.txt").write_text("keep", encoding="utf-8")
    (directory / "20200101T000000000Z-auto-old").mkdir()  # looks similar, is not a snapshot name

    removed = service.rotate_automatic(directory)

    assert len(removed) == 2
    assert {s.name for s in service.list_snapshots(directory) if s.trigger == "manual"} == set(manual)
    assert len([s for s in service.list_snapshots(directory) if s.trigger == "auto"]) == 10
    assert (directory / "my-notes").is_dir()
    assert (directory / "readme.txt").is_file()
    assert (directory / "20200101T000000000Z-auto-old").is_dir()


def test_same_millisecond_names_do_not_clash(make_db: Callable[..., Any], v1: Path, tmp_path: Path) -> None:
    """Oracle: spec-anchored — design rule in SPEC "Definitions": a name clash moves the clock forward by 1 ms."""
    conn = make_db("live", v1)
    moment = datetime(2026, 9, 19, 10, 15, 30, 123_000, tzinfo=UTC)

    first = service.create_snapshot(conn, tmp_path / "b", "manual", now=moment)
    second = service.create_snapshot(conn, tmp_path / "b", "manual", now=moment)

    assert first.name == "20260919T101530123Z-manual"
    assert second.name == "20260919T101530124Z-manual"
    assert first.created_at == "2026-09-19T10:15:30.123+00:00"


# --- AK3: refuse a non-empty target -----------------------------------------------------------


def test_restore_into_non_empty_is_refused(make_db: Callable[..., sqlite3.Connection], v1: Path) -> None:
    """Oracle: spec-anchored — UC-ZF-009 AK3 / extension 5a: refused, hint to empty manually, no merge."""
    source, target = make_db("source", v1), make_db("target", v1)
    _seed_ledger(source)
    target.execute("INSERT INTO account VALUES (9, 'Existing', 1)")
    before = _state(target)

    with pytest.raises(service.RestoreRefused, match="empty the database manually") as caught:
        service.restore_export(target, _raw(source), v1)

    assert caught.value.status_code == 409
    assert "account" in str(caught.value)
    assert _state(target) == before


def test_app_setting_rows_do_not_block_restore(make_db: Callable[..., sqlite3.Connection], v1: Path) -> None:
    """Oracle: spec-anchored — task: "die bloße Existenz von app_setting-Zeilen einen Restore nicht blockiert"."""
    source, target = make_db("source", v1), make_db("target", v1)
    _seed_ledger(source)
    set_setting(source, "ui.language", "en")
    set_setting(target, "ui.language", "de")
    set_setting(target, "extra.only_in_target", 1)

    service.restore_export(target, _raw(source), v1)

    assert get_setting(target, "ui.language") == "en"  # the exported state replaces the settings
    assert get_setting(target, "extra.only_in_target") is None
    assert target.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == 5


def test_migration_seeded_rows_do_not_block_restore(tmp_path: Path, make_db: Callable[..., sqlite3.Connection]) -> None:
    """Oracle: spec-anchored — SPEC "Definitions": rows the migrations themselves create are not user data."""
    seed = (
        "CREATE TABLE category (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
        " INSERT INTO category VALUES (1, 'Uncategorised');"
    )
    migrations = _migrations(tmp_path / "seeded", {"010_category.sql": seed})
    source, target = make_db("source", migrations), make_db("target", migrations)
    source.execute("INSERT INTO category VALUES (2, 'Food')")

    service.restore_export(target, _raw(source), migrations)

    assert _rows(target, "SELECT id, name FROM category ORDER BY id") == [(1, "Uncategorised"), (2, "Food")]


# --- AK4: schema versions ---------------------------------------------------------------------


def test_older_export_is_migrated_forward(make_db: Callable[..., sqlite3.Connection], v1: Path, v2: Path) -> None:
    """Oracle: spec-anchored — UC-ZF-009 extension 5b: older export → migrated while loading."""
    source, target = make_db("source", v1), make_db("target", v2)
    _seed_ledger(source)
    source.execute("UPDATE schema_migrations SET applied_at = ?", (FIXED_APPLIED_AT,))

    result = service.restore_export(target, _raw(source), v2)

    assert result.export_schema_version == 10
    assert result.schema_version == 20
    assert result.migrations_applied == ["020_notes.sql"]
    assert _balances(target) == {1: 337_655, 2: 3_450}
    assert target.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0  # the newer table exists, empty
    rows = target.execute("SELECT number, applied_at FROM schema_migrations ORDER BY number").fetchall()
    assert [(n, a) for n, a in rows if n in (1, 10)] == [(1, FIXED_APPLIED_AT), (10, FIXED_APPLIED_AT)]
    assert rows[-1][0] == 20


def test_newer_export_aborts_with_versions(make_db: Callable[..., sqlite3.Connection], v1: Path, v2: Path) -> None:
    """Oracle: spec-anchored — UC-ZF-009 extension 5b: newer export → abort naming the version."""
    source, target = make_db("source", v2), make_db("target", v1)
    _seed_ledger(source)
    before = _state(target)

    with pytest.raises(service.IncompatibleVersion) as caught:
        service.restore_export(target, _raw(source), v1)

    assert "schema version 20" in str(caught.value)
    assert "up to 10" in str(caught.value)
    assert _state(target) == before


# --- AK5: integrity ---------------------------------------------------------------------------


def _change_amount(doc: dict[str, Any]) -> None:
    doc["tables"]["txn"]["rows"][0][2] += 1  # column order: id, account_id, amount_cents, status


def _drop_row(doc: dict[str, Any]) -> None:
    doc["tables"]["txn"]["rows"].pop()


def _wrong_row_count(doc: dict[str, Any]) -> None:
    doc["tables"]["txn"]["row_count"] += 1


def _forget_table(doc: dict[str, Any]) -> None:
    del doc["tables"]["txn"]


@pytest.mark.parametrize("tamper", [_change_amount, _drop_row, _wrong_row_count, _forget_table])
def test_tampered_export_is_refused(
    make_db: Callable[..., sqlite3.Connection], v1: Path, tamper: Callable[[dict[str, Any]], None]
) -> None:
    """Oracle: spec-anchored — UC-ZF-009 step 5: row counts and (balance) checksums are checked against the export."""
    source, target = make_db("source", v1), make_db("target", v1)
    _seed_ledger(source)
    doc = service.export_data(source)
    tamper(doc)
    before = _state(target)

    with pytest.raises(service.InvalidExport) as caught:
        service.restore_export(target, service.dumps_export(doc), v1)

    assert caught.value.status_code == 422
    assert _state(target) == before


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"format": "something-else"}', b"\xff\xfe"])
def test_not_an_export_is_refused(make_db: Callable[..., sqlite3.Connection], v1: Path, raw: bytes) -> None:
    """Oracle: spec-anchored — UC-ZF-009 step 5: only a z-finance export is read; anything else is refused."""
    target = make_db("target", v1)
    before = _state(target)

    with pytest.raises(service.InvalidExport):
        service.restore_export(target, raw, v1)

    assert _state(target) == before


# --- AK6: failures ----------------------------------------------------------------------------


def test_unwritable_backup_dir_reports_path(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — UC-ZF-009 extension 2a: error message with the path, no silent failure."""
    directory = service.backups_dir(settings.data_dir)
    shutil.rmtree(directory)
    directory.write_text("a file where the backup folder should be", encoding="utf-8")

    response = client.post("/api/backup/export")

    assert response.status_code == 500
    assert str(directory) in response.json()["detail"]


def test_startup_survives_backup_failure(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    """Oracle: spec-anchored — SPEC AK6b (dev rule): a failing automatic backup is logged and the app still starts."""
    settings.data_dir.mkdir(parents=True)
    service.backups_dir(settings.data_dir).write_text("blocks the backup folder", encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger=service.logger.name), _start(settings) as client:
        assert client.get("/api/health").status_code == 200

    assert any("automatic backup failed" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    ("fault", "expected"), [(OSError("disk full"), service.BackupDirError), (RuntimeError("bug"), RuntimeError)]
)
def test_failed_snapshot_leaves_no_folder(
    make_db: Callable[..., Any],
    v1: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: Exception,
    expected: type[Exception],
) -> None:
    """Oracle: spec-anchored — SPEC AK6c (dev rule): a snapshot that fails half-way leaves no folder behind."""
    conn = make_db("live", v1)
    directory = tmp_path / "backups"

    def explode(_export: dict[str, Any]) -> str:
        raise fault

    monkeypatch.setattr(service, "dumps_export", explode)

    with pytest.raises(expected):
        service.create_snapshot(conn, directory, "manual")

    assert list(directory.iterdir()) == []


# --- AK7: schema-driven -----------------------------------------------------------------------


def test_later_migration_table_is_covered(make_db: Callable[..., sqlite3.Connection], v1: Path, v2: Path) -> None:
    """Oracle: spec-anchored — task: "schema-getrieben, damit spätere Feature-Tabellen automatisch enthalten sind"."""
    live = make_db("live", v1)
    assert service.user_tables(live) == ["account", "app_setting", "txn"]

    migrate(live, v2)  # a later feature migration arrives; the backup code is unchanged
    live.execute("INSERT INTO notes VALUES (1, 'first note')")
    export = service.export_data(live)
    target = make_db("target", v2)
    service.restore_export(target, service.dumps_export(export), v2)

    assert service.user_tables(live) == ["account", "app_setting", "notes", "txn"]
    assert export["tables"]["notes"]["rows"] == [[1, "first note"]]
    assert _rows(target, "SELECT id, body FROM notes") == [(1, "first note")]


def test_schema_migrations_is_metadata_only(make_db: Callable[..., sqlite3.Connection], v1: Path) -> None:
    """Oracle: spec-anchored — task: "schema_migrations gehört in die Metadaten, nicht in die Nutzdaten"."""
    source, target = make_db("source", v1), make_db("target", v1)
    source.execute("UPDATE schema_migrations SET applied_at = ?", (FIXED_APPLIED_AT,))

    export = service.export_data(source)
    service.restore_export(target, service.dumps_export(export), v1)

    assert "schema_migrations" not in export["tables"]
    assert [m["number"] for m in export["migrations"]] == [1, 10]
    assert export["schema_version"] == 10
    assert _rows(target, "SELECT number, name, applied_at FROM schema_migrations ORDER BY number") == [
        (1, "001_core.sql", FIXED_APPLIED_AT),
        (10, "010_ledger.sql", FIXED_APPLIED_AT),
    ]


def test_snapshot_contains_uncheckpointed_wal_rows(make_db: Callable[..., Any], v1: Path, tmp_path: Path) -> None:
    """Oracle: spec-anchored — task: WAL mode, copy not by file copy (rows still sit in the -wal file)."""
    conn = make_db("live", v1)
    _seed_ledger(conn)
    wal = tmp_path / "live.sqlite3-wal"
    assert wal.exists() and wal.stat().st_size > 0  # precondition: the data has not reached the main file

    snapshot = service.create_snapshot(conn, tmp_path / "b", "manual")

    with closing(sqlite3.connect(snapshot.path / DB_FILENAME)) as copy:
        assert copy.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == 5
    export = json.loads((snapshot.path / service.EXPORT_FILENAME).read_text(encoding="utf-8"))
    assert export["tables"]["txn"]["row_count"] == 5


def test_value_types_roundtrip(tmp_path: Path, make_db: Callable[..., sqlite3.Connection]) -> None:
    """Oracle: spec-anchored — schema-driven export must not depend on column types (values written by this test)."""
    sql = (
        "CREATE TABLE mixed (id INTEGER PRIMARY KEY, i INTEGER, r REAL, t TEXT, b BLOB, n, "
        "g INTEGER GENERATED ALWAYS AS (i * 2) VIRTUAL);"
    )
    migrations = _migrations(tmp_path / "mixed", {"010_mixed.sql": sql})
    source, target = make_db("source", migrations), make_db("target", migrations)
    source.executemany(
        "INSERT INTO mixed (id, i, r, t, b, n) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 42, 1.0, "Grüße € 你好", b"\x00\xff\x10", None),
            (2, -7, 0.1, "", b"", 3),
            (3, 9_007_199_254_740_993, -2.5e-7, "quote \" and 'single'", b"x", "text in an untyped column"),
        ],
    )
    export = service.export_data(source)

    service.restore_export(target, service.dumps_export(export), migrations)

    query = "SELECT id, i, r, t, b, n, typeof(r), typeof(b), typeof(n) FROM mixed ORDER BY id"
    assert _rows(target, query) == _rows(source, query)
    assert target.execute("SELECT r FROM mixed WHERE id = 1").fetchone()[0] == 1.0
    assert isinstance(target.execute("SELECT r FROM mixed WHERE id = 1").fetchone()[0], float)
    assert target.execute("SELECT i FROM mixed WHERE id = 3").fetchone()[0] == 9_007_199_254_740_993  # beyond 2**53
    assert "g" not in export["tables"]["mixed"]["columns"]
    assert target.execute("SELECT g FROM mixed WHERE id = 1").fetchone()[0] == 84  # recomputed, not restored


# --- AK8: HTTP API ----------------------------------------------------------------------------


def test_api_export_creates_manual_snapshot_and_lists_it(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — UC-ZF-009 main scenario 2: a dated export + SQLite copy in backups/, download offered."""
    response = client.post("/api/backup/export")

    assert response.status_code == 201
    body = response.json()
    assert body["trigger"] == "manual"
    assert body["name"].endswith("-manual")
    assert body["download_url"] == f"/api/backup/{body['name']}/download"
    folder = service.backups_dir(settings.data_dir) / body["name"]
    assert sorted(p.name for p in folder.iterdir()) == [service.EXPORT_FILENAME, DB_FILENAME]

    listing = client.get("/api/backup").json()
    assert listing["directory"] == str(service.backups_dir(settings.data_dir))
    assert listing["snapshots"][0]["name"] == body["name"]  # newest first
    assert {s["trigger"] for s in listing["snapshots"]} == {"manual", "auto"}  # the start wrote the auto one


def test_api_download_rejects_bad_names(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC AK8b (dev rule): only real snapshot names resolve, nothing outside backups/."""
    name = client.post("/api/backup/export").json()["name"]

    ok = client.get(f"/api/backup/{name}/download")
    assert ok.status_code == 200
    assert ok.headers["content-disposition"].startswith("attachment")
    assert f"z-finance-export-{name}.json" in ok.headers["content-disposition"]
    assert ok.json()["format"] == service.FORMAT

    for bad in ("..", "%2e%2e", "not-a-snapshot", "20200101T000000000Z-manual", "..%2Fsecret"):
        assert client.get(f"/api/backup/{bad}/download").status_code == 404, bad


def test_api_restore_roundtrip_reports_rows(client: TestClient, settings: Settings, tmp_path: Path) -> None:
    """Oracle: spec-anchored — UC-ZF-009 steps 4-5: restore into a fresh instance, success report; hand-known counts."""
    with closing(connect(settings.db_path)) as conn:
        set_setting(conn, "ui.language", "de")
    name = client.post("/api/backup/export").json()["name"]
    export_text = client.get(f"/api/backup/{name}/download").text

    fresh = Settings(data_dir=tmp_path / "second", web_dir=settings.web_dir)
    with _start(fresh) as second:
        response = second.post("/api/backup/restore", content=export_text, headers={"Content-Type": "application/json"})

        assert response.status_code == 200
        shipped_version = max(m.number for m in discover())
        report = response.json()
        # Feature tables of the shipped schema are reported with 0 rows; only filled tables are pinned.
        report["rows"] = {table: count for table, count in report["rows"].items() if count}
        assert report == {
            "export_schema_version": shipped_version,
            "schema_version": shipped_version,
            "migrations_applied": [],
            "rows": {"app_setting": 1},
            "receipt_photos": 0,
        }
    with closing(connect(fresh.db_path)) as restored:
        assert get_setting(restored, "ui.language") == "de"


def test_api_restore_refusals_map_to_http_status(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — UC AK3 / step 5: 409 for a non-empty database, 422 for a bad file."""
    export_text = client.get(f"/api/backup/{client.post('/api/backup/export').json()['name']}/download").text
    json_header = {"Content-Type": "application/json"}

    assert client.post("/api/backup/restore", content=b"not an export", headers=json_header).status_code == 422

    with closing(connect(settings.db_path)) as conn:
        conn.execute("CREATE TABLE probe (x INTEGER)")
        conn.execute("INSERT INTO probe VALUES (1)")
    refused = client.post("/api/backup/restore", content=export_text, headers=json_header)
    assert refused.status_code == 409
    assert "empty the database manually" in refused.json()["detail"]


def test_api_restore_refusal_is_localized_with_accept_language(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — UC NF6 (UI bilingual) + backend de.po: German refusal, parameter filled, still 409."""
    export_text = client.get(f"/api/backup/{client.post('/api/backup/export').json()['name']}/download").text
    with closing(connect(settings.db_path)) as conn:
        conn.execute("CREATE TABLE probe (x INTEGER)")
        conn.execute("INSERT INTO probe VALUES (1)")
    json_header = {"Content-Type": "application/json"}

    german = client.post("/api/backup/restore", content=export_text, headers={**json_header, "Accept-Language": "de"})
    english = client.post("/api/backup/restore", content=export_text, headers=json_header)

    assert german.status_code == english.status_code == 409
    assert german.json()["detail"] == (
        "Die Datenbank enthält bereits Daten (Tabellen: probe). Eine Wiederherstellung funktioniert nur in eine "
        "leere Datenbank und führt nie Daten zusammen; leere die Datenbank zuerst von Hand."
    )
    assert english.json()["detail"] == (
        "The database already holds data (tables: probe). A restore only works into an empty database and never "
        "merges; empty the database manually first."
    )


def test_api_restore_requires_json_content_type(client: TestClient) -> None:
    """Oracle: spec-anchored — local_guard.py contract: a cross-site form cannot send application/json."""
    for headers in ({"Content-Type": "text/plain"}, {"Content-Type": "multipart/form-data; boundary=x"}, {}):
        assert client.post("/api/backup/restore", content=b"{}", headers=headers).status_code == 415


# --- receipt photos: snapshot, archive, restore ------------------------------------------------

_EXT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
YEAR = datetime.now(UTC).year
_RECEIPT_INSERT = (
    "INSERT INTO receipt (id, file_path, media_type, image_hash, status, created_at) "
    "VALUES (?, ?, ?, ?, 'review', '2026-09-19T00:00:00+00:00')"
)


def jpeg(tag: str) -> bytes:
    """A tiny synthetic 'JPEG': signature, a tag that makes each photo unique, end marker."""
    return b"\xff\xd8\xff\xe0" + tag.encode() + b"\xff\xd9"


def png(tag: str) -> bytes:
    """A tiny synthetic 'PNG': the signature plus a tag."""
    return b"\x89PNG\r\n\x1a\n" + tag.encode()


def _path(receipt_id: int, ext: str = "jpg") -> str:
    return f"receipts/{YEAR}/{receipt_id}.{ext}"


def _add_receipt(conn: sqlite3.Connection, data_dir: Path, receipt_id: int, relative: str, data: bytes) -> None:
    """A ``receipt`` row with the real hash of ``data`` and the file below ``data_dir``."""
    media_type = _EXT_TYPES[relative.rsplit(".", 1)[1]]
    conn.execute(_RECEIPT_INSERT, (receipt_id, relative, media_type, hashlib.sha256(data).hexdigest()))
    target = data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


@pytest.fixture
def make_instance(tmp_path: Path) -> Iterator[Callable[..., tuple[sqlite3.Connection, Path]]]:
    """Factory for a migrated database in its own data directory; ``photos`` = {id: (relative path, bytes)}."""
    opened: list[sqlite3.Connection] = []

    def factory(name: str, photos: dict[int, tuple[str, bytes]] | None = None) -> tuple[sqlite3.Connection, Path]:
        data_dir = tmp_path / name
        data_dir.mkdir()
        conn = connect(data_dir / DB_FILENAME)
        migrate(conn)
        opened.append(conn)
        for receipt_id, (relative, data) in (photos or {}).items():
            _add_receipt(conn, data_dir, receipt_id, relative, data)
        return conn, data_dir

    yield factory
    for conn in opened:
        conn.close()


def _zip(path: Path, export: str, entries: Mapping[str, bytes | zipfile.ZipInfo] | None = None) -> Path:
    """Write an archive: ``export.json`` plus the given entries (name → bytes, or a ready ZipInfo with no data)."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("export.json", export)
        for name, value in (entries or {}).items():
            if isinstance(value, zipfile.ZipInfo):
                archive.writestr(value, b"")
            else:
                archive.writestr(name, value)
    return path


def _leftovers(data_dir: Path) -> list[str]:
    """Temporary restore/upload/archive files that must never stay behind."""
    return sorted(p.name for p in data_dir.iterdir() if p.name.startswith((".restore-", ".upload-", ".archive-")))


TWO_PHOTOS = {1: (_path(1, "jpg"), jpeg("first")), 2: (_path(2, "png"), png("second"))}
TWO_ENTRIES = {relative: data for relative, data in TWO_PHOTOS.values()}


def test_archive_roundtrip_restores_photos(client: TestClient, settings: Settings, tmp_path: Path) -> None:
    """Oracle: spec-anchored — task T-2026-CU-9130-033 "Roundtrip mit 2 synthetischen Fotos" / UC-ZF-013 storage.

    Two photos go in through the receipt API, the snapshot archive is restored into a fresh
    instance: both files come back byte-identical at their path and the photo endpoint serves them.
    """
    sent: dict[int, bytes] = {}
    for photo in (jpeg("first"), png("second")):
        response = client.post("/api/receipts", files={"photo": ("r", photo, "application/octet-stream")})
        assert response.status_code == 201, response.text
        sent[response.json()["receipt_id"]] = photo
    name = client.post("/api/backup/export").json()["name"]

    archive = client.get(f"/api/backup/{name}/archive")

    assert archive.status_code == 200
    assert archive.headers["content-type"] == "application/zip"
    assert f"z-finance-backup-{name}.zip" in archive.headers["content-disposition"]
    zip_path = tmp_path / "backup.zip"
    zip_path.write_bytes(archive.content)
    expected = ["export.json"] + [_path(i, "jpg" if data.startswith(b"\xff") else "png") for i, data in sent.items()]
    with zipfile.ZipFile(zip_path) as opened:
        assert sorted(opened.namelist()) == sorted(expected)
    fresh = Settings(data_dir=tmp_path / "second", web_dir=settings.web_dir)
    with _start(fresh) as second:
        response = second.post(
            "/api/backup/restore", content=archive.content, headers={"Content-Type": "application/zip"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["receipt_photos"] == 2
        assert response.json()["rows"]["receipt"] == 2
        for receipt_id, photo in sent.items():
            served = second.get(f"/api/receipts/{receipt_id}/photo")
            assert (served.status_code, served.content) == (200, photo)
    assert _leftovers(fresh.data_dir) == []


def test_api_archive_download_and_restore(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-033 AK8: URLs, 404 for bad names, 415 for other content types.

    The archive is built into a temporary file that is removed after the download.
    """
    created = client.post("/api/backup/export").json()
    assert created["archive_url"] == f"/api/backup/{created['name']}/archive"
    assert client.get("/api/backup").json()["snapshots"][0]["archive_url"] == created["archive_url"]

    assert client.get(created["archive_url"]).status_code == 200
    assert _leftovers(settings.data_dir) == []
    for bad in ("..", "%2e%2e", "not-a-snapshot", "20200101T000000000Z-manual", "..%2Fsecret"):
        assert client.get(f"/api/backup/{bad}/archive").status_code == 404, bad
    for content_type in ("text/plain", "application/octet-stream", "multipart/form-data; boundary=x"):
        refused = client.post("/api/backup/restore", content=b"PK", headers={"Content-Type": content_type})
        assert refused.status_code == 415, content_type
    not_a_zip = client.post("/api/backup/restore", content=b"not a zip", headers={"Content-Type": "application/zip"})
    assert not_a_zip.status_code == 422
    assert _leftovers(settings.data_dir) == []


def test_json_export_restore_still_works_without_photos(make_instance: Callable[..., Any]) -> None:
    """Oracle: spec-anchored — task "Restore ohne Fotos (Alt-Backup) bleibt kompatibel": export.json unchanged.

    A plain export.json (the format of every backup made before photos were included) restores;
    the receipt row is back, its photo is simply not there.
    """
    source, _ = make_instance("source", {1: TWO_PHOTOS[1]})
    target, target_dir = make_instance("target")
    raw = _raw(source)
    assert json.loads(raw)["format_version"] == service.FORMAT_VERSION == 1
    assert set(json.loads(raw)) == {
        "format",
        "format_version",
        "app_version",
        "created_at",
        "schema_version",
        "migrations",
        "tables",
    }

    result = service.restore_export(target, raw)

    assert result.receipt_photos == 0
    assert result.rows["receipt"] == 1
    assert _state(target) == _state(source)
    assert not (target_dir / "receipts").exists()


def test_archive_without_photos_restores(make_instance: Callable[..., Any], tmp_path: Path) -> None:
    """Oracle: spec-anchored — task "Alt-Backup bleibt kompatibel": an archive with only export.json restores."""
    source, _ = make_instance("source")
    target, target_dir = make_instance("target")
    archive = _zip(tmp_path / "old.zip", _raw(source))

    result = service.restore_archive(target, archive, target_dir)

    assert result.receipt_photos == 0
    assert _state(target) == _state(source)


@pytest.mark.parametrize(
    "name",
    [
        "../evil.jpg",
        "receipts/../../evil.jpg",
        "receipts/2026/../../evil.jpg",
        "/abs/evil.jpg",
        "C:/evil.jpg",
        "C:\\evil.jpg",
        "..\\evil.jpg",
        "receipts\\..\\..\\evil.jpg",
        "receipts/2026/01.jpg",
        "receipts/2026/1.exe",
        "receipts/20260/1.jpg",
        "receipts/2026/sub/1.jpg",
        "other/evil.jpg",
        "evil.jpg",
    ],
)
def test_archive_entry_names_are_refused(name: str, make_instance: Callable[..., Any], tmp_path: Path) -> None:
    """Oracle: spec-anchored — task "Zip-Slip-Eintrag im Archiv wird abgewiesen"; orchestrator security boundary.

    Whatever the entry is called, the archive is refused (HTTP 422 class), nothing is written
    outside ``<data dir>/receipts`` — in fact nothing at all — and the database is untouched.
    """
    source, _ = make_instance("source", {1: TWO_PHOTOS[1]})
    # A receipt row that names the evil path and carries the right hash: only the name guard stops it.
    source.execute(_RECEIPT_INSERT, (50, name, "image/jpeg", hashlib.sha256(jpeg("evil")).hexdigest()))
    target, target_dir = make_instance("target")
    before = _state(target)
    archive = _zip(tmp_path / "evil.zip", _raw(source), {name: jpeg("evil")})

    with pytest.raises(service.InvalidExport):
        service.restore_archive(target, archive, target_dir)

    assert _state(target) == before
    assert not (target_dir / "receipts").exists()
    assert list(tmp_path.rglob("evil.jpg")) == [] and list(tmp_path.rglob("evil.exe")) == []
    assert _leftovers(target_dir) == []


def test_archive_refuses_symlink_duplicate_and_encrypted_entries(
    make_instance: Callable[..., Any], tmp_path: Path
) -> None:
    """Oracle: spec-anchored — orchestrator security boundary ("absolute Pfade/`..`/Symlinks ... abweisen").

    A symlink entry carrying a legitimate name and hash, a name listed twice and an entry flagged as
    encrypted are refused as a whole.
    """
    source, _ = make_instance("source", {1: TWO_PHOTOS[1]})
    target, target_dir = make_instance("target")
    before = _state(target)
    link = zipfile.ZipInfo(_path(1))
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    encrypted = zipfile.ZipInfo(_path(1))
    encrypted.flag_bits |= 0x1
    duplicate = tmp_path / "dup.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("export.json", _raw(source))
        archive.writestr(_path(1), TWO_PHOTOS[1][1])
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(_path(1), TWO_PHOTOS[1][1])
    cases = {
        "symlink": _zip(tmp_path / "link.zip", _raw(source), {_path(1): link}),
        "encrypted": _zip(tmp_path / "enc.zip", _raw(source), {_path(1): encrypted}),
        "duplicate": duplicate,
    }

    for label, archive_path in cases.items():
        with pytest.raises(service.InvalidExport):
            service.restore_archive(target, archive_path, target_dir)
        assert _state(target) == before, label
        assert not (target_dir / "receipts").exists(), label


def test_archive_integrity_is_checked(make_instance: Callable[..., Any], tmp_path: Path) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-033 AK5: hashes, references and types of the photos are checked.

    Changed photo, photo no receipt row names, bytes that are no image (row hash matching them),
    an image whose type contradicts its extension, a missing export.json and a non-ZIP file.
    """
    bogus = b"not an image at all"
    source, _ = make_instance(
        "source", {1: TWO_PHOTOS[1], 2: (_path(2, "jpg"), bogus), 3: (_path(3, "png"), jpeg("wrong-type"))}
    )
    target, target_dir = make_instance("target")
    before = _state(target)
    raw = _raw(source)
    (tmp_path / "plain.txt").write_text("hello", encoding="utf-8")
    no_export = tmp_path / "noexport.zip"
    with zipfile.ZipFile(no_export, "w") as archive:
        archive.writestr(_path(1), TWO_PHOTOS[1][1])
    cases = [
        (_zip(tmp_path / "changed.zip", raw, {_path(1): jpeg("CHANGED")}), "does not match its receipt"),
        (_zip(tmp_path / "orphan.zip", raw, {_path(9): jpeg("orphan")}), "no receipt in the export"),
        (_zip(tmp_path / "bogus.zip", raw, {_path(2): bogus}), "not an image"),
        (_zip(tmp_path / "type.zip", raw, {_path(3, "png"): jpeg("wrong-type")}), "not an image"),
        (no_export, "no export.json"),
        (tmp_path / "plain.txt", "not a valid ZIP"),
    ]

    for archive_path, message in cases:
        with pytest.raises(service.InvalidExport, match=message):
            service.restore_archive(target, archive_path, target_dir)
        assert _state(target) == before, message
        assert not (target_dir / "receipts").exists(), message
        assert _leftovers(target_dir) == [], message


def test_valid_archive_with_photos_restores_via_service(make_instance: Callable[..., Any], tmp_path: Path) -> None:
    """Oracle: spec-anchored — task round trip: same rows, same bytes; a stale file at a target is replaced.

    An orphan left at ``receipts/<year>/1.jpg`` (the target database has no receipt) does not block
    the restore; the archive's photo wins.
    """
    source, _ = make_instance("source", TWO_PHOTOS)
    target, target_dir = make_instance("target")
    stale = target_dir / _path(1)
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"orphan")
    archive = _zip(tmp_path / "ok.zip", _raw(source), TWO_ENTRIES)

    result = service.restore_archive(target, archive, target_dir)

    assert result.receipt_photos == 2
    assert _state(target) == _state(source)
    assert {relative: (target_dir / relative).read_bytes() for relative in TWO_ENTRIES} == TWO_ENTRIES
    assert _leftovers(target_dir) == []


def test_archive_limits_are_enforced(
    make_instance: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oracle: spec-anchored — orchestrator "Größen- und Anzahl-Limit gegen Zip-Bomben"; the module constants.

    Too many photos, a photo above the photo limit and photos above the total all refuse the
    archive; the photo limit also holds for the bytes actually read, and a header that lies about
    the size is caught by the CRC check.
    """
    source, _ = make_instance("source", TWO_PHOTOS)
    target, target_dir = make_instance("target")
    before = _state(target)
    archive = _zip(tmp_path / "ok.zip", _raw(source), TWO_ENTRIES)
    largest = max(len(data) for data in TWO_ENTRIES.values())
    hashes = {relative: hashlib.sha256(data).hexdigest() for relative, data in TWO_ENTRIES.items()}

    for constant, value, message in [
        ("MAX_PHOTOS", 1, "too many photos"),
        ("MAX_PHOTO_BYTES", largest - 1, "larger than"),
        ("MAX_PHOTOS_TOTAL_BYTES", largest, "in total"),
        ("MAX_EXPORT_BYTES", 10, "export.json"),
    ]:
        with monkeypatch.context() as patch:
            patch.setattr(files, constant, value)
            with pytest.raises(service.InvalidExport, match=message):
                service.restore_archive(target, archive, target_dir)

    with files.open_archive(archive) as opened:  # entry list passed; now the bytes actually read decide
        monkeypatch.setattr(files, "MAX_PHOTO_BYTES", largest - 1)
        with pytest.raises(service.InvalidExport, match="larger than"):
            opened.stage_photos(hashes, tmp_path / "stage")
        monkeypatch.undo()
    with files.open_archive(archive) as opened:  # a lying header does not pass: the CRC check fails
        for info in opened.photos:
            info.file_size = 1
        with pytest.raises(service.InvalidExport, match="damaged"):
            opened.stage_photos(hashes, tmp_path / "stage2")

    assert _state(target) == before
    assert _leftovers(target_dir) == []


def test_upload_size_limit(client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — orchestrator zip-bomb limit: an oversized upload is refused with 413 and leaves no file.

    Once with a ``Content-Length`` (refused before reading) and once streamed without one.
    """
    monkeypatch.setattr(files, "MAX_ARCHIVE_BYTES", 100)
    zip_header = {"Content-Type": "application/zip"}

    sized = client.post("/api/backup/restore", content=b"PK" + b"0" * 200, headers=zip_header)
    streamed = client.post("/api/backup/restore", content=(b"PK" + b"0" * 50 for _ in range(4)), headers=zip_header)

    assert sized.status_code == streamed.status_code == 413
    assert "larger than" in sized.json()["detail"]
    assert _leftovers(settings.data_dir) == []


class _BackupFails(sqlite3.Connection):
    """A connection whose ``backup()`` fails: stands in for the final copy onto the target breaking."""

    def backup(self, *args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")


def test_failed_restore_removes_placed_photos(
    make_instance: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oracle: spec-anchored — restore invariant "changes the database only as its last step", extended to photos.

    The photos are placed just before the database is switched; when that step fails they are
    removed again and the database stays as it was.
    """
    source, _ = make_instance("source", TWO_PHOTOS)
    target, target_dir = make_instance("target")
    before = _state(target)
    archive = _zip(tmp_path / "ok.zip", _raw(source), TWO_ENTRIES)
    placed: list[list[Path]] = []
    real_place = files.place_photos

    def spy(*args: Any) -> list[Path]:
        placed.append(real_place(*args))
        return placed[-1]

    def failing_connect(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path, isolation_level=None, factory=_BackupFails)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(files, "place_photos", spy)
    monkeypatch.setattr(service, "connect", failing_connect)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        service.restore_archive(target, archive, target_dir)

    assert len(placed[0]) == 2  # the photos were in place when the last step failed ...
    assert [p for p in (target_dir / "receipts").rglob("*") if p.is_file()] == []  # ... and are gone again
    assert _leftovers(target_dir) == []
    assert _state(target) == before


def _hard_links_available(directory: Path) -> bool:
    probe = directory / "probe"
    probe.write_bytes(b"x")
    try:
        os.link(probe, directory / "probe-link")
    except OSError:
        return False
    return True


def test_snapshot_links_photos(settings: Settings, tmp_path: Path) -> None:
    """Oracle: spec-anchored — task "Start-Backup: Vollkopie der Fotos bei jedem Start ist teuer → hardlink".

    The start snapshot holds the photos as links to the live files (same file, no copy); deleting a
    live photo leaves the snapshot's bytes, and rotating the snapshot away leaves the live photo.
    """
    if not _hard_links_available(tmp_path):
        pytest.skip("this file system has no hard links")
    settings.data_dir.mkdir(parents=True)
    with closing(connect(settings.db_path)) as conn:
        migrate(conn)
        for receipt_id, (relative, data) in TWO_PHOTOS.items():
            _add_receipt(conn, settings.data_dir, receipt_id, relative, data)

    service.run_startup_backup(settings)

    (snapshot,) = _snapshots(settings, "auto")
    for relative, data in TWO_ENTRIES.items():
        assert (snapshot.path / relative).read_bytes() == data
        assert os.path.samefile(snapshot.path / relative, settings.data_dir / relative)  # a link, not a copy
    (settings.data_dir / TWO_PHOTOS[1][0]).unlink()  # the user discards the receipt
    assert (snapshot.path / TWO_PHOTOS[1][0]).read_bytes() == TWO_PHOTOS[1][1]
    service.rotate_automatic(service.backups_dir(settings.data_dir), keep=0)  # rotation removes the snapshot
    assert _snapshots(settings) == []
    assert (settings.data_dir / TWO_PHOTOS[2][0]).read_bytes() == TWO_PHOTOS[2][1]


def test_snapshot_copies_when_linking_fails(
    make_instance: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-033 AK6: where hard links are impossible the photo is copied.

    ``os.link`` raises (file system without hard links); the snapshot still holds identical bytes,
    as an independent file.
    """
    source, data_dir = make_instance("source", TWO_PHOTOS)

    def no_links(*_: object, **__: object) -> None:
        raise OSError("hard links not supported")

    monkeypatch.setattr(files.os, "link", no_links)

    snapshot = service.create_snapshot(source, tmp_path / "backups", "manual", data_dir=data_dir)

    for relative, data in TWO_ENTRIES.items():
        assert (snapshot.path / relative).read_bytes() == data
        assert not os.path.samefile(snapshot.path / relative, data_dir / relative)


def test_snapshot_survives_a_photo_discarded_meanwhile(
    make_instance: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Oracle: spec-anchored — restore/backup "kein stilles Scheitern" vs. UC-ZF-013 discard: a discard is not an error.

    The live photo is gone between the check and the link (the receipt was discarded): the snapshot
    is still written, without that photo, and says so in the log.
    """
    source, data_dir = make_instance("source", TWO_PHOTOS)

    def gone(*_: object, **__: object) -> None:
        raise FileNotFoundError("discarded meanwhile")

    monkeypatch.setattr(files.os, "link", gone)
    with caplog.at_level(logging.WARNING):
        snapshot = service.create_snapshot(source, tmp_path / "backups", "manual", data_dir=data_dir)

    assert (snapshot.path / service.EXPORT_FILENAME).is_file()
    assert [p for p in snapshot.path.rglob("*") if p.is_file() and p.suffix in {".jpg", ".png"}] == []
    assert "vanished" in caplog.text


def test_snapshot_skips_paths_outside_receipts(
    make_instance: Callable[..., Any], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Oracle: spec-anchored — orchestrator security boundary + receipts._inside_receipts contract.

    Receipt rows that point outside ``<data dir>/receipts`` (or are no photo path at all) never make
    the snapshot link or copy that file; a row whose photo is missing is skipped as well.
    """
    source, data_dir = make_instance("source", {1: TWO_PHOTOS[1]})
    (tmp_path / "secret.jpg").write_bytes(jpeg("secret"))
    (data_dir / "secret.jpg").write_bytes(jpeg("secret"))
    paths = ["../secret.jpg", "receipts/../secret.jpg", "secret.jpg", _path(404), str(tmp_path / "secret.jpg")]
    for receipt_id, relative in enumerate(paths, start=10):
        source.execute(_RECEIPT_INSERT, (receipt_id, relative, "image/jpeg", f"hash{receipt_id}"))

    with caplog.at_level(logging.WARNING):
        snapshot = service.create_snapshot(source, tmp_path / "backups", "manual", data_dir=data_dir)

    found = sorted(p.relative_to(snapshot.path).as_posix() for p in snapshot.path.rglob("*") if p.is_file())
    assert found == sorted([_path(1), service.EXPORT_FILENAME, DB_FILENAME])
    assert "not backed up" in caplog.text
