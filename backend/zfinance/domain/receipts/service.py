"""Receipts (UC-ZF-013, API part): plain functions over a ``sqlite3.Connection``.

The receipt agent reads a photo (date, total, merchant, payment method, category suggestion,
confidence) and hands photo and fields to ``submit_receipt``. The outcome, in this order:

1. The same photo (sha256 of the bytes) is already stored → ``duplicate``; nothing is written and
   the answer names the stored receipt (UC 5a; a repeated agent run is therefore harmless).
2. Something is unsure → a ``review`` receipt with a reason; no booking (UC 3a-3c, AK3).
3. Same date, total and merchant as a receipt that is ``booked`` or waits for review → a
   ``duplicate`` receipt pointing at it; the photo is kept, no booking (AK2).
4. Card (or unknown) payment and an expense booking of the same amount within ±3 days on an
   account of the area, without a receipt → the receipt is attached to that booking (UC 5b).
5. Otherwise a new booking through the transactions service (``source='receipt'``), with the
   category from the message hint or the suggestion, else from the rules (UC-ZF-007).

The message text (``hints``) overrides the agent: ``business``/``private`` choose the area,
``bar``/``cash`` and ``karte``/``card`` the payment method, a category name the category.

Photos are stored at ``<data dir>/receipts/<year of upload>/<receipt id>.<ext>``. Every part of
that path is a server value: the type comes from the file's magic bytes, never from the request.

Invariants:

* A stored file always lies inside ``<data dir>/receipts`` (``_inside_receipts`` is checked on
  every write, read and delete), and a receipt row exists for every file this module writes: the
  file is written inside the database transaction and removed again when the transaction fails.
* ``status = 'booked'`` is only set together with a ``transaction_id``; the booking can later be
  deleted by the user (the foreign key then clears ``transaction_id``).
* ``image_hash`` is unique: one photo is stored once.

Errors are ``ReceiptsError`` subclasses carrying an ``N_()`` msgid, like the other features;
``AccountsError`` and ``TransactionsError`` from the services called here pass through.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from zfinance.app_settings import get_setting, set_setting
from zfinance.db import transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.rules import service as rules
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.i18n import N_

logger = logging.getLogger(__name__)

Status = Literal["booked", "review", "duplicate"]
PaymentMethod = Literal["cash", "card", "unknown"]
Area = Literal["private", "business"]
Reason = Literal["unreadable", "missing_fields", "foreign_currency", "low_confidence", "no_account", "not_bookable"]

#: Largest photo accepted (bytes). Phone photos of a receipt are 1-5 MB.
MAX_PHOTO_BYTES = 10 * 1024 * 1024
#: A receipt read with less confidence than this goes to the review list (setting overrides).
DEFAULT_CONFIDENCE_THRESHOLD = 0.8
#: A card receipt matches an existing booking up to this many days apart (UC 5b).
CARD_MATCH_DAYS = 3
#: Sub-directory of the data directory holding the photos.
RECEIPTS_DIR = "receipts"

SETTING_THRESHOLD = "receipts.confidence_threshold"
SETTING_ACCOUNTS = "receipts.accounts"

# Magic bytes of the accepted photo types → (media type, file extension).
_JPEG = (b"\xff\xd8\xff", "image/jpeg", "jpg")
_PNG = (b"\x89PNG\r\n\x1a\n", "image/png", "png")

_PAYMENT_WORDS: dict[str, PaymentMethod] = {
    "cash": "cash",
    "bar": "cash",
    "card": "card",
    "karte": "card",
    "unknown": "unknown",
    "unbekannt": "unknown",
}
_AREA_WORDS: dict[str, Area] = {
    "business": "business",
    "geschäftlich": "business",
    "private": "private",
    "privat": "private",
}


# --- errors ------------------------------------------------------------------------------------


class ReceiptsError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(ReceiptsError):
    """The receipt, its photo or the agent run does not exist."""

    status_code = 404


class Conflict(ReceiptsError):
    """The request contradicts the receipt's state."""

    status_code = 409


class UnsupportedPhoto(ReceiptsError):
    """The upload is not a JPEG, PNG or WebP image (by its bytes)."""

    status_code = 415


