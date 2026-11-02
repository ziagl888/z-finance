"""CSV import (UC-ZF-005): profiles, settings, preview and the one-transaction commit.

Flow: ``preview`` parses a file with a profile (``parser``), loads the account's ledger around the
file's dates and classifies every line (``matching``); ``stage`` keeps the file for the commit.
``commit`` recomputes that preview from the staged file (the ledger may have changed), applies the
user's overrides and writes everything in one database transaction through the services of the
other features:

* ``transactions.create_transaction`` per line, then the row is tagged ``source='import'``,
  ``status='booked'``, ``import_hash``, ``import_run_id``;
* ``recurring.replace_planned`` for a line that matches a planned item;
* ``rules.apply_rules`` over the written rows;
* one ``import_run`` row with the counts.

Any error rolls all of it back (UC 6a, AK6) and names the line. After a successful commit
``debts.sync_debts`` runs once, so a debt paid off by an imported instalment is closed at once
(contract of -013; it logs and skips a debt it cannot sync, it never undoes the import).

Invariants: a ``duplicate`` line is never written (its key is unique in the ledger); a
``duplicate_suspect`` line is written only when an override says ``skip: false``; a matched planned
row is replaced by exactly one line.

Errors are ``ImportsError`` (from ``parser``: ``ImportFileError``) subclasses carrying an ``N_()``
msgid; ``AccountsError``, ``TransactionsError`` and ``RecurringError`` raised while writing a line
are wrapped in ``LineWriteError`` with the line number.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

import regex

from zfinance.app_settings import get_setting, set_setting
from zfinance.db import transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.debts import service as debts
from zfinance.domain.imports import matching, parser
from zfinance.domain.imports.matching import Classified, Kind, LedgerRow, Tolerance
from zfinance.domain.imports.parser import ColumnMap, ImportFileError, LineError, ParsedLine, Profile
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.service import RecurringError
from zfinance.domain.rules import service as rules
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransactionsError
from zfinance.i18n import N_

AMOUNT_PCT_KEY = "imports.match_amount_tolerance_pct"
DATE_DAYS_KEY = "imports.match_date_tolerance_days"
#: Accepted ranges of the tolerance settings.
AMOUNT_PCT_MAX = 50
DATE_DAYS_MAX = 31
#: Staged files kept for a commit, and for how long.
STAGE_MAX = 8
STAGE_TTL_SECONDS = 3600
#: Ids per ``IN (...)`` query (SQLite caps the number of parameters).
KEY_CHUNK = 500


# --- errors ------------------------------------------------------------------------------------


class ImportsError(ImportFileError):
    """Base class of the service's own refusals (same shape as the parser's file errors)."""


class NotFound(ImportsError):
    """Profile, run or staged preview does not exist."""

    status_code = 404


class Conflict(ImportsError):
    """The request contradicts the preview or existing data."""

    status_code = 409


class LineWriteError(Exception):
    """Writing one line failed; the whole import was rolled back. ``cause`` has msgid and params."""

    def __init__(self, line: int, cause: AccountsError | TransactionsError | RecurringError | ImportsError) -> None:
        super().__init__(f"line {line}: {cause}")
        self.line = line
        self.cause = cause
        self.status_code = cause.status_code


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StoredProfile(Profile):
    """A saved import profile."""

    id: int
    created_at: str


@dataclass(frozen=True)
class PreviewLine:
    """One classified line as the preview shows it.

    ``category_id`` is the proposed category: the planned item's for a match, else the winning
    rule's (``category_source`` ``planned`` or ``rule``). ``planned`` describes the matched planned
    row (id, date, amount, text).
    """

    line: int
    date: date
    amount_cents: int
    text: str
    counterparty: str | None
    kind: Kind
    import_hash: str
    planned_id: int | None
    candidates: tuple[int, ...]
    review: bool
    suspect_id: int | None
    category_id: int | None
    category_source: str | None


@dataclass(frozen=True)
class Preview:
    """What an import of this file would do; nothing is written."""

    account_id: int
    profile_id: int
    file_hash: str
    encoding: str
    delimiter: str
    lines: list[PreviewLine]
    errors: list[LineError]
    counts: dict[str, int]
    preview_id: str = ""


@dataclass(frozen=True)
class Override:
    """The user's correction of one preview line (UC step 4).

    ``skip``: ``True`` leaves the line out, ``False`` imports a duplicate suspect; ``None`` keeps
    the default (duplicates and suspects are skipped, all else imported). ``set_match`` with
    ``planned_id`` re-hangs the match to another candidate, with ``None`` removes it.
    """

    line: int
    skip: bool | None = None
    category_id: int | None = None
    set_match: bool = False
    planned_id: int | None = None


@dataclass(frozen=True)
class Run:
    """A committed import."""

    id: int
    account_id: int
    profile_id: int | None
    file_name: str | None
    file_hash: str
    counts: dict[str, int]
    errors: list[LineError]
    created_at: str


@dataclass(frozen=True)
class Detected:
    """Encoding, delimiter, header and first records of a file, for setting up a profile."""

    encoding: str
    delimiter: str
    header: list[str]
    rows: list[list[str]]


@dataclass
class _Staged:
    profile_id: int
    account_id: int
    data: bytes
    file_name: str | None
    stored_at: float = field(default_factory=time.monotonic)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- settings ----------------------------------------------------------------------------------


def get_tolerance(conn: sqlite3.Connection) -> Tolerance:
    """The match tolerance (UC assumption: ±5 % and ±5 days unless changed)."""
    return Tolerance(
        int(get_setting(conn, AMOUNT_PCT_KEY, matching.DEFAULT_AMOUNT_PCT)),
        int(get_setting(conn, DATE_DAYS_KEY, matching.DEFAULT_DATE_DAYS)),
    )


def set_tolerance(conn: sqlite3.Connection, tolerance: Tolerance) -> Tolerance:
    """Store the match tolerance: 0 to 50 percent and 0 to 31 days."""
    if not 0 <= tolerance.amount_pct <= AMOUNT_PCT_MAX or not 0 <= tolerance.date_days <= DATE_DAYS_MAX:
        raise ImportsError(
            N_("Choose an amount tolerance of 0 to %(pct)s percent and a date tolerance of 0 to %(days)s days."),
            pct=AMOUNT_PCT_MAX,
            days=DATE_DAYS_MAX,
        )
    with transaction(conn):
        set_setting(conn, AMOUNT_PCT_KEY, tolerance.amount_pct)
        set_setting(conn, DATE_DAYS_KEY, tolerance.date_days)
    return get_tolerance(conn)


# --- profiles ----------------------------------------------------------------------------------


def _profile(row: sqlite3.Row) -> StoredProfile:
    return StoredProfile(
        bank_name=row["bank_name"],
        column_map=ColumnMap(**json.loads(row["column_map"])),
        delimiter=row["delimiter"],
        encoding=row["encoding"],
        date_format=row["date_format"],
        decimal_sep=row["decimal_sep"],
        skip_rows=row["skip_rows"],
        id=row["id"],
        created_at=row["created_at"],
    )


def _column_map_json(column_map: ColumnMap) -> str:
    return json.dumps(column_map.roles(), ensure_ascii=False, sort_keys=True)


def list_profiles(conn: sqlite3.Connection) -> list[StoredProfile]:
    """All profiles by bank name."""
    return [_profile(row) for row in conn.execute("SELECT * FROM import_profile ORDER BY bank_name COLLATE NOCASE")]


def get_profile(conn: sqlite3.Connection, profile_id: int) -> StoredProfile:
    """Return the profile or raise ``NotFound``."""
    row = conn.execute("SELECT * FROM import_profile WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Import profile not found."))
    return _profile(row)


def _clean(fields: Profile) -> Profile:
    fields = replace(fields, bank_name=fields.bank_name.strip())
    fields.validate()
    return fields


def _duplicate_profile(fields: Profile) -> Conflict:
    return Conflict(N_("An import profile for %(name)s already exists."), name=fields.bank_name)


def create_profile(conn: sqlite3.Connection, fields: Profile) -> StoredProfile:
    """Store a new profile (one per bank name, case-insensitive)."""
    fields = _clean(fields)
    try:
        row = conn.execute(
            "INSERT INTO import_profile (bank_name, delimiter, encoding, date_format, decimal_sep, column_map,"
            " skip_rows, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                fields.bank_name,
                fields.delimiter,
                fields.encoding,
                fields.date_format,
                fields.decimal_sep,
                _column_map_json(fields.column_map),
                fields.skip_rows,
                _now(),
            ),
        ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _duplicate_profile(fields) from exc
    return get_profile(conn, int(row["id"]))


def update_profile(conn: sqlite3.Connection, profile_id: int, fields: Profile) -> StoredProfile:
    """Replace a profile (UC 2a: the bank changed its format). Runs and bookings stay as they are."""
    get_profile(conn, profile_id)
    fields = _clean(fields)
    try:
        conn.execute(
            "UPDATE import_profile SET bank_name = ?, delimiter = ?, encoding = ?, date_format = ?, decimal_sep = ?,"
            " column_map = ?, skip_rows = ? WHERE id = ?",
            (
                fields.bank_name,
                fields.delimiter,
                fields.encoding,
                fields.date_format,
                fields.decimal_sep,
                _column_map_json(fields.column_map),
                fields.skip_rows,
                profile_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise _duplicate_profile(fields) from exc
    return get_profile(conn, profile_id)


def delete_profile(conn: sqlite3.Connection, profile_id: int) -> None:
    """Delete a profile; its runs keep their counts (``profile_id`` becomes NULL)."""
    get_profile(conn, profile_id)
    conn.execute("DELETE FROM import_profile WHERE id = ?", (profile_id,))


# --- detection ---------------------------------------------------------------------------------


def detect(
    data: bytes,
    *,
    encoding: parser.Encoding = "auto",
    delimiter: parser.Delimiter = "auto",
    skip_rows: int = 0,
) -> Detected:
    """Dialect, header and first five records of a file (UC steps 1 and 2); refuses an undetectable dialect."""
    if not 0 <= skip_rows <= 50:
        raise ImportsError(N_("Skip between 0 and 50 lines before the header."))
    used, name, records = parser.read_sample(data, encoding, delimiter, skip_rows)
    return Detected(used, name, records.header, [record for _, record in records.rows])


# --- preview -----------------------------------------------------------------------------------


def _existing_keys(conn: sqlite3.Connection, keys: list[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(keys), KEY_CHUNK):
        chunk = keys[start : start + KEY_CHUNK]
        marks = ",".join("?" * len(chunk))
        found.update(
            row[0]
            for row in conn.execute(f'SELECT import_hash FROM "transaction" WHERE import_hash IN ({marks})', chunk)  # noqa: S608  # nosec B608 — placeholders only
        )
    return found


def _ledger(
    conn: sqlite3.Connection, account_id: int, lines: list[ParsedLine], tolerance: Tolerance
) -> list[LedgerRow]:
    """Planned and booked rows of the account that a line of the file could meet."""
    if not lines:
        return []
    reach = max(tolerance.date_days, matching.SUSPECT_DAYS)
    first = min(line.date for line in lines) - timedelta(days=reach)
    last = max(line.date for line in lines) + timedelta(days=reach)
    rows = conn.execute(
        'SELECT id, date, amount_cents, status, import_hash FROM "transaction"'
        " WHERE account_id = ? AND date BETWEEN ? AND ? AND status IN ('planned', 'booked') ORDER BY date, id",
        (account_id, first.isoformat(), last.isoformat()),
    )
    return [
        LedgerRow(
            row["id"],
            date.fromisoformat(row["date"]),
            row["amount_cents"],
            row["status"],
            row["import_hash"] is not None,
        )
        for row in rows
    ]


def rule_matcher(conn: sqlite3.Connection) -> Callable[[str, str | None], int | None]:
    """``(text, counterparty) -> category_id`` of the rule ``apply_rules`` would pick, without writing.

    Same order and matching as the rule engine (``rules.list_rules``, its search function and text
    cap). The commit runs ``rules.apply_rules`` itself, so this is only the preview's proposal; a
    rule that times out here counts as no match (the engine marks it at commit).
    """
    compiled: list[tuple[int, Callable[[str, float], bool]]] = []
    for rule in rules.list_rules(conn):
        if rule.invalid_reason is not None:
            continue
        try:
            # The engine's private compiler, reused so the preview can never disagree with it.
            compiled.append((rule.category_id, rules._search_function(rule.match_kind, rule.pattern)))
        except regex.error:
            continue

    def match(text: str, counterparty: str | None) -> int | None:
        for category_id, search in compiled:
            try:
                for haystack in (text, counterparty or ""):
                    if search(haystack[: rules.MATCH_TEXT_MAX], rules.MATCH_TIMEOUT_SECONDS):
                        return category_id
            except TimeoutError:
                continue
        return None

    return match


def _planned_categories(conn: sqlite3.Connection, ids: list[int]) -> dict[int, int | None]:
    result: dict[int, int | None] = {}
    for start in range(0, len(ids), KEY_CHUNK):
        chunk = ids[start : start + KEY_CHUNK]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(f'SELECT id, category_id FROM "transaction" WHERE id IN ({marks})', chunk):  # noqa: S608  # nosec B608 — placeholders only
            result[row["id"]] = row["category_id"]
    return result


def _counts(lines: list[PreviewLine], errors: list[LineError]) -> dict[str, int]:
    kinds = [line.kind for line in lines]
    importable = [line for line in lines if line.kind in ("new", "match")]
    return {
        "lines": len(lines) + len(errors),
        "new": kinds.count("new"),
        "matched": kinds.count("match"),
        "duplicates": kinds.count("duplicate"),
        "suspects": kinds.count("duplicate_suspect"),
        "errors": len(errors),
        "rule_hits": sum(1 for line in importable if line.category_source == "rule"),
        "uncategorized": sum(1 for line in importable if line.category_id is None),
    }


def preview(conn: sqlite3.Connection, profile_id: int, account_id: int, data: bytes) -> Preview:
    """Classify every line of the file for the account; nothing is written (UC step 3).

    A line dated before the account's opening date cannot be booked (UC-ZF-001 1a) and is listed
    as a line error; it does not count towards the 10 % refusal threshold, which is about lines
    the profile cannot read.
    """
    profile = get_profile(conn, profile_id)
    account = accounts.get_account(conn, account_id)
    accounts.ensure_bookable(conn, account_id, account.opening_date)  # archived → refused
    parsed = parser.parse_rows(profile, data)
    errors = list(parsed.errors)
    lines: list[ParsedLine] = []
    for line in parsed.lines:
        if line.date < account.opening_date:
            errors.append(
                LineError(
                    line.line,
                    N_("The date %(value)s is before the opening date %(opening)s of the account."),
                    {"value": line.date.isoformat(), "opening": account.opening_date.isoformat()},
                )
            )
        else:
            lines.append(line)
    errors.sort(key=lambda error: error.line)
    keys = parser.assign_keys(account_id, lines)
    tolerance = get_tolerance(conn)
    classified = matching.classify(
        lines, keys, _existing_keys(conn, keys), _ledger(conn, account_id, lines, tolerance), tolerance
    )
    planned_categories = _planned_categories(conn, [c.planned_id for c in classified if c.planned_id is not None])
    match_rule = rule_matcher(conn)
    preview_lines = [_preview_line(item, planned_categories, match_rule) for item in classified]
    return Preview(
        account_id=account_id,
        profile_id=profile_id,
        file_hash=hashlib.sha256(data).hexdigest(),
        encoding=parsed.encoding,
        delimiter=parsed.delimiter,
        lines=preview_lines,
        errors=errors,
        counts=_counts(preview_lines, errors),
    )


def _preview_line(
    item: Classified,
    planned_categories: dict[int, int | None],
    match_rule: Callable[[str, str | None], int | None],
) -> PreviewLine:
    category_id: int | None = None
    source: str | None = None
    if item.planned_id is not None and planned_categories.get(item.planned_id) is not None:
        category_id, source = planned_categories[item.planned_id], "planned"
    elif item.kind in ("new", "match"):
        category_id = match_rule(item.line.text, item.line.counterparty)
        source = "rule" if category_id is not None else None
    return PreviewLine(
        line=item.line.line,
        date=item.line.date,
        amount_cents=item.line.amount_cents,
        text=item.line.text,
        counterparty=item.line.counterparty,
        kind=item.kind,
        import_hash=item.key,
        planned_id=item.planned_id,
        candidates=item.candidates,
        review=item.review,
        suspect_id=item.suspect_id,
        category_id=category_id,
        category_source=source,
    )


# --- staging -----------------------------------------------------------------------------------


class _Stage:
    """Files between preview and commit, in memory: at most ``STAGE_MAX``, each for ``STAGE_TTL_SECONDS``.

    A restart forgets them; the user uploads again. Keeping the file here (not in the database)
    keeps bank statements out of the backups.
    """

    def __init__(self) -> None:
        self._items: OrderedDict[str, _Staged] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, item: _Staged) -> str:
        preview_id = secrets.token_hex(16)
        with self._lock:
            self._expire()
            self._items[preview_id] = item
            while len(self._items) > STAGE_MAX:
                self._items.popitem(last=False)
        return preview_id

    def get(self, preview_id: str) -> _Staged:
        with self._lock:
            self._expire()
            item = self._items.get(preview_id)
        if item is None:
            raise NotFound(N_("The preview has expired. Upload the file again."))
        return item

    def drop(self, preview_id: str) -> None:
        with self._lock:
            self._items.pop(preview_id, None)

    def _expire(self) -> None:
        limit = time.monotonic() - STAGE_TTL_SECONDS
        for key in [key for key, item in self._items.items() if item.stored_at < limit]:
            del self._items[key]


_stage = _Stage()


def stage_preview(
    conn: sqlite3.Connection, profile_id: int, account_id: int, data: bytes, file_name: str | None = None
) -> Preview:
    """``preview`` plus keeping the file for ``commit``; the result carries the ``preview_id``."""
    result = preview(conn, profile_id, account_id, data)
    preview_id = _stage.put(_Staged(profile_id, account_id, data, file_name))
    return replace(result, preview_id=preview_id)


# --- commit ------------------------------------------------------------------------------------


@dataclass
class _Planned:
    """A preview line after the overrides: whether and how it is written."""

    line: PreviewLine
    write: bool
    planned_id: int | None
    category_id: int | None


def _apply_overrides(lines: list[PreviewLine], overrides: list[Override]) -> list[_Planned]:
    by_line = {line.line: line for line in lines}
    plan = {
        line.line: _Planned(
            line,
            line.kind in ("new", "match"),
            line.planned_id,
            line.category_id if line.category_source == "planned" else None,
        )
        for line in lines
    }
    for override in overrides:
        line = by_line.get(override.line)
        if line is None:
            raise Conflict(N_("Line %(line)s is not an importable line of the preview."), line=override.line)
        entry = plan[override.line]
        if override.skip is not None:
            if not override.skip and line.kind == "duplicate":
                raise Conflict(N_("Line %(line)s was imported before and cannot be imported again."), line=line.line)
            entry.write = not override.skip
        if override.category_id is not None:
            entry.category_id = override.category_id
        if override.set_match:
            if override.planned_id is not None and override.planned_id not in line.candidates:
                raise Conflict(
                    N_("The planned item %(id)s does not fit line %(line)s."), id=override.planned_id, line=line.line
                )
            if line.kind == "duplicate":
                raise Conflict(N_("Line %(line)s was imported before and cannot be imported again."), line=line.line)
            entry.planned_id = override.planned_id
    claimed: set[int] = set()
    for entry in plan.values():
        if entry.write and entry.planned_id is not None:
            if entry.planned_id in claimed:
                raise Conflict(N_("The planned item %(id)s is matched by more than one line."), id=entry.planned_id)
            claimed.add(entry.planned_id)
    return sorted(plan.values(), key=lambda entry: entry.line.line)


def _write_line(conn: sqlite3.Connection, account_id: int, run_id: int, entry: _Planned) -> transactions.Transaction:
    line = entry.line
    fields = BookingFields(line.date, line.amount_cents, account_id, entry.category_id, line.text, line.counterparty)
    booking = transactions.create_transaction(conn, fields)
    conn.execute(
        "UPDATE \"transaction\" SET source = 'import', status = 'booked', import_hash = ?, import_run_id = ?"
        " WHERE id = ?",
        (line.import_hash, run_id, booking.id),
    )
    if entry.planned_id is not None:
        return recurring.replace_planned(conn, entry.planned_id, booking.id)
    return transactions.get_transaction(conn, booking.id)


def _check_not_imported(conn: sqlite3.Connection, to_write: list[_Planned]) -> None:
    """Refuse the commit if a line to write was imported since the preview (a second "Import" click).

    Runs under the write lock: the duplicate check of the preview may be stale by then.
    """
    found = _existing_keys(conn, [entry.line.import_hash for entry in to_write])
    first = next((entry.line.line for entry in to_write if entry.line.import_hash in found), None)
    if first is not None:
        raise Conflict(N_("Line %(line)s was imported before and cannot be imported again."), line=first)


def commit(conn: sqlite3.Connection, preview_id: str, overrides: list[Override] | None = None) -> Run:
    """Write the staged file in one transaction (UC steps 5 and 6) and return the run with its counts.

    The preview is recomputed first, so it reflects the ledger at commit time. Nothing is written
    if any line fails (``LineWriteError``) or an override does not fit the preview (``Conflict``).
    """
    staged = _stage.get(preview_id)
    result = preview(conn, staged.profile_id, staged.account_id, staged.data)
    plan = _apply_overrides(result.lines, overrides or [])
    to_write = [entry for entry in plan if entry.write]
    counts = {
        "lines": result.counts["lines"],
        "new": sum(1 for entry in to_write if entry.planned_id is None),
        "matched": sum(1 for entry in to_write if entry.planned_id is not None),
        "duplicates": result.counts["duplicates"],
        "suspects": result.counts["suspects"],
        "skipped": sum(1 for entry in plan if not entry.write and entry.line.kind != "duplicate"),
        "errors": len(result.errors),
    }
    errors_json = json.dumps([{"line": e.line, "msgid": e.msgid, "params": e.params} for e in result.errors])
    with transaction(conn):
        _check_not_imported(conn, to_write)
        run_id = int(
            conn.execute(
                "INSERT INTO import_run (profile_id, account_id, file_name, file_hash, counts, errors, created_at)"
                " VALUES (?, ?, ?, ?, '{}', ?, ?) RETURNING id",
                (staged.profile_id, staged.account_id, staged.file_name, result.file_hash, errors_json, _now()),
            ).fetchone()[0]
        )
        written: list[transactions.Transaction] = []
        for entry in to_write:
            try:
                written.append(_write_line(conn, staged.account_id, run_id, entry))
            except (AccountsError, TransactionsError, RecurringError) as exc:
                raise LineWriteError(entry.line.line, exc) from exc
        rules.apply_rules(conn, written)
        counts["uncategorized"] = int(
            conn.execute(
                'SELECT count(*) FROM "transaction" WHERE import_run_id = ? AND category_id IS NULL'
                " AND transfer_group_id IS NULL",
                (run_id,),
            ).fetchone()[0]
        )
        conn.execute("UPDATE import_run SET counts = ? WHERE id = ?", (json.dumps(counts), run_id))
    _stage.drop(preview_id)
    if to_write:
        debts.sync_debts(conn)
    return get_run(conn, run_id)


# --- runs --------------------------------------------------------------------------------------


def _run(row: sqlite3.Row) -> Run:
    errors = [LineError(item["line"], item["msgid"], item["params"]) for item in json.loads(row["errors"])]
    return Run(
        id=row["id"],
        account_id=row["account_id"],
        profile_id=row["profile_id"],
        file_name=row["file_name"],
        file_hash=row["file_hash"],
        counts=json.loads(row["counts"]),
        errors=errors,
        created_at=row["created_at"],
    )


def list_runs(conn: sqlite3.Connection, *, account_id: int | None = None, limit: int = 50) -> list[Run]:
    """The latest runs, newest first, optionally of one account."""
    rows = conn.execute(
        "SELECT * FROM import_run WHERE ? IS NULL OR account_id = ? ORDER BY id DESC LIMIT ?",
        (account_id, account_id, limit),
    )
    return [_run(row) for row in rows]


def get_run(conn: sqlite3.Connection, run_id: int) -> Run:
    """Return the run or raise ``NotFound``."""
    row = conn.execute("SELECT * FROM import_run WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Import run not found."))
    return _run(row)
