"""Tax plan (UC-ZF-008) as plain functions over a connection.

The user enters the income-tax prepayment (ESt-VZ) and the SVS contribution per due date and the
business account once; every due date becomes one *yearly* recurring item of the recurring engine
(-009), so four planned rows per kind appear in the next 12 months. The engine is not extended and
this feature writes no booking of its own. There is no VAT anywhere (small business, decision
TBD-2).

* **Settings** (``TaxFields``): business account, an amount per due date for each kind (``None`` =
  not entered, UC 1a) and the due dates as ``MM-DD`` (day 31 = the last day of the month; the
  defaults are 15.02./15.05./15.08./15.11. for ESt-VZ and the end of Feb/May/Aug/Nov for SVS).
* **Save = sync** (``save_settings``): one database transaction that brings the recurring items in
  line with the settings — like the debts (-013), the pattern "recurring item from master data".
  The settings are the source: an item whose values were edited on the recurring screen is put back
  by the next save.
* **Amount change** (UC step 5, AK2): the old item of a due date ends the day before that date's
  next open occurrence and a new one starts there. Rows before that day stay: confirmed (booked or
  skipped) quarters, overdue unconfirmed ones (UC 5a). A quarter confirmed ahead of its due date
  counts as confirmed, so the new item starts a year later.
* **Empty settings** (UC 1a): no item; ``tax_plan_missing`` is the flag for the dashboard hint.

Invariants:

* At most one current item per (kind, due date) (partial unique index); a superseded item is kept
  with ``retired_at`` so its booked rows still count for ``prepayments_ytd``.
* Only ``planned`` rows are removed, and only through the recurring service.

Errors are ``TaxError`` subclasses carrying an ``N_()`` msgid; ``AccountsError`` and
``RecurringError`` from the services called here pass through.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from zfinance.app_settings import get_setting
from zfinance.db import transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import CategoryFields
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.schedule import clamped
from zfinance.domain.recurring.service import RecurringFields
from zfinance.i18n import DEFAULT_LANGUAGE, LANGUAGE_SETTING_KEY, N_

Kind = Literal["est", "svs"]
KINDS: tuple[Kind, ...] = ("est", "svs")

#: ESt-VZ due on 15.02./15.05./15.08./15.11.; SVS at the end of Feb/May/Aug/Nov (31 = month end).
DEFAULT_EST_DUE_DATES = ("02-15", "05-15", "08-15", "11-15")
DEFAULT_SVS_DUE_DATES = ("02-31", "05-31", "08-31", "11-31")

#: Text of the planned rows; data, like the category names, not a translated UI string.
_TEXT: dict[Kind, str] = {"est": "Income tax prepayment", "svs": "SVS contribution"}
#: Expense category a kind is booked under, per UI language (the default sets of the accounts feature).
_CATEGORY_NAMES: dict[Kind, dict[str, str]] = {
    "est": {"en": "Tax", "de": "Steuer"},
    "svs": {"en": "SVS", "de": "SVS"},
}
_MAX_DUE_DATES = 12
_DUE = re.compile(r"(\d{2})-(\d{2})")


# --- errors ------------------------------------------------------------------------------------


class TaxError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class Conflict(TaxError):
    """The request contradicts the state of the account."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxFields:
    """What the user enters (full replacement). Amounts are positive cents per due date."""

    business_account_id: int | None = None
    est_vz_quarter_cents: int | None = None
    svs_quarter_cents: int | None = None
    est_due_dates: tuple[str, ...] = DEFAULT_EST_DUE_DATES
    svs_due_dates: tuple[str, ...] = DEFAULT_SVS_DUE_DATES

    def amount(self, kind: Kind) -> int | None:
        """The amount per due date of ``kind``."""
        return self.est_vz_quarter_cents if kind == "est" else self.svs_quarter_cents

    def due_dates(self, kind: Kind) -> tuple[str, ...]:
        """The due dates of ``kind`` as ``MM-DD``."""
        return self.est_due_dates if kind == "est" else self.svs_due_dates


@dataclass(frozen=True)
class TaxTerm:
    """One current plan item: a due date with its recurring item and next open planned date."""

    kind: Kind
    due: str
    recurring_id: int
    amount_cents: int
    next_due_date: date | None
    planned_count: int


