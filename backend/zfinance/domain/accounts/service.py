"""Accounts and categories (UC-ZF-001): plain functions over a ``sqlite3.Connection``.

Accounts carry an area (private/business), a type, an opening balance with its date and a reserve
floor. Their balance is ``balance_at``: opening balance plus the booked amounts up to a date.
Everything that depends on bookings goes through ``zfinance.domain.accounts.ledger`` (empty until
task -008 adds the transactions).

Categories have a kind (income/expense/transfer), an optional area and an optional parent, one
level deep. On first use the app offers a default set in the UI language; once adopted, the
categories are ordinary rows owned by the user.

Feature settings live in ``app_setting``: ``accounts.total_reserve_floor_cents`` (UC TBD-5,
SPEC AK12) and ``accounts.first_run_done`` (the default set was adopted or skipped).

Errors are ``AccountsError`` subclasses carrying an ``N_()`` msgid and its parameters; the API
translates them per request.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, get_args

from zfinance.app_settings import get_setting, set_setting
from zfinance.db import transaction
from zfinance.domain.accounts import ledger
from zfinance.i18n import DEFAULT_LANGUAGE, N_

Area = Literal["private", "business"]
AccountType = Literal["giro", "savings", "cash", "card", "liability"]
CategoryKind = Literal["income", "expense", "transfer"]

AREAS: tuple[Area, ...] = get_args(Area)
ACCOUNT_TYPES: tuple[AccountType, ...] = get_args(AccountType)
CATEGORY_KINDS: tuple[CategoryKind, ...] = get_args(CategoryKind)

TOTAL_RESERVE_FLOOR_KEY = "accounts.total_reserve_floor_cents"
FIRST_RUN_DONE_KEY = "accounts.first_run_done"


# --- errors ------------------------------------------------------------------------------------


class AccountsError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``;
    ``str(exc)`` is the English text.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(AccountsError):
    """The account or category does not exist."""

    status_code = 404


class Conflict(AccountsError):
    """The request contradicts existing data (bookings, duplicates, one-level tree, setup done)."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountFields:
    """What the user enters for an account (create and full update)."""

    name: str
    area: Area
    type: AccountType
    opening_balance_cents: int
    opening_date: date
    reserve_floor_cents: int = 0


@dataclass(frozen=True, kw_only=True)
class Account(AccountFields):
    """A stored account. ``archived_at`` is a UTC timestamp, ``None`` while active."""

    id: int
    archived_at: str | None = None


@dataclass(frozen=True)
class CategoryFields:
    """What the user enters for a category (create, full update, default-set draft)."""

    name: str
    kind: CategoryKind
    area: Area | None = None
    parent_id: int | None = None


@dataclass(frozen=True, kw_only=True)
class Category(CategoryFields):
    """A stored category."""

    id: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        name=row["name"],
        area=row["area"],
        type=row["type"],
        opening_balance_cents=row["opening_balance_cents"],
        opening_date=date.fromisoformat(row["opening_date"]),
        reserve_floor_cents=row["reserve_floor_cents"],
        archived_at=row["archived_at"],
    )


def _category(row: sqlite3.Row) -> Category:
    return Category(id=row["id"], name=row["name"], kind=row["kind"], area=row["area"], parent_id=row["parent_id"])


# --- accounts ----------------------------------------------------------------------------------


def list_accounts(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[Account]:
    """Accounts ordered by area and name. Without ``include_archived`` only active ones.

    The default is what booking forms and the forecast offer (UC 1b: archived accounts are not
    selectable there); overviews and evaluations pass ``include_archived=True``.
    """
    rows = conn.execute(
        "SELECT * FROM account WHERE ? OR archived_at IS NULL ORDER BY area DESC, name COLLATE NOCASE",
        (include_archived,),
    )
    return [_account(row) for row in rows]


def get_account(conn: sqlite3.Connection, account_id: int) -> Account:
    """Return the account or raise ``NotFound``."""
    row = conn.execute("SELECT * FROM account WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Account not found."))
    return _account(row)


def _check_reserve_floor(cents: int) -> None:
    if cents < 0:
        raise AccountsError(N_("The reserve floor cannot be negative."))


def _check_name(name: str) -> None:
    if not name.strip():
        raise AccountsError(N_("Enter a name."))


def _duplicate_account(fields: AccountFields) -> Conflict:
    return Conflict(N_("An account named %(name)s already exists."), name=fields.name)


def create_account(conn: sqlite3.Connection, fields: AccountFields) -> Account:
    """Store a new active account and return it."""
    _check_name(fields.name)
    _check_reserve_floor(fields.reserve_floor_cents)
    try:
        row = conn.execute(
            "INSERT INTO account (name, area, type, opening_balance_cents, opening_date, reserve_floor_cents,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                fields.name.strip(),
                fields.area,
                fields.type,
                fields.opening_balance_cents,
                fields.opening_date.isoformat(),
                fields.reserve_floor_cents,
                _now(),
            ),
        ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _duplicate_account(fields) from exc
    return get_account(conn, row["id"])


