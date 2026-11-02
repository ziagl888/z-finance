"""Reading a bank CSV: dialect detection, profile-driven parsing, the duplicate key. Pure functions.

Nothing here touches the database, so every rule is tested on bytes and plain values
(``tests/test_import.py``). The service combines these with the ledger.

Invariants:

* Amounts become integer cents through ``Decimal`` and ``money.to_cents``; a float never exists.
* Encoding and delimiter are never guessed: with the profile value ``auto`` a file whose encoding
  or delimiter cannot be detected for sure is refused, and the user sets it in the profile (UC 1a).
* A file is refused as a whole (``ImportFileError``) when a mapped column is missing, it is empty,
  has more than ``MAX_LINES`` data lines, or more than ``MAX_ERROR_PERCENT`` of its lines fail;
  otherwise every failing line is reported (``LineError``) and nothing is dropped silently.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from itertools import islice
from typing import Any, Literal

from zfinance.i18n import N_
from zfinance.money import AmountTooLargeError, to_cents

Delimiter = Literal["auto", "semicolon", "comma", "tab"]
Encoding = Literal["auto", "utf-8", "cp1252"]
DecimalSep = Literal[",", "."]

DELIMITER_CHARS: dict[str, str] = {"semicolon": ";", "comma": ",", "tab": "\t"}
DELIMITER_NAMES: dict[str, str] = {char: name for name, char in DELIMITER_CHARS.items()}

#: Largest accepted file (UC: up to 500 lines per file; 5 MB leaves room for long texts).
MAX_FILE_BYTES = 5 * 1024 * 1024
#: Most data lines per file.
MAX_LINES = 5_000
#: A file with more failing lines than this share (percent) is refused (UC Fehlerverhalten).
MAX_ERROR_PERCENT = 10
#: Longest stored booking text and counterparty; a bank cell beyond is cut.
TEXT_MAX = 500
COUNTERPARTY_MAX = 200
#: Records the delimiter detection looks at: the header and the first data lines.
DETECT_RECORDS = 6
#: Records ``read_sample`` returns after the header (UC step 2: preview of the first five lines).
SAMPLE_RECORDS = 5

_UTF8_BOM = b"\xef\xbb\xbf"
_DATE_FORMAT = re.compile(r"(?:%[dmYy%]|[^%])+")
_BLANKS = re.compile(r"\s+")
_CURRENCY = re.compile(r"€|\bEUR\b", re.IGNORECASE)


# --- errors ------------------------------------------------------------------------------------


class ImportFileError(Exception):
    """The whole file (or profile) is refused; ``msgid`` is an ``N_()`` id filled from ``params``.

    ``status_code`` is the HTTP status the API answers with.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class FileTooLarge(ImportFileError):
    """The upload exceeds ``MAX_FILE_BYTES``."""

    status_code = 413


@dataclass(frozen=True)
class LineError:
    """A data line that cannot be imported; ``line`` is its first physical line in the file."""

    line: int
    msgid: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        """The English text."""
        return self.msgid % self.params if self.params else self.msgid


# --- profile -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnMap:
    """Header names per role: ``date``, ``text`` and either ``amount`` or ``debit`` + ``credit``."""

    date: str
    text: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    counterparty: str | None = None

    def validate(self) -> None:
        """Refuse a map without date/text, or without exactly one amount form."""
        if not self.date.strip() or not self.text.strip():
            raise ImportFileError(N_("Map the date and the text column."))
        split = bool(self.debit and self.debit.strip()) or bool(self.credit and self.credit.strip())
        single = bool(self.amount and self.amount.strip())
        if single == split or (split and not (self.debit and self.credit)):
            raise ImportFileError(N_("Map either one amount column or both a debit and a credit column."))

    def roles(self) -> dict[str, str]:
        """Mapped roles and their header names, blanks left out."""
        values = {
            "date": self.date,
            "amount": self.amount,
            "debit": self.debit,
            "credit": self.credit,
            "text": self.text,
            "counterparty": self.counterparty,
        }
        return {role: name.strip() for role, name in values.items() if name and name.strip()}