@dataclass(frozen=True, kw_only=True)
class TaxSettings(TaxFields):
    """Stored settings with the current plan items and the "not entered" flag."""

    plan_missing: bool
    terms: list[TaxTerm] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- pure rules --------------------------------------------------------------------------------


def parse_due_dates(dates: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Validate a list of ``MM-DD`` due dates and return it ascending.

    1 to 12 distinct dates; month 1-12, day 1-31 (a day the month does not have falls on the
    month's last day, so 31 means "end of the month").
    """
    if not 1 <= len(dates) <= _MAX_DUE_DATES:
        raise TaxError(N_("Enter between 1 and 12 due dates."))
    parsed: set[tuple[int, int]] = set()
    for text in dates:
        match = _DUE.fullmatch(text.strip())
        if match is None or not (1 <= int(match[1]) <= 12 and 1 <= int(match[2]) <= 31):
            raise TaxError(N_("Enter each due date as month and day, e.g. 02-15."))
        parsed.add((int(match[1]), int(match[2])))
    if len(parsed) != len(dates):
        raise TaxError(N_("Each due date can be entered only once."))
    return tuple(f"{month:02d}-{day:02d}" for month, day in sorted(parsed))


def occurrence(due: str, year: int) -> date:
    """The date of the ``MM-DD`` due date in ``year`` (day 31 in a 30-day month is its last day)."""
    return clamped(year, int(due[:2]), int(due[3:]))


def next_open_due(due: str, today: date, done: set[date]) -> date:
    """The first occurrence of ``due`` on or after ``today`` that is not in ``done`` (confirmed)."""
    for year in range(today.year, today.year + len(done) + 2):
        candidate = occurrence(due, year)
        if candidate >= today and candidate not in done:
            return candidate
    raise AssertionError("unreachable: more years than confirmed dates were tried")


# --- reading -----------------------------------------------------------------------------------


def _fields(row: sqlite3.Row | None) -> TaxFields:
    if row is None:
        return TaxFields()
    return TaxFields(
        business_account_id=row["business_account_id"],
        est_vz_quarter_cents=row["est_vz_quarter_cents"],
        svs_quarter_cents=row["svs_quarter_cents"],
        est_due_dates=tuple(row["est_due_dates"].split(",")),
        svs_due_dates=tuple(row["svs_due_dates"].split(",")),
    )


def _stored(conn: sqlite3.Connection) -> TaxFields:
    return _fields(conn.execute("SELECT * FROM tax_settings WHERE id = 1").fetchone())


def _plan_missing(fields_: TaxFields) -> bool:
    return fields_.business_account_id is None or all(fields_.amount(kind) is None for kind in KINDS)


def tax_plan_missing(conn: sqlite3.Connection) -> bool:
    """True when no plan exists: no amount or no account entered (UC 1a).

    Flag for the dashboard hint "Taxes & levies not set" and the ``plan_missing`` field of the API.
    """
    return _plan_missing(_stored(conn))


def _current_rows(conn: sqlite3.Connection, kind: Kind) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM tax_item WHERE kind = ? AND retired_at IS NULL", (kind,))
    return {row["due"]: row for row in rows}


def get_settings(conn: sqlite3.Connection) -> TaxSettings:
    """The stored settings (defaults for the due dates while nothing is saved) with the plan items."""
    stored = _stored(conn)
    terms: list[TaxTerm] = []
    for kind in KINDS:
        rows = _current_rows(conn, kind)
        for due in sorted(rows):
            recurring_id = rows[due]["recurring_id"]
            if recurring_id is None:
                continue
            item = recurring.get_recurring(conn, recurring_id)
            terms.append(TaxTerm(kind, due, item.id, -item.amount_cents, item.next_due_date, item.planned_count))
    return TaxSettings(
        business_account_id=stored.business_account_id,
        est_vz_quarter_cents=stored.est_vz_quarter_cents,
        svs_quarter_cents=stored.svs_quarter_cents,
        est_due_dates=stored.est_due_dates,
        svs_due_dates=stored.svs_due_dates,
        plan_missing=_plan_missing(stored),
        terms=terms,
    )


def prepayments_ytd(conn: sqlite3.Connection, year: int) -> int:
    """Booked income-tax prepayments plus SVS of ``year`` in cents, positive (a credit reduces it).

    Contract for the levy reserve (UC-ZF-015, task -019): the sum over the booked rows of every
    recurring item the tax plan ever owned — current and superseded ones — whose booking date lies
    in ``year``. Planned and skipped rows do not count, and neither do back-payments (Nachzahlungen,
    expected one-off items) or payments that were never linked to a plan item: the reserve built
    from this figure stays on the safe side. ``0`` without a plan.
    """
    ids = [row[0] for row in conn.execute("SELECT DISTINCT recurring_id FROM tax_item WHERE recurring_id IS NOT NULL")]
    return -sum(
        item.amount_cents
        for recurring_id in ids
        for item in recurring.list_items(conn, recurring_id)
        if item.status == "booked" and item.date.year == year
    )


def ensure_not_plan_item(conn: sqlite3.Connection, recurring_id: int) -> None:
    """Raise ``recurring.Conflict`` if the tax plan owns ``recurring_id``, current or superseded.

    The recurring screen's delete guard (wired in ``create_app``): deleting such an item unlinks
    its booked rows, and ``prepayments_ytd`` would lose them. The plan changes through the settings
    (UC step 5).
    """
    owned = conn.execute("SELECT 1 FROM tax_item WHERE recurring_id = ? LIMIT 1", (recurring_id,)).fetchone()
    if owned is not None:
        raise recurring.Conflict(
            N_("This recurring item belongs to the tax plan. Change or clear it under Taxes & levies.")
        )


# --- writing -----------------------------------------------------------------------------------


def _check(conn: sqlite3.Connection, fields_: TaxFields) -> TaxFields:
    """Validate the fields; the due dates come back normalized (``MM-DD``, ascending)."""
    for kind in KINDS:
        amount = fields_.amount(kind)
        if amount is not None and amount <= 0:
            raise TaxError(N_("Enter an amount greater than 0 or leave it empty."))
    if fields_.business_account_id is None:
        if any(fields_.amount(kind) is not None for kind in KINDS):
            raise TaxError(N_("Choose the account the payments are made from."))
    else:
        account = accounts.get_account(conn, fields_.business_account_id)
        if account.archived_at is not None:
            raise Conflict(N_("The account %(name)s is archived."), name=account.name)
    return replace(
        fields_,
        est_due_dates=parse_due_dates(fields_.est_due_dates),
        svs_due_dates=parse_due_dates(fields_.svs_due_dates),
    )


def save_settings(conn: sqlite3.Connection, fields_: TaxFields, *, today: date | None = None) -> TaxSettings:
    """Store the settings and bring the recurring items in line (UC steps 1-2 and 5).

    A first amount creates the items (four planned rows per kind within 12 months), a changed
    amount replaces them from the next open due date on (AK2), a cleared amount or a removed due
    date ends the item, an added due date creates one. Everything happens in one transaction.
    """
    today = today or date.today()
    fields_ = _check(conn, fields_)
    with transaction(conn):
        conn.execute(
            "INSERT INTO tax_settings (id, business_account_id, est_vz_quarter_cents, svs_quarter_cents,"
            " est_due_dates, svs_due_dates, updated_at) VALUES (1, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET business_account_id = excluded.business_account_id,"
            " est_vz_quarter_cents = excluded.est_vz_quarter_cents, svs_quarter_cents = excluded.svs_quarter_cents,"
            " est_due_dates = excluded.est_due_dates, svs_due_dates = excluded.svs_due_dates,"
            " updated_at = excluded.updated_at",
            (
                fields_.business_account_id,
                fields_.est_vz_quarter_cents,
                fields_.svs_quarter_cents,
                ",".join(fields_.est_due_dates),
                ",".join(fields_.svs_due_dates),
                _now(),
            ),
        )
        for kind in KINDS:
            _sync_kind(conn, kind, fields_, today)
    return get_settings(conn)


def _category_id(conn: sqlite3.Connection, kind: Kind) -> int:
    """The expense category of the kind: an existing "Tax"/"Steuer"/"SVS", else one in the UI language."""
    names = _CATEGORY_NAMES[kind]
    known = {name.casefold() for name in names.values()}
    for category in accounts.list_categories(conn):
        if category.kind == "expense" and category.parent_id is None and category.name.casefold() in known:
            return category.id
    language = get_setting(conn, LANGUAGE_SETTING_KEY, DEFAULT_LANGUAGE)
    name = names.get(language, names[DEFAULT_LANGUAGE])
    return accounts.create_category(conn, CategoryFields(name, "expense", "business")).id


def _wanted(kind: Kind, due: str, amount: int, account_id: int, category_id: int, start: date) -> RecurringFields:
    """The yearly recurring item of one due date: an expense from the business account."""
    return RecurringFields(
        description=_TEXT[kind],
        amount_cents=-amount,
        account_id=account_id,
        interval="yearly",
        start_date=start,
        category_id=category_id,
        day_of_month=int(due[3:]),
    )


def _same(item: recurring.Recurring, wanted: RecurringFields) -> bool:
    """Whether the stored item carries the values of the plan (start and end are not compared)."""
    return (
        item.description,
        item.amount_cents,
        item.account_id,
        item.category_id,
        item.interval,
        item.day_of_month,
        item.transfer_target_account_id,
    ) == (
        wanted.description,
        wanted.amount_cents,
        wanted.account_id,
        wanted.category_id,
        wanted.interval,
        wanted.day_of_month,
        None,
    )


def _stored_fields(item: recurring.Recurring) -> RecurringFields:
    return RecurringFields(
        description=item.description,
        amount_cents=item.amount_cents,
        account_id=item.account_id,
        interval=item.interval,
        start_date=item.start_date,
        category_id=item.category_id,
        transfer_target_account_id=item.transfer_target_account_id,
        day_of_month=item.day_of_month,
        weekday=item.weekday,
        end_date=item.end_date,
    )


def _close(conn: sqlite3.Connection, row: sqlite3.Row, today: date) -> date:
    """End the current item of ``row`` and return the day its successor starts (the next open due date).

    The item ends the day before that date through ``update_recurring``: its unmoved planned rows
    from then on go, everything earlier stays (confirmed, skipped, overdue, moved). An item that
    starts on or after that date has no history before it and is deleted with its row; otherwise
    the row is retired and kept for ``prepayments_ytd``.
    """
    if row["recurring_id"] is None:  # the item was deleted on the recurring screen
        conn.execute("DELETE FROM tax_item WHERE id = ?", (row["id"],))
        return next_open_due(row["due"], today, set())
    item = recurring.get_recurring(conn, row["recurring_id"])
    confirmed = {p.due_date for p in recurring.list_items(conn, item.id) if p.status != "planned"}
    successor_start = next_open_due(row["due"], today, confirmed)
    if item.start_date < successor_start:
        end = successor_start - timedelta(days=1)
        if item.end_date is not None:
            end = min(end, item.end_date)
        recurring.update_recurring(conn, item.id, replace(_stored_fields(item), end_date=end), today=today)
        conn.execute("UPDATE tax_item SET retired_at = ? WHERE id = ?", (_now(), row["id"]))
    else:
        recurring.delete_recurring(conn, item.id)
        conn.execute("DELETE FROM tax_item WHERE id = ?", (row["id"],))
    return successor_start


def _sync_kind(conn: sqlite3.Connection, kind: Kind, fields_: TaxFields, today: date) -> None:
    """Bring the recurring items of one kind in line with the settings."""
    amount, account_id = fields_.amount(kind), fields_.business_account_id
    planned = amount is not None and account_id is not None
    wanted_dues = list(fields_.due_dates(kind)) if planned else []
    current = _current_rows(conn, kind)
    for due, old in current.items():
        if due not in wanted_dues:
            _close(conn, old, today)
    if not wanted_dues or amount is None or account_id is None:
        return
    category_id = _category_id(conn, kind)
    for due in wanted_dues:
        row = current.get(due)
        wanted = _wanted(kind, due, amount, account_id, category_id, today)
        item = (
            None if row is None or row["recurring_id"] is None else recurring.get_recurring(conn, row["recurring_id"])
        )
        if item is not None and _same(item, wanted):
            if not item.active:
                recurring.set_active(conn, item.id, True, today=today)
            continue
        start = _close(conn, row, today) if row is not None else next_open_due(due, today, set())
        created = recurring.create_recurring(conn, replace(wanted, start_date=start), today=today)
        conn.execute(
            "INSERT INTO tax_item (kind, due, recurring_id, created_at) VALUES (?, ?, ?, ?)",
            (kind, due, created.id, _now()),
        )
