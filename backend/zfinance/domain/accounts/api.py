"""``/api/accounts`` and ``/api/categories``: master data of UC-ZF-001.

One feature, two resources: ``router`` (the module-level router the app discovers) has no prefix
of its own and includes one router per resource. Feature settings and the first-run setup sit
below ``/api/accounts`` (``/settings``, ``/setup``), declared before ``/{account_id}``.

Amounts are integer cents (``*_cents``) and refused as floats; dates are ``YYYY-MM-DD``.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts import service
from zfinance.domain.accounts.service import AccountType, Area, CategoryKind
from zfinance.i18n import N_, SUPPORTED_LANGUAGES
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import AmountCents, NonNegativeCents

accounts_router = APIRouter(prefix="/api/accounts", tags=["accounts"])
categories_router = APIRouter(prefix="/api/categories", tags=["categories"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def _fail(exc: service.AccountsError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class AccountIn(BaseModel):
    """Body of create and update (full replacement)."""

    name: Name
    area: Area
    type: AccountType
    opening_balance_cents: AmountCents
    opening_date: date
    reserve_floor_cents: NonNegativeCents = 0

    def fields(self) -> service.AccountFields:
        """The service's input value."""
        return service.AccountFields(**self.model_dump())


class AccountOut(BaseModel):
    """An account with its balance at ``balance_date`` and its number of bookings."""

    id: int
    name: str
    area: Area
    type: AccountType
    opening_balance_cents: int
    opening_date: date
    reserve_floor_cents: int
    archived_at: str | None
    balance_cents: int
    balance_date: date
    booking_count: int


class BalanceOut(BaseModel):
    """Result of ``GET /api/accounts/{id}/balance``."""

    account_id: int
    on: date
    balance_cents: int


class CategoryIn(BaseModel):
    """Body of create and update (full replacement)."""

    name: Name
    kind: CategoryKind
    area: Area | None = None
    parent_id: int | None = None

    def fields(self) -> service.CategoryFields:
        """The service's input value."""
        return service.CategoryFields(**self.model_dump())


class CategoryDraft(BaseModel):
    """One entry of the default set, as offered and as sent back for adoption (top level only)."""

    name: Name
    kind: CategoryKind
    area: Area | None = None


class CategoryOut(BaseModel):
    """A category with its number of bookings (a target is needed to delete it when > 0)."""

    id: int
    name: str
    kind: CategoryKind
    area: Area | None
    parent_id: int | None
    booking_count: int


class SetupState(BaseModel):
    """First-run state: whether the default set is offered, and the set in the asked language."""

    offer_default_set: bool
    language: str
    default_categories: list[CategoryDraft]


class SetupIn(BaseModel):
    """The edited default set to adopt; an empty list skips it."""

    categories: list[CategoryDraft] = Field(max_length=200)


class AccountSettings(BaseModel):
    """Feature settings kept in ``app_setting``."""

    total_reserve_floor_cents: NonNegativeCents


def _account_out(conn: sqlite3.Connection, account: service.Account, on: date, counts: dict[int, int]) -> AccountOut:
    return AccountOut(
        id=account.id,
        name=account.name,
        area=account.area,
        type=account.type,
        opening_balance_cents=account.opening_balance_cents,
        opening_date=account.opening_date,
        reserve_floor_cents=account.reserve_floor_cents,
        archived_at=account.archived_at,
        balance_cents=service.balance_at(conn, account.id, on),
        balance_date=on,
        booking_count=counts.get(account.id, 0),
    )


def _category_out(category: service.Category, counts: dict[int, int]) -> CategoryOut:
    return CategoryOut(
        id=category.id,
        name=category.name,
        kind=category.kind,
        area=category.area,
        parent_id=category.parent_id,
        booking_count=counts.get(category.id, 0),
    )


# --- /api/accounts: settings and first-run setup -------------------------------------------------


@accounts_router.get("/settings")
def get_settings(conn: Conn) -> AccountSettings:
    """Return the total reserve floor (0 until set)."""
    return AccountSettings(total_reserve_floor_cents=service.total_reserve_floor(conn))


@accounts_router.put("/settings")
def put_settings(body: AccountSettings, conn: Conn) -> AccountSettings:
    """Store the total reserve floor."""
    return AccountSettings(
        total_reserve_floor_cents=service.set_total_reserve_floor(conn, body.total_reserve_floor_cents)
    )


@accounts_router.get("/setup")
def get_setup(conn: Conn, language: Annotated[str, Query(max_length=35)] = "en") -> SetupState:
    """Say whether the default category set is offered and return it in ``language``."""
    resolved = language if language in SUPPORTED_LANGUAGES else "en"
    drafts = [CategoryDraft(name=d.name, kind=d.kind, area=d.area) for d in service.default_categories(resolved)]
    return SetupState(offer_default_set=service.offers_default_set(conn), language=resolved, default_categories=drafts)