def update_account(conn: sqlite3.Connection, account_id: int, fields: AccountFields) -> Account:
    """Replace the editable fields of an account.

    The opening date cannot move past the account's earliest booking (UC 1a: no bookings before
    the opening date).
    """
    get_account(conn, account_id)
    _check_name(fields.name)
    _check_reserve_floor(fields.reserve_floor_cents)
    earliest = ledger.earliest_booking_date(conn, account_id)
    if earliest is not None and fields.opening_date > earliest:
        raise Conflict(
            N_("The opening date cannot be later than the first booking on %(date)s."), date=earliest.isoformat()
        )
    try:
        conn.execute(
            "UPDATE account SET name = ?, area = ?, type = ?, opening_balance_cents = ?, opening_date = ?,"
            " reserve_floor_cents = ? WHERE id = ?",
            (
                fields.name.strip(),
                fields.area,
                fields.type,
                fields.opening_balance_cents,
                fields.opening_date.isoformat(),
                fields.reserve_floor_cents,
                account_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise _duplicate_account(fields) from exc
    return get_account(conn, account_id)


def set_archived(conn: sqlite3.Connection, account_id: int, archived: bool) -> Account:
    """Archive (UC 1b) or re-activate an account. Archiving an archived account keeps its date."""
    get_account(conn, account_id)
    if archived:
        conn.execute("UPDATE account SET archived_at = ? WHERE id = ? AND archived_at IS NULL", (_now(), account_id))
    else:
        conn.execute("UPDATE account SET archived_at = NULL WHERE id = ?", (account_id,))
    return get_account(conn, account_id)


# What else names an account besides its bookings: (refusal text, count query with one ``?`` per use
# of the account id). The foreign keys either block the delete (pots, allocation proposals) or
# would drop the rows silently (recurring items, debts, import runs, tax and levy settings).
_ACCOUNT_USES: tuple[tuple[str, str, int], ...] = (
    (
        N_("This account has %(count)s savings pots and cannot be deleted. Archive it instead."),
        "SELECT COUNT(*) FROM pot WHERE account_id = ?",
        1,
    ),
    (
        N_("This account has %(count)s recurring items and cannot be deleted. Archive it instead."),
        "SELECT COUNT(*) FROM recurring WHERE account_id = ? OR transfer_target_account_id = ?",
        2,
    ),
    (
        N_("This account belongs to %(count)s debts and cannot be deleted. Archive it instead."),
        "SELECT COUNT(*) FROM debt WHERE account_id = ? OR pay_account_id = ?",
        2,
    ),
    (
        N_("This account has %(count)s import runs and cannot be deleted. Archive it instead."),
        "SELECT COUNT(*) FROM import_run WHERE account_id = ?",
        1,
    ),
    (
        N_("This account is the business account of the tax plan and cannot be deleted. Archive it instead."),
        "SELECT COUNT(*) FROM tax_settings WHERE business_account_id = ?",
        1,
    ),
    (
        N_("This account is used by the levy reserve and cannot be deleted. Archive it instead."),
        "SELECT COUNT(*) FROM levy_settings WHERE account_id = ?",
        1,
    ),
    (
        N_("This account has %(count)s allocation proposals and cannot be deleted. Archive it instead."),
        "SELECT COUNT(*) FROM allocation_proposal WHERE account_id = ?",
        1,
    ),
)


def delete_account(conn: sqlite3.Connection, account_id: int) -> None:
    """Delete an account nothing refers to; otherwise it can only be archived (UC AK2).

    Refers to: bookings, savings pots, recurring items (as source or transfer target), debts,
    import runs, the tax plan, the levy reserve and allocation proposals.
    """
    get_account(conn, account_id)
    count = ledger.account_booking_counts(conn).get(account_id, 0)
    if count:
        raise Conflict(
            N_("This account has %(count)s bookings and cannot be deleted. Archive it instead."), count=count
        )
    for msgid, query, uses in _ACCOUNT_USES:
        count = int(conn.execute(query, (account_id,) * uses).fetchone()[0])
        if count:
            raise Conflict(msgid, count=count)
    conn.execute("DELETE FROM account WHERE id = ?", (account_id,))


def account_booking_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """Bookings per account id (absent = none); lets the UI offer delete or archive."""
    return ledger.account_booking_counts(conn)


def balance_at(conn: sqlite3.Connection, account_id: int, on_date: date) -> int:
    """Balance in cents at the end of ``on_date``: opening balance + booked amounts up to that day.

    Before the opening date nothing is recorded for the account, so the balance is 0.
    """
    account = get_account(conn, account_id)
    if on_date < account.opening_date:
        return 0
    return account.opening_balance_cents + ledger.booked_sum_until(conn, account_id, on_date)


def ensure_bookable(conn: sqlite3.Connection, account_id: int, booking_date: date) -> Account:
    """Check that a booking on ``booking_date`` may go to the account; return the account.

    The transactions feature (task -008) calls this before every insert or date change. Refuses an
    archived account (UC 1b) and a date before the opening date, naming that date (UC 1a).
    """
    account = get_account(conn, account_id)
    if account.archived_at is not None:
        raise Conflict(N_("The account %(name)s is archived."), name=account.name)
    if booking_date < account.opening_date:
        raise Conflict(
            N_("Bookings before the opening date %(date)s are not possible for the account %(name)s."),
            date=account.opening_date.isoformat(),
            name=account.name,
        )
    return account


# --- categories --------------------------------------------------------------------------------


def list_categories(conn: sqlite3.Connection) -> list[Category]:
    """All categories ordered by kind and name (the UI builds the tree from ``parent_id``)."""
    rows = conn.execute(
        "SELECT * FROM category ORDER BY CASE kind WHEN 'income' THEN 0 WHEN 'expense' THEN 1 ELSE 2 END,"
        " name COLLATE NOCASE"
    )
    return [_category(row) for row in rows]


def get_category(conn: sqlite3.Connection, category_id: int) -> Category:
    """Return the category or raise ``NotFound``."""
    row = conn.execute("SELECT * FROM category WHERE id = ?", (category_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Category not found."))
    return _category(row)


def _has_children(conn: sqlite3.Connection, category_id: int) -> bool:
    return conn.execute("SELECT 1 FROM category WHERE parent_id = ? LIMIT 1", (category_id,)).fetchone() is not None


def _check_parent(conn: sqlite3.Connection, fields: CategoryFields, category_id: int | None) -> None:
    """One level only: the parent is a top-level category of the same kind, and not the category itself."""
    if fields.parent_id is None:
        return
    if fields.parent_id == category_id:
        raise Conflict(N_("A category cannot be its own parent."))
    parent = get_category(conn, fields.parent_id)
    if parent.parent_id is not None:
        raise Conflict(N_("Subcategories cannot have subcategories of their own."))
    if category_id is not None and _has_children(conn, category_id):
        raise Conflict(N_("A category with subcategories cannot become a subcategory."))
    if parent.kind != fields.kind:
        raise Conflict(N_("A subcategory must have the same kind as its parent."))


def _duplicate_category(fields: CategoryFields) -> Conflict:
    return Conflict(N_("A category named %(name)s already exists here."), name=fields.name)


def _insert_category(conn: sqlite3.Connection, fields: CategoryFields) -> int:
    _check_name(fields.name)
    try:
        row = conn.execute(
            "INSERT INTO category (name, kind, area, parent_id, created_at) VALUES (?, ?, ?, ?, ?) RETURNING id",
            (fields.name.strip(), fields.kind, fields.area, fields.parent_id, _now()),
        ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _duplicate_category(fields) from exc
    return int(row["id"])


def create_category(conn: sqlite3.Connection, fields: CategoryFields) -> Category:
    """Store a new category and return it."""
    _check_parent(conn, fields, None)
    return get_category(conn, _insert_category(conn, fields))


def update_category(conn: sqlite3.Connection, category_id: int, fields: CategoryFields) -> Category:
    """Replace the editable fields of a category.

    A parent's kind can only change while its subcategories would still match it.
    """
    get_category(conn, category_id)
    _check_name(fields.name)
    _check_parent(conn, fields, category_id)
    mismatched = conn.execute(
        "SELECT 1 FROM category WHERE parent_id = ? AND kind != ? LIMIT 1", (category_id, fields.kind)
    ).fetchone()
    if mismatched is not None:
        raise Conflict(N_("A subcategory must have the same kind as its parent."))
    try:
        conn.execute(
            "UPDATE category SET name = ?, kind = ?, area = ?, parent_id = ? WHERE id = ?",
            (fields.name.strip(), fields.kind, fields.area, fields.parent_id, category_id),
        )
    except sqlite3.IntegrityError as exc:
        raise _duplicate_category(fields) from exc
    return get_category(conn, category_id)


def category_booking_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """Bookings per category id (absent = none); tells the UI when a target category is needed."""
    return ledger.category_booking_counts(conn)


def delete_category(conn: sqlite3.Connection, category_id: int, target_id: int | None = None) -> int:
    """Delete a category; return how many bookings moved to ``target_id``.

    A category with bookings needs a target category: its bookings move there and then it is
    removed, in one transaction (UC AK4, extension 3a). A category with subcategories is refused.
    """
    category = get_category(conn, category_id)
    if _has_children(conn, category_id):
        raise Conflict(N_("The category %(name)s has subcategories. Move or delete them first."), name=category.name)
    count = ledger.category_booking_counts(conn).get(category_id, 0)
    if target_id is not None:
        if target_id == category_id:
            raise Conflict(N_("Choose a different category as the target."))
        get_category(conn, target_id)
    elif count:
        raise Conflict(
            N_("The category %(name)s has %(count)s bookings. Choose a category to move them to."),
            name=category.name,
            count=count,
        )
    with transaction(conn):
        moved = ledger.move_category_bookings(conn, category_id, target_id) if target_id is not None else 0
        conn.execute("DELETE FROM category WHERE id = ?", (category_id,))
    return moved


# --- default category set (first run) ----------------------------------------------------------

# Data, not UI text: offered once in the UI language and stored as the user's own rows.
_DEFAULT_SETS: dict[str, list[CategoryFields]] = {
    "de": [
        CategoryFields("Wohnen", "expense", "private"),
        CategoryFields("Lebensmittel", "expense", "private"),
        CategoryFields("Essen gehen", "expense", "private"),
        CategoryFields("Kleidung", "expense", "private"),
        CategoryFields("Tanken", "expense", "private"),
        CategoryFields("Öffis", "expense", "private"),
        CategoryFields("Versicherung", "expense", "private"),
        CategoryFields("Freizeit", "expense", "private"),
        CategoryFields("Gesundheit", "expense", "private"),
        CategoryFields("Abos", "expense", "private"),
        CategoryFields("Honorar", "income", "business"),
        CategoryFields("Software", "expense", "business"),
        CategoryFields("Hardware", "expense", "business"),
        CategoryFields("Reise", "expense", "business"),
        CategoryFields("SVS", "expense", "business"),
        CategoryFields("Steuer", "expense", "business"),
        CategoryFields("Privatentnahme/-einlage", "transfer", "business"),
    ],
    "en": [
        CategoryFields("Housing", "expense", "private"),
        CategoryFields("Groceries", "expense", "private"),
        CategoryFields("Eating out", "expense", "private"),
        CategoryFields("Clothing", "expense", "private"),
        CategoryFields("Fuel", "expense", "private"),
        CategoryFields("Public transport", "expense", "private"),
        CategoryFields("Insurance", "expense", "private"),
        CategoryFields("Leisure", "expense", "private"),
        CategoryFields("Health", "expense", "private"),
        CategoryFields("Subscriptions", "expense", "private"),
        CategoryFields("Fees", "income", "business"),
        CategoryFields("Software", "expense", "business"),
        CategoryFields("Hardware", "expense", "business"),
        CategoryFields("Travel", "expense", "business"),
        CategoryFields("SVS", "expense", "business"),
        CategoryFields("Tax", "expense", "business"),
        CategoryFields("Owner draw/contribution", "transfer", "business"),
    ],
}


def default_categories(language: str) -> list[CategoryFields]:
    """The default category set in ``language`` (English for a language without its own set)."""
    return list(_DEFAULT_SETS.get(language, _DEFAULT_SETS[DEFAULT_LANGUAGE]))


def offers_default_set(conn: sqlite3.Connection) -> bool:
    """True on first run: setup not finished and no category exists yet."""
    if get_setting(conn, FIRST_RUN_DONE_KEY, False):
        return False
    return conn.execute("SELECT 1 FROM category LIMIT 1").fetchone() is None


def complete_setup(conn: sqlite3.Connection, drafts: list[CategoryFields]) -> list[Category]:
    """Adopt the (possibly edited) default set and end the first run, in one transaction.

    An empty list skips the default set. Drafts are top-level categories. Refused once the first
    run is over or categories exist, so the set is never added twice.
    """
    with transaction(conn):
        if not offers_default_set(conn):
            raise Conflict(N_("The category setup is already complete."))
        ids = [_insert_category(conn, CategoryFields(d.name, d.kind, d.area)) for d in drafts]
        set_setting(conn, FIRST_RUN_DONE_KEY, True)
    return [get_category(conn, category_id) for category_id in ids]


# --- settings ----------------------------------------------------------------------------------


def total_reserve_floor(conn: sqlite3.Connection) -> int:
    """The reserve floor over all accounts in cents (UC TBD-5, SPEC AK12); 0 if never set."""
    return int(get_setting(conn, TOTAL_RESERVE_FLOOR_KEY, 0))


def set_total_reserve_floor(conn: sqlite3.Connection, cents: int) -> int:
    """Store the total reserve floor; negative values are refused."""
    _check_reserve_floor(cents)
    set_setting(conn, TOTAL_RESERVE_FLOOR_KEY, cents)
    return cents