@dataclass(frozen=True)
class Profile:
    """How one bank's CSV export is read (table ``import_profile``)."""

    bank_name: str
    column_map: ColumnMap
    delimiter: Delimiter = "auto"
    encoding: Encoding = "auto"
    date_format: str = "%d.%m.%Y"
    decimal_sep: DecimalSep = ","
    skip_rows: int = 0

    def validate(self) -> None:
        """Refuse a blank bank name, an unusable date format, a bad map or skip count."""
        if not self.bank_name.strip():
            raise ImportFileError(N_("Enter the name of the bank."))
        check_date_format(self.date_format)
        self.column_map.validate()
        if not 0 <= self.skip_rows <= 50:
            raise ImportFileError(N_("Skip between 0 and 50 lines before the header."))


def check_date_format(date_format: str) -> None:
    """Accept only day/month/year directives (``%d %m %Y %y``) and literal separators, round-trip safe."""
    sample = date(2026, 12, 31)
    has_parts = "%d" in date_format and "%m" in date_format and ("%Y" in date_format or "%y" in date_format)
    if not has_parts or not _DATE_FORMAT.fullmatch(date_format):
        raise ImportFileError(
            N_("The date format %(format)s needs day, month and year, e.g. %%d.%%m.%%Y."), format=date_format
        )
    if datetime.strptime(sample.strftime(date_format), date_format).date() != sample:
        raise ImportFileError(
            N_("The date format %(format)s needs day, month and year, e.g. %%d.%%m.%%Y."), format=date_format
        )


# --- dialect -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Dialect:
    """Detected encoding and delimiter name; ``None`` = not detectable for sure."""

    encoding: str | None
    delimiter: str | None


def detect_encoding(data: bytes) -> str | None:
    """``utf-8`` for a BOM or valid UTF-8 (ASCII included), ``cp1252`` if only that decodes, else None."""
    if data.startswith(_UTF8_BOM):
        return "utf-8"
    for encoding in ("utf-8", "cp1252"):
        try:
            data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding
    return None


def detect_delimiter(text: str, skip_rows: int = 0) -> str | None:
    """The delimiter name that splits header and first lines into one equal count (≥ 2) of fields.

    Several candidates → the one giving the most fields; a tie or none → None (not guessed).
    """
    scores: dict[str, int] = {}
    for name, char in DELIMITER_CHARS.items():
        try:
            records = [
                r
                for r in islice(
                    csv.reader(io.StringIO(text, newline=""), delimiter=char), skip_rows, skip_rows + DETECT_RECORDS
                )
                if any(cell.strip() for cell in r)
            ]
        except csv.Error:
            continue
        counts = {len(record) for record in records}
        if len(counts) == 1 and (width := counts.pop()) >= 2:
            scores[name] = width
    if not scores:
        return None
    best = max(scores.values())
    winners = [name for name, width in scores.items() if width == best]
    return winners[0] if len(winners) == 1 else None


def detect_dialect(data: bytes, skip_rows: int = 0) -> Dialect:
    """Encoding and delimiter of a file as far as they are certain (UC step 1)."""
    encoding = detect_encoding(data)
    if encoding is None:
        return Dialect(None, None)
    return Dialect(encoding, detect_delimiter(_strip_bom(data).decode(encoding), skip_rows))


def _strip_bom(data: bytes) -> bytes:
    return data[len(_UTF8_BOM) :] if data.startswith(_UTF8_BOM) else data


def decode(data: bytes, encoding: Encoding) -> tuple[str, str]:
    """Decode strictly with the profile's encoding (``auto`` = detect); return (text, encoding used)."""
    if len(data) > MAX_FILE_BYTES:
        raise FileTooLarge(N_("The file is larger than %(max)s MB."), max=MAX_FILE_BYTES // (1024 * 1024))
    used = detect_encoding(data) if encoding == "auto" else encoding
    if used is None:
        raise ImportFileError(N_("The encoding of the file could not be detected. Choose it in the import profile."))
    try:
        return _strip_bom(data).decode(used), used
    except UnicodeDecodeError as exc:
        raise ImportFileError(
            N_("The file is not valid %(encoding)s text. Check the encoding in the import profile."), encoding=used
        ) from exc


def _delimiter(text: str, profile_delimiter: Delimiter, skip_rows: int) -> str:
    name = detect_delimiter(text, skip_rows) if profile_delimiter == "auto" else profile_delimiter
    if name is None:
        raise ImportFileError(N_("The column separator could not be detected. Choose it in the import profile."))
    return name


# --- records -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Records:
    """The header and the non-blank data records with their first physical line number."""

    header: list[str]
    rows: list[tuple[int, list[str]]]


def read_records(text: str, delimiter: str, skip_rows: int, *, limit: int | None = MAX_LINES) -> Records:
    """Split the text into records: skip ``skip_rows`` records, take the header, then the data.

    Blank records (every cell empty) are not data and are left out. More than ``limit`` data
    records refuse the file.
    """
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=DELIMITER_CHARS[delimiter])
    header: list[str] | None = None
    rows: list[tuple[int, list[str]]] = []
    previous_end = 0
    try:
        for index, record in enumerate(reader):
            start, previous_end = previous_end + 1, reader.line_num
            if index < skip_rows:
                continue
            if header is None:
                if any(cell.strip() for cell in record):
                    header = [cell.strip() for cell in record]
                continue
            if not any(cell.strip() for cell in record):
                continue
            if limit is not None and len(rows) >= limit:
                raise ImportFileError(N_("The file has more than %(max)s lines."), max=limit)
            rows.append((start, record))
    except csv.Error as exc:
        raise ImportFileError(N_("The file is not a readable CSV file (line %(line)s)."), line=reader.line_num) from exc
    if header is None:
        raise ImportFileError(N_("The file is empty."))
    return Records(header, rows)