@accounts_router.post("/setup", status_code=201)
def post_setup(body: SetupIn, conn: Conn) -> list[CategoryOut]:
    """Adopt the edited default set (or skip it with an empty list) and end the first run."""
    drafts = [service.CategoryFields(name=d.name, kind=d.kind, area=d.area) for d in body.categories]
    try:
        created = service.complete_setup(conn, drafts)
    except service.AccountsError as exc:
        _fail(exc)
    return [_category_out(category, {}) for category in created]


# --- /api/accounts -------------------------------------------------------------------------------


@accounts_router.get("")
def list_accounts(
    conn: Conn, include_archived: bool = False, on: Annotated[date | None, Query()] = None
) -> list[AccountOut]:
    """Accounts with their balance on ``on`` (default today); archived ones only on request."""
    on = on or date.today()
    counts = service.account_booking_counts(conn)
    return [_account_out(conn, a, on, counts) for a in service.list_accounts(conn, include_archived=include_archived)]


@accounts_router.post("", status_code=201)
def create_account(body: AccountIn, conn: Conn) -> AccountOut:
    """Create an account."""
    try:
        account = service.create_account(conn, body.fields())
    except service.AccountsError as exc:
        _fail(exc)
    return _account_out(conn, account, date.today(), {})


@accounts_router.get("/{account_id}")
def get_account(account_id: int, conn: Conn) -> AccountOut:
    """One account with today's balance."""
    try:
        account = service.get_account(conn, account_id)
    except service.AccountsError as exc:
        _fail(exc)
    return _account_out(conn, account, date.today(), service.account_booking_counts(conn))


@accounts_router.put("/{account_id}")
def update_account(account_id: int, body: AccountIn, conn: Conn) -> AccountOut:
    """Replace an account's fields."""
    try:
        account = service.update_account(conn, account_id, body.fields())
    except service.AccountsError as exc:
        _fail(exc)
    return _account_out(conn, account, date.today(), service.account_booking_counts(conn))


@accounts_router.post("/{account_id}/archive")
def archive_account(account_id: int, conn: Conn) -> AccountOut:
    """Archive an account: it stays in evaluations but is no longer offered for bookings."""
    try:
        account = service.set_archived(conn, account_id, True)
    except service.AccountsError as exc:
        _fail(exc)
    return _account_out(conn, account, date.today(), service.account_booking_counts(conn))


@accounts_router.post("/{account_id}/unarchive")
def unarchive_account(account_id: int, conn: Conn) -> AccountOut:
    """Make an archived account active again."""
    try:
        account = service.set_archived(conn, account_id, False)
    except service.AccountsError as exc:
        _fail(exc)
    return _account_out(conn, account, date.today(), service.account_booking_counts(conn))


@accounts_router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, conn: Conn) -> Response:
    """Delete an account nothing refers to (409 otherwise: archive it)."""
    try:
        service.delete_account(conn, account_id)
    except service.AccountsError as exc:
        _fail(exc)
    except sqlite3.IntegrityError:
        # A reference the service check does not know yet must not become a 500.
        _fail(service.Conflict(N_("This account is still in use and cannot be deleted. Archive it instead.")))
    return Response(status_code=204)


@accounts_router.get("/{account_id}/balance")
def get_balance(account_id: int, conn: Conn, on: Annotated[date | None, Query()] = None) -> BalanceOut:
    """Balance at the end of ``on`` (default today)."""
    on = on or date.today()
    try:
        balance = service.balance_at(conn, account_id, on)
    except service.AccountsError as exc:
        _fail(exc)
    return BalanceOut(account_id=account_id, on=on, balance_cents=balance)


# --- /api/categories -----------------------------------------------------------------------------


@categories_router.get("")
def list_categories(conn: Conn) -> list[CategoryOut]:
    """All categories with their number of bookings."""
    counts = service.category_booking_counts(conn)
    return [_category_out(category, counts) for category in service.list_categories(conn)]


@categories_router.post("", status_code=201)
def create_category(body: CategoryIn, conn: Conn) -> CategoryOut:
    """Create a category (top level, or below a top-level category of the same kind)."""
    try:
        category = service.create_category(conn, body.fields())
    except service.AccountsError as exc:
        _fail(exc)
    return _category_out(category, {})


@categories_router.put("/{category_id}")
def update_category(category_id: int, body: CategoryIn, conn: Conn) -> CategoryOut:
    """Replace a category's fields."""
    try:
        category = service.update_category(conn, category_id, body.fields())
    except service.AccountsError as exc:
        _fail(exc)
    return _category_out(category, service.category_booking_counts(conn))


@categories_router.delete("/{category_id}", status_code=204)
def delete_category(category_id: int, conn: Conn, target_id: Annotated[int | None, Query()] = None) -> Response:
    """Delete a category; one with bookings needs ``target_id`` to move them to (409 otherwise)."""
    try:
        service.delete_category(conn, category_id, target_id)
    except service.AccountsError as exc:
        _fail(exc)
    return Response(status_code=204)


router = APIRouter()
router.include_router(accounts_router)
router.include_router(categories_router)
