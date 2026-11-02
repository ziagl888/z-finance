"""Backup and restore, driven by the database schema instead of a list of tables.

Export: every ordinary table found via ``PRAGMA table_list`` goes into one JSON document together
with the applied migrations, the schema version and a row count plus SHA-256 checksum per table.
``schema_migrations`` is metadata, never a data table, so tables added by later feature
migrations are covered without touching this module.

Snapshot: ``backups/<utc timestamp>-<auto|manual>/`` holds ``export.json``, a copy of the
SQLite file taken with ``Connection.backup()`` (a plain file copy would lose the WAL content) and,
when the database names receipt photos, ``receipts/<year>/<id>.<ext>`` (hard links to the live
photos, see ``files``). The archive of a snapshot is a ZIP of ``export.json`` plus those photos.

Restore: the export is checked against its own checksums, loaded into an in-memory *staging*
database that has exactly the exported schema (the shipped migrations up to the exported set),
verified there (row counts, checksums, foreign keys), migrated forward to the current schema
and only then copied over the empty target. Older exports therefore run through the normal
migrator; newer ones are refused. A restore from an archive additionally checks and stages the
photos first and moves them into ``<data dir>/receipts`` just before that last step.

Invariants:
    * ``restore_export`` changes the target database only as its very last step; every refusal
      and every error before that leaves it untouched. Photos are placed just before that step and
      removed again when it fails, so a failed restore leaves no photo behind either.
    * A restore is only accepted into an empty instance (see ``non_empty_tables``); it never merges.
    * ``create_snapshot`` leaves no folder behind when it fails.
    * ``rotate_automatic`` removes only folders that match the snapshot naming pattern with the
      ``auto`` suffix; manual snapshots and foreign entries are never touched.
    * ``run_startup_backup`` never raises: a failing backup must not stop the application.
    * Every ``BackupError`` is built from an ``N_()`` msgid plus named parameters (never an
      f-string), so the API can translate it per request; ``str(exc)`` is the English text (the
      classes live in ``errors``).
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from zfinance import __version__
from zfinance.config import DB_FILENAME, Settings
from zfinance.db import connect, transaction
from zfinance.domain.backup import files
from zfinance.domain.backup.errors import (
    BackupDirError,
    IncompatibleVersion,
    InvalidExport,
    RestoreRefused,
)
from zfinance.i18n import N_
from zfinance.migrator import MIGRATIONS_DIR, MigrationError, discover, migrate

logger = logging.getLogger(__name__)

FORMAT = "z-finance-export"
FORMAT_VERSION = 1
BACKUPS_DIRNAME = "backups"
EXPORT_FILENAME = files.EXPORT_FILENAME
KEEP_AUTOMATIC = 10

Trigger = Literal["auto", "manual"]

_METADATA_TABLE = "schema_migrations"
_SETTINGS_TABLE = "app_setting"
_SNAPSHOT_RE = re.compile(r"^(?P<stamp>\d{8}T\d{9}Z)-(?P<trigger>auto|manual)$")


@dataclass(frozen=True)
class Snapshot:
    """One folder below ``backups/``."""

    name: str
    trigger: Trigger
    created_at: str
    path: Path


@dataclass(frozen=True)
class RestoreResult:
    """What a successful restore did."""

    export_schema_version: int
    schema_version: int
    migrations_applied: list[str]
    rows: dict[str, int]
    receipt_photos: int = 0


def backups_dir(data_dir: Path) -> Path:
    """Folder that holds all snapshots."""
    return data_dir / BACKUPS_DIRNAME


# --- schema access -----------------------------------------------------------------------------


def _q(identifier: str) -> str:
    """Quote an identifier taken from the database itself (never from user input)."""
    return '"' + identifier.replace('"', '""') + '"'


def _table_list(conn: sqlite3.Connection) -> list[tuple[str, bool]]:
    """Ordinary data tables as ``(name, without_rowid)``, sorted by name.

    ``PRAGMA table_list`` rows are ``(schema, name, type, ncol, wr, strict)``; only ``type ==
    'table'`` is taken, which leaves out views, FTS shadow tables and virtual tables.
    """
    rows = conn.execute("PRAGMA table_list").fetchall()
    return sorted(
        (row[1], bool(row[4]))
        for row in rows
        if row[0] == "main" and row[2] == "table" and not row[1].startswith("sqlite_") and row[1] != _METADATA_TABLE
    )


def user_tables(conn: sqlite3.Connection) -> list[str]:
    """Names of the tables that an export contains."""
    return [name for name, _ in _table_list(conn)]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Insertable columns in declaration order (generated columns are skipped).

    ``PRAGMA table_xinfo`` rows are ``(cid, name, type, notnull, dflt_value, pk, hidden)``; hidden
    is 0 for ordinary columns and 2/3 for virtual/stored generated columns.
    """
    return [row[1] for row in conn.execute(f"PRAGMA table_xinfo({_q(table)})") if row[6] == 0]