def column_indexes(header: list[str], column_map: ColumnMap) -> dict[str, int]:
    """Index of each mapped role in the header (case-insensitive, first occurrence); missing → refused."""
    positions: dict[str, int] = {}
    for index, name in enumerate(header):
        positions.setdefault(name.casefold(), index)
    indexes: dict[str, int] = {}
    for role, name in column_map.roles().items():
        if name.casefold() not in positions:
            raise ImportFileError(N_("The column %(column)s is missing in the file."), column=name)
        indexes[role] = positions[name.casefold()]
    return indexes


# --- values ------------------------------------------------------------------------------------


def parse_amount(raw: str, decimal_sep: DecimalSep) -> int:
    """Exact cents from a bank amount (``-1.234,56`` with ``,``; ``-1,234.56`` with ``.``).

    The other separator is accepted only as a thousands separator in groups of three; blanks,
    ``€``/``EUR``, a leading ``+`` and a trailing ``-`` (``12,50-``) are understood.

    Raises:
        AmountTooLargeError: beyond ``MAX_CENTS`` (a ``ValueError``).
        ValueError: anything else, including more than two decimals.
    """
    value = _CURRENCY.sub("", raw).replace("\u00a0", "").replace(" ", "").strip()
    sign = ""
    if value.endswith("-"):
        sign, value = "-", value[:-1]
    if value[:1] in "+-" and value:
        sign, value = ("-" if value[0] == "-" else sign), value[1:]
    thousands = "." if decimal_sep == "," else ","
    pattern = rf"(\d{{1,3}}(?:{re.escape(thousands)}\d{{3}})+|\d+)(?:{re.escape(decimal_sep)}(\d{{1,2}}))?"
    match = re.fullmatch(pattern, value)
    if match is None:
        raise ValueError(f"not an amount: {raw!r}")
    whole = match.group(1).replace(thousands, "")
    return to_cents(Decimal(f"{sign}{whole}.{match.group(2) or '0'}"))


def parse_date(raw: str, date_format: str) -> date:
    """A calendar day in the profile's format; ``ValueError`` if it does not fit."""
    return datetime.strptime(raw.strip(), date_format).date()


def normalize_text(text: str) -> str:
    """Casefolded text with whitespace runs collapsed: the text part of the duplicate key."""
    return _BLANKS.sub(" ", text).strip().casefold()


def dedupe_key(account_id: int, day: date, amount_cents: int, text: str, occurrence: int = 1) -> str:
    """The duplicate key: sha256 over account, date, amount and normalised text (UC Schnittstellen).

    ``occurrence`` numbers identical lines inside one file (1, 2, …) so that two equal payments on
    one day stay two bookings; the first one keeps the plain key.
    """
    parts = f"{account_id}|{day.isoformat()}|{amount_cents}|{normalize_text(text)}"
    if occurrence > 1:
        parts += f"#{occurrence}"
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


# --- parsing -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedLine:
    """One readable data line."""

    line: int
    date: date
    amount_cents: int
    text: str
    counterparty: str | None


@dataclass(frozen=True)
class ParseResult:
    """Readable lines, failing lines, and the dialect that was used."""

    lines: list[ParsedLine]
    errors: list[LineError]
    encoding: str
    delimiter: str
    header: list[str]