class PhotoTooLarge(ReceiptsError):
    """The upload is larger than ``MAX_PHOTO_BYTES``."""

    status_code = 413


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Extracted:
    """The fields the agent read from one photo; every one may be missing.

    ``amount_cents`` is the receipt total as a positive number; the booking gets the negative
    amount (an expense). ``hints`` is the text of the message the photo came with.
    """

    date: date | None = None
    amount_cents: int | None = None
    merchant: str | None = None
    payment_method: PaymentMethod = "unknown"
    category_suggestion: str | None = None
    confidence: float | None = None
    currency: str = "EUR"
    hints: str = ""
    unreadable: bool = False
    line_items: Any = None

    def to_json(self) -> str:
        """The fields as stored in ``receipt.extracted_json``."""
        return json.dumps(
            {
                "date": self.date.isoformat() if self.date else None,
                "amount_cents": self.amount_cents,
                "merchant": self.merchant,
                "payment_method": self.payment_method,
                "category_suggestion": self.category_suggestion,
                "confidence": self.confidence,
                "currency": self.currency,
                "hints": self.hints,
                "unreadable": self.unreadable,
                "line_items": self.line_items,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class Receipt:
    """A stored receipt with the key figures of its booking (``booking_*``, None without one)."""

    id: int
    status: Status
    reason: Reason | None
    transaction_id: int | None
    media_type: str
    confidence: float | None
    extracted: dict[str, Any]
    duplicate_of: int | None
    message_ref: str | None
    run_id: int | None
    created_at: str
    booked_at: str | None
    booking_date: date | None = None
    booking_amount_cents: int | None = None
    booking_text: str | None = None
    booking_account_id: int | None = None


@dataclass(frozen=True)
class SubmitResult:
    """Outcome of ``submit_receipt``.

    ``receipt_id`` is None when nothing was stored (the same photo exists: ``duplicate_of`` names
    it). ``matched`` = the receipt was attached to an existing booking instead of a new one.
    """

    status: Status
    receipt_id: int | None
    transaction_id: int | None = None
    reason: Reason | None = None
    duplicate_of: int | None = None
    matched: bool = False


@dataclass(frozen=True)
class Correction:
    """What the user confirms on the review list; ``amount_cents`` is the positive total."""

    date: date
    amount_cents: int
    merchant: str
    account_id: int
    category_id: int | None = None


@dataclass(frozen=True)
class ReceiptSettings:
    """Review threshold and the account per area and payment method (None = automatic)."""

    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    accounts: dict[str, dict[str, int | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRun:
    """One run of the receipt agent; ``booked``/``review``/``duplicate`` count its receipts."""

    id: int
    started_at: str
    finished_at: str | None
    counts: dict[str, Any]
    note: str | None
    booked: int = 0
    review: int = 0
    duplicate: int = 0


@dataclass(frozen=True)
class Hints:
    """What the message text says: area, payment method, category (None = not said)."""

    area: Area | None = None
    payment_method: PaymentMethod | None = None
    category_id: int | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- photos ------------------------------------------------------------------------------------


def detect_photo_type(data: bytes) -> tuple[str, str] | None:
    """Return (media type, extension) of a JPEG, PNG or WebP image by its magic bytes, else None."""
    for magic, media_type, extension in (_JPEG, _PNG):
        if data.startswith(magic):
            return media_type, extension
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def _receipts_root(data_dir: Path) -> Path:
    return (data_dir / RECEIPTS_DIR).resolve()


def _inside_receipts(data_dir: Path, relative: str) -> Path:
    """Resolve a stored relative path and refuse anything outside ``<data dir>/receipts``."""
    root = _receipts_root(data_dir)
    path = (data_dir / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise NotFound(N_("Receipt photo not found."))
    return path


def photo_file(conn: sqlite3.Connection, data_dir: Path, receipt_id: int) -> tuple[Path, str]:
    """Return (file, media type) of a receipt's photo, or ``NotFound``."""
    row = conn.execute("SELECT file_path, media_type FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Receipt not found."))
    path = _inside_receipts(data_dir, row["file_path"])
    if not path.is_file():
        raise NotFound(N_("Receipt photo not found."))
    return path, row["media_type"]


# --- settings ----------------------------------------------------------------------------------


def get_receipt_settings(conn: sqlite3.Connection) -> ReceiptSettings:
    """Stored settings; defaults for anything not set."""
    threshold = get_setting(conn, SETTING_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD)
    mapping = get_setting(conn, SETTING_ACCOUNTS, {}) or {}
    return ReceiptSettings(
        confidence_threshold=float(threshold),
        accounts={
            area: {method: (mapping.get(area) or {}).get(method) for method in ("cash", "card")}
            for area in ("private", "business")
        },
    )


def set_receipt_settings(conn: sqlite3.Connection, value: ReceiptSettings) -> ReceiptSettings:
    """Store threshold (0..1) and account mapping; a mapped account must be active and of that area."""
    if not 0 <= value.confidence_threshold <= 1:
        raise ReceiptsError(N_("Enter a threshold between 0 and 1."))
    mapping: dict[str, dict[str, int | None]] = {}
    for area in ("private", "business"):
        mapping[area] = {}
        for method in ("cash", "card"):
            account_id = (value.accounts.get(area) or {}).get(method)
            if account_id is not None:
                account = accounts.get_account(conn, account_id)
                if account.archived_at is not None or account.area != area:
                    raise ReceiptsError(N_("Choose an active account of the matching area."))
            mapping[area][method] = account_id
    with transaction(conn):
        set_setting(conn, SETTING_THRESHOLD, value.confidence_threshold)
        set_setting(conn, SETTING_ACCOUNTS, mapping)
    return get_receipt_settings(conn)


def resolve_account(conn: sqlite3.Connection, area: Area, method: PaymentMethod, value: ReceiptSettings) -> int | None:
    """Account for a receipt of ``area`` paid by ``method``.

    The mapped account wins; otherwise the only active account of the area with type ``cash``
    (cash) or ``card``, then ``giro`` (card and unknown). None when there is none or several.
    """
    key = "cash" if method == "cash" else "card"
    mapped = value.accounts.get(area, {}).get(key)
    if mapped is not None:
        return mapped
    candidates = [a for a in accounts.list_accounts(conn) if a.area == area]
    for account_type in ("cash",) if method == "cash" else ("card", "giro"):
        of_type = [a.id for a in candidates if a.type == account_type]
        if len(of_type) == 1:
            return of_type[0]
        if of_type:
            return None
    return None


# --- hints -------------------------------------------------------------------------------------


def normalize_payment_method(value: str | None) -> PaymentMethod:
    """``cash``/``bar``, ``card``/``karte`` or ``unknown`` for anything else (case-insensitive)."""
    return _PAYMENT_WORDS.get((value or "").strip().casefold(), "unknown")


def parse_hints(conn: sqlite3.Connection, text: str) -> Hints:
    """Read area, payment method and category from the message text (UC step 1/3).

    Keywords are whole words, case-insensitive. The category is an expense category whose name
    equals the text left after removing the keywords, or equals one of its words.
    """
    words = re.findall(r"\w+", text.casefold())
    area: Area | None = next((_AREA_WORDS[w] for w in words if w in _AREA_WORDS), None)
    method: PaymentMethod | None = next((_PAYMENT_WORDS[w] for w in words if w in _PAYMENT_WORDS), None)
    rest = [w for w in words if w not in _AREA_WORDS and w not in _PAYMENT_WORDS]
    category_id = None
    if rest:
        by_name = {
            " ".join(re.findall(r"\w+", c.name.casefold())): c.id
            for c in accounts.list_categories(conn)
            if c.kind == "expense"
        }
        category_id = by_name.get(" ".join(rest)) or next((by_name[w] for w in rest if w in by_name), None)
    return Hints(area, method, category_id)


def _category_by_name(conn: sqlite3.Connection, name: str | None) -> int | None:
    if not name or not name.strip():
        return None
    wanted = name.strip().casefold()
    return next(
        (c.id for c in accounts.list_categories(conn) if c.kind == "expense" and c.name.casefold() == wanted),
        None,
    )


# --- reading -----------------------------------------------------------------------------------

_SELECT = (
    "SELECT r.*, t.date AS booking_date, t.amount_cents AS booking_amount_cents, t.text AS booking_text,"
    ' t.account_id AS booking_account_id FROM receipt r LEFT JOIN "transaction" t ON t.id = r.transaction_id'
)


def _receipt(row: sqlite3.Row) -> Receipt:
    return Receipt(
        id=row["id"],
        status=row["status"],
        reason=row["reason"],
        transaction_id=row["transaction_id"],
        media_type=row["media_type"],
        confidence=row["confidence"],
        extracted=json.loads(row["extracted_json"]),
        duplicate_of=row["duplicate_of"],
        message_ref=row["message_ref"],
        run_id=row["run_id"],
        created_at=row["created_at"],
        booked_at=row["booked_at"],
        booking_date=date.fromisoformat(row["booking_date"]) if row["booking_date"] else None,
        booking_amount_cents=row["booking_amount_cents"],
        booking_text=row["booking_text"],
        booking_account_id=row["booking_account_id"],
    )


def get_receipt(conn: sqlite3.Connection, receipt_id: int) -> Receipt:
    """Return the receipt or raise ``NotFound``."""
    row = conn.execute(f"{_SELECT} WHERE r.id = ?", (receipt_id,)).fetchone()  # nosec B608 — constant
    if row is None:
        raise NotFound(N_("Receipt not found."))
    return _receipt(row)


def list_receipts(conn: sqlite3.Connection, statuses: list[Status], *, limit: int = 100) -> list[Receipt]:
    """Receipts with one of ``statuses``, newest first."""
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"{_SELECT} WHERE r.status IN ({marks}) ORDER BY r.id DESC LIMIT ?",  # nosec B608 — placeholders only
        (*statuses, limit),
    )
    return [_receipt(row) for row in rows]


# --- submitting --------------------------------------------------------------------------------


def _duplicate_key(fields: Extracted) -> str | None:
    if fields.date is None or fields.amount_cents is None or not (fields.merchant or "").strip():
        return None
    return f"{fields.date.isoformat()}|{fields.amount_cents}|{(fields.merchant or '').strip().casefold()}"


def review_reason(fields: Extracted, threshold: float) -> Reason | None:
    """Why a receipt cannot be booked as read, or None (UC 3a-3c, AK3)."""
    if fields.unreadable:
        return "unreadable"
    if (
        fields.date is None
        or fields.amount_cents is None
        or fields.amount_cents <= 0
        or not (fields.merchant or "").strip()
    ):
        return "missing_fields"
    if fields.currency.strip().upper() != "EUR":
        return "foreign_currency"
    if fields.confidence is None or fields.confidence < threshold:
        return "low_confidence"
    return None


def find_card_match(conn: sqlite3.Connection, area: Area, amount_cents: int, on: date) -> int | None:
    """An expense booking of ``-amount_cents`` within ±3 days on an account of ``area`` (UC 5b).

    Planned and booked rows count (UC 5b: "eine geplante oder importierte Buchung"); when a planned
    row is later confirmed or replaced by an import, ``recurring.replace_planned`` moves the receipt
    to the real booking. Transfer legs and bookings that already have a receipt are left out; the
    closest date wins, then the booking entered first.
    """
    row = conn.execute(
        'SELECT t.id FROM "transaction" t JOIN account a ON a.id = t.account_id'
        " WHERE a.area = ? AND t.amount_cents = ? AND t.transfer_group_id IS NULL AND t.status != 'skipped'"
        " AND abs(julianday(t.date) - julianday(?)) <= ?"
        " AND NOT EXISTS (SELECT 1 FROM receipt r WHERE r.transaction_id = t.id)"
        " ORDER BY abs(julianday(t.date) - julianday(?)), t.id LIMIT 1",
        (area, -amount_cents, on.isoformat(), CARD_MATCH_DAYS, on.isoformat()),
    ).fetchone()
    return None if row is None else int(row["id"])


def _book(conn: sqlite3.Connection, booking: Correction) -> int:
    """A new expense booking written through the transactions service, tagged ``source='receipt'``."""
    fields = BookingFields(
        booking.date, -booking.amount_cents, booking.account_id, booking.category_id, booking.merchant.strip()
    )
    created = transactions.create_transaction(conn, fields)
    conn.execute("UPDATE \"transaction\" SET source = 'receipt' WHERE id = ?", (created.id,))
    if booking.category_id is None:
        rules.apply_rules(conn, [created])
    return created.id


@dataclass
class _Plan:
    """What ``submit_receipt`` decided before writing anything."""

    status: Status
    reason: Reason | None = None
    duplicate_of: int | None = None
    matched_id: int | None = None
    booking: Correction | None = None


def _plan(conn: sqlite3.Connection, fields: Extracted) -> _Plan:
    settings = get_receipt_settings(conn)
    reason = review_reason(fields, settings.confidence_threshold)
    if reason is not None:
        return _Plan("review", reason)
    key = _duplicate_key(fields)
    earlier = conn.execute(
        "SELECT id FROM receipt WHERE duplicate_key = ? AND status IN ('booked', 'review') ORDER BY id LIMIT 1",
        (key,),
    ).fetchone()
    if earlier is not None:
        return _Plan("duplicate", duplicate_of=int(earlier["id"]))
    day, total, merchant = fields.date, fields.amount_cents, fields.merchant
    if day is None or total is None or merchant is None:  # excluded by review_reason; narrows the types
        return _Plan("review", "missing_fields")
    hints = parse_hints(conn, fields.hints)
    area: Area = hints.area or "private"
    method = hints.payment_method or fields.payment_method
    if method != "cash":
        matched = find_card_match(conn, area, total, day)
        if matched is not None:
            return _Plan("booked", matched_id=matched)
    account_id = resolve_account(conn, area, method, settings)
    if account_id is None:
        return _Plan("review", "no_account")
    try:
        accounts.ensure_bookable(conn, account_id, day)
    except AccountsError:
        return _Plan("review", "not_bookable")
    category_id = hints.category_id or _category_by_name(conn, fields.category_suggestion)
    return _Plan("booked", booking=Correction(day, total, merchant, account_id, category_id))


def _same_photo(conn: sqlite3.Connection, image_hash: str) -> SubmitResult | None:
    row = conn.execute("SELECT id, transaction_id FROM receipt WHERE image_hash = ?", (image_hash,)).fetchone()
    return None if row is None else SubmitResult("duplicate", None, row["transaction_id"], duplicate_of=row["id"])


def submit_receipt(
    conn: sqlite3.Connection,
    data_dir: Path,
    photo: bytes,
    fields: Extracted,
    *,
    message_ref: str | None = None,
    run_id: int | None = None,
) -> SubmitResult:
    """Store a receipt photo with the agent's fields and book it if nothing is unsure (see module doc)."""
    if len(photo) > MAX_PHOTO_BYTES:
        raise PhotoTooLarge(N_("The photo is larger than %(limit)s MB."), limit=MAX_PHOTO_BYTES // (1024 * 1024))
    detected = detect_photo_type(photo)
    if detected is None:
        raise UnsupportedPhoto(N_("Send a JPEG, PNG or WebP photo."))
    media_type, extension = detected
    image_hash = hashlib.sha256(photo).hexdigest()
    same = _same_photo(conn, image_hash)
    if same is not None:
        return same
    if run_id is not None and conn.execute("SELECT 1 FROM agent_run WHERE id = ?", (run_id,)).fetchone() is None:
        raise NotFound(N_("Agent run not found."))

    plan = _plan(conn, fields)
    written: Path | None = None
    try:
        with transaction(conn):
            receipt_id = int(
                conn.execute(
                    "INSERT INTO receipt (file_path, media_type, image_hash, extracted_json, confidence,"
                    " duplicate_key, status, reason, duplicate_of, message_ref, run_id, created_at)"
                    " VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                    (
                        media_type,
                        image_hash,
                        fields.to_json(),
                        fields.confidence,
                        _duplicate_key(fields),
                        plan.status,
                        plan.reason,
                        plan.duplicate_of,
                        message_ref,
                        run_id,
                        _now(),
                    ),
                ).fetchone()["id"]
            )
            transaction_id = plan.matched_id
            if plan.booking is not None:
                transaction_id = _book(conn, plan.booking)
            relative = f"{RECEIPTS_DIR}/{datetime.now(UTC).year}/{receipt_id}.{extension}"
            conn.execute(
                "UPDATE receipt SET file_path = ?, transaction_id = ?, booked_at = ? WHERE id = ?",
                (relative, transaction_id, _now() if transaction_id else None, receipt_id),
            )
            target = _inside_receipts(data_dir, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = target
            # A file already at this path is an orphan (receipt ids can come back after a delete). It
            # may be hard-linked into a backup snapshot (backup.files.link_photos), so it is unlinked
            # instead of overwritten: writing through the link would change the snapshot's copy.
            target.unlink(missing_ok=True)
            target.write_bytes(photo)
    except BaseException as exc:
        if written is not None:
            written.unlink(missing_ok=True)
        # Two uploads of one photo at the same moment: the unique hash lets exactly one through.
        if isinstance(exc, sqlite3.IntegrityError) and (same := _same_photo(conn, image_hash)) is not None:
            return same
        raise
    logger.info("receipt %s stored as %s%s", receipt_id, plan.status, f" ({plan.reason})" if plan.reason else "")
    return SubmitResult(
        plan.status,
        receipt_id,
        transaction_id,
        plan.reason,
        plan.duplicate_of,
        matched=plan.matched_id is not None,
    )


# --- review list -------------------------------------------------------------------------------


def book_receipt(conn: sqlite3.Connection, receipt_id: int, correction: Correction) -> Receipt:
    """Book a receipt from the review list with the corrected values (UC step 7, AK3).

    Refused (``Conflict``) for a receipt that already has its booking.
    """
    receipt = get_receipt(conn, receipt_id)
    if receipt.transaction_id is not None:
        raise Conflict(N_("This receipt is already booked."))
    if correction.amount_cents <= 0:
        raise ReceiptsError(N_("Enter the receipt total as an amount greater than 0."))
    if not correction.merchant.strip():
        raise ReceiptsError(N_("Enter the merchant."))
    with transaction(conn):
        transaction_id = _book(conn, correction)
        conn.execute(
            "UPDATE receipt SET status = 'booked', reason = NULL, transaction_id = ?, booked_at = ? WHERE id = ?",
            (transaction_id, _now(), receipt_id),
        )
    return get_receipt(conn, receipt_id)


def delete_receipt(conn: sqlite3.Connection, data_dir: Path, receipt_id: int) -> None:
    """Discard a receipt that has no booking, together with its photo."""
    receipt = get_receipt(conn, receipt_id)
    if receipt.transaction_id is not None:
        raise Conflict(N_("A booked receipt cannot be discarded; delete its booking first."))
    row = conn.execute("SELECT file_path FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
    conn.execute("DELETE FROM receipt WHERE id = ?", (receipt_id,))
    with contextlib.suppress(NotFound):
        _inside_receipts(data_dir, row["file_path"]).unlink(missing_ok=True)


# --- agent runs --------------------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, note: str | None = None) -> AgentRun:
    """Record the start of an agent run; its receipts name the returned id."""
    run_id = conn.execute(
        "INSERT INTO agent_run (started_at, note) VALUES (?, ?) RETURNING id", (_now(), note)
    ).fetchone()["id"]
    return get_run(conn, int(run_id))


def finish_run(conn: sqlite3.Connection, run_id: int, counts: dict[str, Any], note: str | None = None) -> AgentRun:
    """Record the end of a run with the agent's own counts (e.g. messages read, failures)."""
    get_run(conn, run_id)
    conn.execute(
        "UPDATE agent_run SET finished_at = ?, counts_json = ?, note = coalesce(?, note) WHERE id = ?",
        (_now(), json.dumps(counts, ensure_ascii=False), note, run_id),
    )
    return get_run(conn, run_id)


_RUN_SELECT = (
    "SELECT r.*,"
    " (SELECT count(*) FROM receipt WHERE run_id = r.id AND status = 'booked') AS booked,"
    " (SELECT count(*) FROM receipt WHERE run_id = r.id AND status = 'review') AS review,"
    " (SELECT count(*) FROM receipt WHERE run_id = r.id AND status = 'duplicate') AS duplicate"
    " FROM agent_run r"
)


def _run(row: sqlite3.Row) -> AgentRun:
    return AgentRun(
        id=row["id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        counts=json.loads(row["counts_json"]),
        note=row["note"],
        booked=row["booked"],
        review=row["review"],
        duplicate=row["duplicate"],
    )


def get_run(conn: sqlite3.Connection, run_id: int) -> AgentRun:
    """Return the run or raise ``NotFound``."""
    row = conn.execute(f"{_RUN_SELECT} WHERE r.id = ?", (run_id,)).fetchone()  # nosec B608 — constant
    if row is None:
        raise NotFound(N_("Agent run not found."))
    return _run(row)


def list_runs(conn: sqlite3.Connection, *, limit: int = 50) -> list[AgentRun]:
    """The latest runs, newest first."""
    rows = conn.execute(f"{_RUN_SELECT} ORDER BY r.id DESC LIMIT ?", (limit,))  # nosec B608 — constant
    return [_run(row) for row in rows]