def _order_by(conn: sqlite3.Connection, table: str, without_rowid: bool) -> str:
    """Deterministic row order: rowid, or the primary key of a WITHOUT ROWID table."""
    if not without_rowid:
        return "rowid"
    keys = sorted((row[5], row[1]) for row in conn.execute(f"PRAGMA table_xinfo({_q(table)})") if row[5] > 0)
    return ", ".join(_q(name) for _, name in keys)


# --- value encoding and checksums --------------------------------------------------------------


def _encode(value: Any) -> Any:
    """SQLite value → JSON value; BLOBs become ``{"$blob": <base64>}``."""
    if isinstance(value, bytes):
        return {"$blob": base64.b64encode(value).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    """JSON value → SQLite value (inverse of ``_encode``); containers other than blobs are refused."""
    if isinstance(value, dict):
        blob = value.get("$blob")
        if len(value) != 1 or not isinstance(blob, str):
            raise InvalidExport(N_("the export contains a value that no table column can hold"))
        try:
            return base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidExport(N_("the export contains a damaged binary value")) from exc
    if isinstance(value, list):
        raise InvalidExport(N_("the export contains a value that no table column can hold"))
    return value


def _digest(columns: list[str], rows: list[list[Any]]) -> str:
    """SHA-256 over the canonical JSON encoding of ``[columns, rows]``."""
    payload = json.dumps([columns, rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_table(conn: sqlite3.Connection, table: str, without_rowid: bool) -> tuple[list[str], list[list[Any]]]:
    columns = _columns(conn, table)
    select = ", ".join(_q(name) for name in columns)
    order = _order_by(conn, table, without_rowid)
    cursor = conn.execute(f"SELECT {select} FROM {_q(table)} ORDER BY {order}")  # noqa: S608  # nosec B608 — identifiers from sqlite_master
    return columns, [[_encode(value) for value in row] for row in cursor]


# --- export ------------------------------------------------------------------------------------


def export_data(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """Read every data table in one read transaction and return the export document."""
    created_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    with transaction(conn):
        migrations = [
            {"number": row[0], "name": row[1], "applied_at": row[2]}
            for row in conn.execute(f"SELECT number, name, applied_at FROM {_METADATA_TABLE} ORDER BY number")  # noqa: S608  # nosec B608
        ]
        tables: dict[str, Any] = {}
        for name, without_rowid in _table_list(conn):
            columns, rows = _read_table(conn, name, without_rowid)
            tables[name] = {"columns": columns, "row_count": len(rows), "sha256": _digest(columns, rows), "rows": rows}
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "created_at": created_at,
        "schema_version": max((m["number"] for m in migrations), default=0),
        "migrations": migrations,
        "tables": tables,
    }


def dumps_export(export: dict[str, Any]) -> str:
    """Serialise an export document (compact JSON, UTF-8 safe)."""
    return json.dumps(export, ensure_ascii=False, separators=(",", ":"))


# --- snapshots ---------------------------------------------------------------------------------


def _created_at(stamp: str) -> str:
    """``20260919T101530123Z`` → ``2026-09-19T10:15:30.123+00:00``."""
    moment = datetime.strptime(stamp[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    return (moment + timedelta(milliseconds=int(stamp[15:18]))).isoformat(timespec="milliseconds")


def _snapshot_from(path: Path) -> Snapshot | None:
    match = _SNAPSHOT_RE.match(path.name)
    if match is None or not path.is_dir():
        return None
    trigger: Trigger = "auto" if match["trigger"] == "auto" else "manual"
    return Snapshot(name=path.name, trigger=trigger, created_at=_created_at(match["stamp"]), path=path)


def list_snapshots(directory: Path) -> list[Snapshot]:
    """Snapshots below ``directory``, newest first; anything else in the folder is ignored."""
    if not directory.is_dir():
        return []
    found = (_snapshot_from(path) for path in directory.iterdir())
    return sorted((s for s in found if s is not None), key=lambda s: s.name, reverse=True)


def snapshot_export_path(directory: Path, name: str) -> Path | None:
    """``export.json`` of the named snapshot, or None when the name is not a snapshot name or it is gone."""
    if _SNAPSHOT_RE.match(name) is None:
        return None
    path = directory / name / EXPORT_FILENAME
    return path if path.is_file() else None


def _new_snapshot_dir(directory: Path, now: datetime, trigger: Trigger) -> Path:
    """Create the empty snapshot folder; a name clash within the same millisecond bumps the clock."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupDirError(
            N_("The backup folder %(directory)s cannot be used: %(reason)s"), directory=directory, reason=exc
        ) from exc
    for _ in range(5):
        stamp = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"
        path = directory / f"{stamp}-{trigger}"
        try:
            path.mkdir()
        except FileExistsError:
            now += timedelta(milliseconds=1)
        except OSError as exc:
            raise BackupDirError(
                N_("The backup folder %(directory)s is not writable: %(reason)s"), directory=directory, reason=exc
            ) from exc
        else:
            return path
    raise BackupDirError(N_("Could not allocate a new snapshot folder in %(directory)s"), directory=directory)


def create_snapshot(
    conn: sqlite3.Connection,
    directory: Path,
    trigger: Trigger,
    *,
    now: datetime | None = None,
    data_dir: Path | None = None,
) -> Snapshot:
    """Write ``export.json`` and a SQLite copy into a new snapshot folder below ``directory``.

    The SQLite copy is taken first and the JSON is read from that copy, so both files describe
    the same state even while the application keeps writing. With ``data_dir`` the receipt photos
    that the copy names are linked into ``<snapshot>/receipts/`` afterwards (``files.link_photos``);
    without it the snapshot is database-only.
    """
    moment = now or datetime.now(UTC)
    target = _new_snapshot_dir(directory, moment, trigger)
    try:
        with closing(sqlite3.connect(target / DB_FILENAME, isolation_level=None)) as copy:
            conn.backup(copy)
            export = export_data(copy, now=moment)
            if data_dir is not None:
                files.link_photos(copy, data_dir, target)
        (target / EXPORT_FILENAME).write_text(dumps_export(export), encoding="utf-8")
    except BaseException as exc:
        shutil.rmtree(target, ignore_errors=True)
        if isinstance(exc, OSError | sqlite3.Error):
            raise BackupDirError(
                N_("The backup could not be written to %(target)s: %(reason)s"), target=target, reason=exc
            ) from exc
        raise
    return Snapshot(name=target.name, trigger=trigger, created_at=_created_at(target.name.split("-")[0]), path=target)


def rotate_automatic(directory: Path, keep: int = KEEP_AUTOMATIC) -> list[str]:
    """Delete all but the newest ``keep`` automatic snapshots; return the removed names."""
    removed: list[str] = []
    automatic = [s for s in list_snapshots(directory) if s.trigger == "auto"]
    for snapshot in automatic[keep:]:
        try:
            shutil.rmtree(snapshot.path)
        except OSError:
            logger.warning("could not remove old backup %s", snapshot.path, exc_info=True)
        else:
            removed.append(snapshot.name)
    return removed


def run_startup_backup(settings: Settings) -> None:
    """Write the automatic snapshot of a start and rotate; log and continue on any failure."""
    try:
        conn = connect(settings.db_path)
        try:
            snapshot = create_snapshot(conn, backups_dir(settings.data_dir), "auto", data_dir=settings.data_dir)
        finally:
            conn.close()
        rotate_automatic(backups_dir(settings.data_dir))
        logger.info("automatic backup: %s", snapshot.path)
    except Exception:
        logger.exception("automatic backup failed; continuing startup")


# --- restore -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _ParsedExport:
    schema_version: int
    migrations: list[dict[str, Any]]
    tables: dict[str, dict[str, Any]]


def _require(condition: bool, msgid: str, **params: Any) -> None:
    if not condition:
        raise InvalidExport(msgid, **params)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_export(raw: bytes | str) -> _ParsedExport:
    """Parse and validate an export document, including row counts and checksums per table."""
    try:
        doc = json.loads(raw)
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise InvalidExport(N_("The file is not valid JSON: %(reason)s"), reason=exc) from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise InvalidExport(N_("The file is not a z-finance export."))
    _require(
        doc.get("format_version") == FORMAT_VERSION,
        N_("Unsupported export format version %(found)s; this z-finance reads version %(supported)s."),
        found=repr(doc.get("format_version")),
        supported=FORMAT_VERSION,
    )
    migrations, tables = doc.get("migrations"), doc.get("tables")
    if not isinstance(migrations, list) or not isinstance(tables, dict):
        raise InvalidExport(N_("The export is missing its migrations or tables."))
    for entry in migrations:
        _require(
            isinstance(entry, dict)
            and _is_int(entry.get("number"))
            and isinstance(entry.get("name"), str)
            and isinstance(entry.get("applied_at"), str),
            N_("The export lists a malformed migration."),
        )
    version = max((m["number"] for m in migrations), default=0)
    _require(doc.get("schema_version") == version, N_("The export's schema version does not match its migrations."))
    for name, table in tables.items():
        _require(
            isinstance(table, dict)
            and isinstance(table.get("columns"), list)
            and all(isinstance(c, str) for c in table["columns"])
            and isinstance(table.get("rows"), list)
            and all(isinstance(r, list) and len(r) == len(table["columns"]) for r in table["rows"])
            and _is_int(table.get("row_count")),
            N_("Table %(table)s in the export is malformed."),
            table=repr(name),
        )
        _require(
            table["row_count"] == len(table["rows"]),
            N_("Table %(table)s: the row count does not match its rows."),
            table=repr(name),
        )
        _require(
            table.get("sha256") == _digest(table["columns"], table["rows"]),
            N_("Table %(table)s: the checksum does not match its rows (the file was changed or damaged)."),
            table=repr(name),
        )
    return _ParsedExport(schema_version=version, migrations=migrations, tables=tables)


def _pristine_rows(migrations_dir: Path) -> dict[str, set[tuple[Any, ...]]]:
    """Rows of a freshly migrated database, i.e. what the migrations themselves create."""
    with closing(connect(Path(":memory:"))) as fresh:
        migrate(fresh, migrations_dir)
        return {
            name: {tuple(row) for row in fresh.execute(f"SELECT * FROM {_q(name)}")}  # noqa: S608  # nosec B608
            for name in user_tables(fresh)
        }


def non_empty_tables(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Tables that hold user data.

    ``app_setting`` never counts (a fresh install already stores ``ui.language`` there), and rows
    that a freshly migrated database contains anyway (seed rows of a migration) do not count either.
    """
    pristine = _pristine_rows(migrations_dir)
    found: list[str] = []
    for name in user_tables(conn):
        if name == _SETTINGS_TABLE:
            continue
        seeded = pristine.get(name, set())
        cursor = conn.execute(f"SELECT * FROM {_q(name)}")  # noqa: S608  # nosec B608
        if any(tuple(row) not in seeded for row in cursor):
            found.append(name)
    return found


def _load_staging(staging: sqlite3.Connection, parsed: _ParsedExport) -> None:
    """Fill the staging database with the export and verify it against the export's checksums."""
    schema = dict(_table_list(staging))
    _require(
        set(parsed.tables) == set(schema),
        N_(
            "The export's tables do not match schema version %(version)s: "
            "missing %(missing)s, unexpected %(unexpected)s."
        ),
        version=parsed.schema_version,
        missing=sorted(set(schema) - set(parsed.tables)),
        unexpected=sorted(set(parsed.tables) - set(schema)),
    )
    staging.execute("PRAGMA foreign_keys = OFF")  # tables load in name order; checked afterwards
    try:
        with transaction(staging):
            for name, table in parsed.tables.items():
                _require(
                    table["columns"] == _columns(staging, name),
                    N_("The columns of table %(table)s do not match its schema."),
                    table=repr(name),
                )
                marks = ", ".join("?" for _ in table["columns"])
                names = ", ".join(_q(c) for c in table["columns"])
                # Seed rows of a migration are replaced by the exported state, not added to it.
                staging.execute(f"DELETE FROM {_q(name)}")  # noqa: S608  # nosec B608
                staging.executemany(
                    f"INSERT INTO {_q(name)} ({names}) VALUES ({marks})",  # noqa: S608  # nosec B608
                    ([_decode(v) for v in row] for row in table["rows"]),
                )
            for entry in parsed.migrations:  # keep the original application timestamps
                staging.execute(
                    f"UPDATE {_METADATA_TABLE} SET applied_at = ? WHERE number = ?",  # noqa: S608  # nosec B608
                    (entry["applied_at"], entry["number"]),
                )
    except sqlite3.Error as exc:
        raise InvalidExport(N_("The export data does not fit the database schema: %(reason)s"), reason=exc) from exc
    for name, table in parsed.tables.items():
        columns, rows = _read_table(staging, name, schema[name])
        _require(
            len(rows) == table["row_count"] and _digest(columns, rows) == table["sha256"],
            N_("Table %(table)s does not read back as exported (row count or checksum mismatch)."),
            table=repr(name),
        )
    violations = staging.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise InvalidExport(N_("The export breaks a foreign key in table %(table)s."), table=repr(violations[0][0]))
    staging.execute("PRAGMA foreign_keys = ON")


def _receipt_photo_hashes(parsed: _ParsedExport) -> dict[str, str]:
    """``file_path`` → ``image_hash`` of every receipt row in the export (empty without a ``receipt`` table)."""
    table = parsed.tables.get("receipt")
    if table is None or not {"file_path", "image_hash"} <= set(table["columns"]):
        return {}
    path_at, hash_at = table["columns"].index("file_path"), table["columns"].index("image_hash")
    return {
        row[path_at]: row[hash_at]
        for row in table["rows"]
        if isinstance(row[path_at], str) and isinstance(row[hash_at], str)
    }


def restore_archive(
    conn: sqlite3.Connection, archive: Path, data_dir: Path, migrations_dir: Path = MIGRATIONS_DIR
) -> RestoreResult:
    """Restore from a backup archive (ZIP of ``export.json`` and receipt photos) into the empty ``conn``.

    The photos are written below ``<data_dir>/receipts`` (see ``files`` for what is checked).
    """
    with files.open_archive(archive) as opened:
        return restore_export(conn, opened.read_export(), migrations_dir, photos=opened, data_dir=data_dir)


def restore_export(
    conn: sqlite3.Connection,
    raw: bytes | str,
    migrations_dir: Path = MIGRATIONS_DIR,
    *,
    photos: files.PhotoArchive | None = None,
    data_dir: Path | None = None,
) -> RestoreResult:
    """Load an export into the empty database behind ``conn`` (see the module docstring).

    ``photos`` (an opened archive, with the ``data_dir`` it restores into) adds the receipt photos:
    they are checked and staged first, placed just before the database is switched and removed
    again if that step fails.
    """
    if (photos is None) != (data_dir is None):
        raise ValueError("photos and data_dir belong together")
    parsed = parse_export(raw)
    shipped = {m.number: m for m in discover(migrations_dir)}
    supported = max(shipped, default=0)
    if parsed.schema_version > supported:
        raise IncompatibleVersion(
            N_(
                "The export has schema version %(version)s, but this z-finance supports up to %(supported)s. "
                "Update z-finance and try again."
            ),
            version=parsed.schema_version,
            supported=supported,
        )
    for entry in parsed.migrations:
        known = shipped.get(entry["number"])
        _require(
            known is not None and known.name == entry["name"],
            N_("The export was made with a migration this z-finance does not have: %(name)s."),
            name=entry["name"],
        )
    blocking = non_empty_tables(conn, migrations_dir)
    if blocking:
        raise RestoreRefused(
            N_(
                "The database already holds data (tables: %(tables)s). A restore only works into an "
                "empty database and never merges; empty the database manually first."
            ),
            tables=", ".join(blocking),
        )
    placed: list[Path] = []
    with contextlib.ExitStack() as cleanup:
        staged_names: list[str] = []
        staging_dir: Path | None = None
        if photos is not None and data_dir is not None:
            staging_dir = Path(tempfile.mkdtemp(dir=data_dir, prefix=".restore-"))
            cleanup.callback(shutil.rmtree, staging_dir, ignore_errors=True)
            staged_names = photos.stage_photos(_receipt_photo_hashes(parsed), staging_dir)
        with closing(connect(Path(":memory:"))) as staging:  # in memory: nothing to clean up on failure
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    for entry in parsed.migrations:
                        shutil.copy2(shipped[entry["number"]].path, tmp)
                    migrate(staging, Path(tmp))  # exactly the exported schema
                _load_staging(staging, parsed)
                applied = migrate(staging, migrations_dir)  # then forward to the current schema
            except MigrationError as exc:
                raise InvalidExport(
                    N_("The export could not be migrated to the current schema: %(reason)s"), reason=exc
                ) from exc
            if staging_dir is not None and data_dir is not None:
                placed = files.place_photos(staging_dir, data_dir, staged_names)
            try:
                staging.backup(conn)  # the only step that touches the target database
            except BaseException:
                files.remove_files(placed)
                raise
    return RestoreResult(
        export_schema_version=parsed.schema_version,
        schema_version=supported,
        migrations_applied=applied,
        rows={name: table["row_count"] for name, table in parsed.tables.items()},
        receipt_photos=len(placed),
    )