def _cell(record: list[str], index: int | None) -> str:
    if index is None or index >= len(record):
        return ""
    return record[index].strip()


def _amount_or_error(line: int, raw: str, decimal_sep: DecimalSep) -> int | LineError:
    """Cents of a cell, or the line error that tells the user why the cell is refused."""
    try:
        return parse_amount(raw, decimal_sep)
    except AmountTooLargeError:
        return LineError(line, N_("The amount %(value)s is too large."), {"value": raw})
    except ValueError:
        return LineError(line, N_("The amount %(value)s is not a valid amount."), {"value": raw})


def _parse_line(line: int, record: list[str], indexes: dict[str, int], profile: Profile) -> ParsedLine | LineError:
    raw_date = _cell(record, indexes["date"])
    try:
        day = parse_date(raw_date, profile.date_format)
    except ValueError:
        return LineError(
            line,
            N_("The date %(value)s does not match the format %(format)s."),
            {"value": raw_date, "format": profile.date_format},
        )
    if "amount" in indexes:
        raw = _cell(record, indexes["amount"])
        parsed = _amount_or_error(line, raw, profile.decimal_sep)
        if isinstance(parsed, LineError):
            return parsed
        cents = parsed
    else:
        debit, credit = _cell(record, indexes["debit"]), _cell(record, indexes["credit"])
        if bool(debit) == bool(credit):
            return LineError(line, N_("Fill exactly one of the debit and the credit column."))
        parsed = _amount_or_error(line, debit or credit, profile.decimal_sep)
        if isinstance(parsed, LineError):
            return parsed
        cents = -abs(parsed) if debit else abs(parsed)
    if cents == 0:
        return LineError(line, N_("The amount is 0."))
    counterparty = _cell(record, indexes.get("counterparty"))[:COUNTERPARTY_MAX] or None
    return ParsedLine(line, day, cents, _cell(record, indexes["text"])[:TEXT_MAX], counterparty)


def parse_rows(profile: Profile, data: bytes) -> ParseResult:
    """Parse a bank CSV with a profile into lines and line errors, or refuse the whole file.

    Refused (``ImportFileError``): undecodable or undetectable dialect, missing mapped column (its
    name in the message, UC AK4), no data line, more than ``MAX_LINES`` lines, more than
    ``MAX_ERROR_PERCENT`` % failing lines.
    """
    text, encoding = decode(data, profile.encoding)
    delimiter = _delimiter(text, profile.delimiter, profile.skip_rows)
    records = read_records(text, delimiter, profile.skip_rows)
    indexes = column_indexes(records.header, profile.column_map)
    if not records.rows:
        raise ImportFileError(N_("The file contains no lines to import."))
    lines: list[ParsedLine] = []
    errors: list[LineError] = []
    for line, record in records.rows:
        parsed = _parse_line(line, record, indexes, profile)
        (errors if isinstance(parsed, LineError) else lines).append(parsed)  # type: ignore[arg-type]
    if len(errors) * 100 > MAX_ERROR_PERCENT * len(records.rows):
        raise ImportFileError(
            N_("%(errors)s of %(lines)s lines could not be read, more than 10 percent. Check the import profile."),
            errors=len(errors),
            lines=len(records.rows),
        )
    return ParseResult(lines, errors, encoding, delimiter, records.header)


def assign_keys(account_id: int, lines: list[ParsedLine]) -> list[str]:
    """Duplicate keys for the lines of one file, numbering identical lines in file order."""
    seen: dict[tuple[date, int, str], int] = {}
    keys: list[str] = []
    for line in lines:
        identity = (line.date, line.amount_cents, normalize_text(line.text))
        seen[identity] = seen.get(identity, 0) + 1
        keys.append(dedupe_key(account_id, line.date, line.amount_cents, line.text, seen[identity]))
    return keys


def read_sample(data: bytes, encoding: Encoding, delimiter: Delimiter, skip_rows: int) -> tuple[str, str, Records]:
    """Header and first ``SAMPLE_RECORDS`` records for mapping columns (UC step 2); (encoding, delimiter, records)."""
    text, used = decode(data, encoding)
    name = _delimiter(text, delimiter, skip_rows)
    records = read_records(text, name, skip_rows, limit=None)
    return used, name, Records(records.header, records.rows[:SAMPLE_RECORDS])
