"""UC-ZF-001 accounts, areas and categories: service rules, first-run default set, HTTP API, backup.

Rules that depend on bookings run against ``FakeLedger``, an in-memory stand-in for the extension
point ``zfinance.domain.accounts.ledger``, and again against real bookings of the transactions
feature (task -008) through the real ledger queries. Every value is synthetic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.app_settings import get_setting, set_setting
from zfinance.db import connect
from zfinance.domain.accounts import ledger, service
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.backup import service as backup
from zfinance.domain.debts import service as debts
from zfinance.domain.pots import service as pots
from zfinance.domain.recurring import service as recurring
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.i18n import LANGUAGE_SETTING_KEY
from zfinance.migrator import migrate

# --- fixtures ----------------------------------------------------------------------------------


@dataclass
class Booking:
    account_id: int
    category_id: int
    on: date
    amount_cents: int
    status: str = "booked"


@dataclass
class FakeLedger:
    """Bookings kept in a list; installed in place of the ledger extension point."""

    bookings: list[Booking] = field(default_factory=list)

    def booked_sum_until(self, conn: sqlite3.Connection, account_id: int, on_date: date) -> int:
        return sum(
            b.amount_cents
            for b in self.bookings
            if b.account_id == account_id and b.status == "booked" and b.on <= on_date
        )

    def earliest_booking_date(self, conn: sqlite3.Connection, account_id: int) -> date | None:
        return min((b.on for b in self.bookings if b.account_id == account_id), default=None)

    def account_booking_counts(self, conn: sqlite3.Connection) -> dict[int, int]:
        counts: dict[int, int] = {}
        for b in self.bookings:
            counts[b.account_id] = counts.get(b.account_id, 0) + 1
        return counts

    def category_booking_counts(self, conn: sqlite3.Connection) -> dict[int, int]:
        counts: dict[int, int] = {}
        for b in self.bookings:
            counts[b.category_id] = counts.get(b.category_id, 0) + 1
        return counts

    def move_category_bookings(self, conn: sqlite3.Connection, source_id: int, target_id: int) -> int:
        moved = [b for b in self.bookings if b.category_id == source_id]
        for b in moved:
            b.category_id = target_id
        return len(moved)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "accounts.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def fake_ledger(monkeypatch: pytest.MonkeyPatch) -> FakeLedger:
    fake = FakeLedger()
    for name in (
        "booked_sum_until",
        "earliest_booking_date",
        "account_booking_counts",
        "category_booking_counts",
        "move_category_bookings",
    ):
        monkeypatch.setattr(ledger, name, getattr(fake, name))
    return fake


def _giro(name: str = "Test giro", **overrides: object) -> AccountFields:
    values: dict[str, object] = {
        "name": name,
        "area": "private",
        "type": "giro",
        "opening_balance_cents": 100_000,
        "opening_date": date(2026, 9, 1),
        "reserve_floor_cents": 0,
    }
    values.update(overrides)
    return AccountFields(**values)  # type: ignore[arg-type]


def _names(conn: sqlite3.Connection) -> list[str]:
    return [c.name for c in service.list_categories(conn)]


# --- AK1: balance --------------------------------------------------------------------------------


def test_balance_opening_minus_booked_expense(conn: sqlite3.Connection, fake_ledger: FakeLedger) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK1: 1,000.00 € on 01.09., -200.00 € booked on 05.09. → 800.00 € on 06.09.

    Before the booking day the balance is still the opening balance; a planned item never counts.
    """
    account = service.create_account(conn, _giro())
    fake_ledger.bookings += [
        Booking(account.id, 0, date(2026, 9, 5), -20_000),
        Booking(account.id, 0, date(2026, 9, 5), -5_000, status="planned"),
    ]

    assert service.balance_at(conn, account.id, date(2026, 9, 6)) == 80_000
    assert service.balance_at(conn, account.id, date(2026, 9, 4)) == 100_000


def test_balance_with_real_booking(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK1: 1,000.00 € on 01.09., -200.00 € booked on 05.09. → 800.00 € on 06.09.

    Same numbers as the fake-ledger test, now through the transactions feature and the real ledger
    queries; a planned item (future date) never counts.
    """
    account = service.create_account(conn, _giro())
    today = date(2026, 9, 6)
    transactions.create_transaction(conn, BookingFields(date(2026, 9, 5), -20_000, account.id), today=today)
    planned = transactions.create_transaction(
        conn, BookingFields(date(2026, 9, 6), -5_000, account.id), today=date(2026, 9, 5)
    )

    assert planned.status == "planned"
    assert service.balance_at(conn, account.id, date(2026, 9, 6)) == 80_000
    assert service.balance_at(conn, account.id, date(2026, 9, 4)) == 100_000


def test_ledger_queries_real_bookings(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2, AK4, 1a with real bookings: delete, move, opening date."""
    account = service.create_account(conn, _giro())
    source = service.create_category(conn, CategoryFields("Test source", "expense"))
    target = service.create_category(conn, CategoryFields("Test target", "expense"))
    for day in (5, 7):
        transactions.create_transaction(
            conn, BookingFields(date(2026, 9, day), -1_000, account.id, source.id), today=date(2026, 9, 19)
        )

    assert service.account_booking_counts(conn) == {account.id: 2}
    with pytest.raises(service.Conflict, match="Archive"):
        service.delete_account(conn, account.id)
    with pytest.raises(service.Conflict, match="2026-09-05"):
        service.update_account(conn, account.id, _giro(opening_date=date(2026, 9, 6)))
    with pytest.raises(service.Conflict, match="Choose a category"):
        service.delete_category(conn, source.id)

    assert service.delete_category(conn, source.id, target.id) == 2
    assert service.category_booking_counts(conn) == {target.id: 2}


def test_new_account_balance_is_opening_balance(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC AK1: balance = opening balance + Σ booked; no bookings exist yet.

    Before the opening date nothing is recorded, so the balance is 0 (SPEC Definitions).
    """
    account = service.create_account(conn, _giro(opening_balance_cents=-45_050, type="card"))

    assert service.balance_at(conn, account.id, date(2026, 9, 1)) == -45_050
    assert service.balance_at(conn, account.id, date(2027, 1, 1)) == -45_050
    assert service.balance_at(conn, account.id, date(2026, 8, 31)) == 0


def test_account_reserve_floor_persisted(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 postcondition: every account has a reserve floor; negative is refused."""
    account = service.create_account(conn, _giro(reserve_floor_cents=50_000))
    assert service.get_account(conn, account.id).reserve_floor_cents == 50_000

    updated = service.update_account(conn, account.id, _giro(reserve_floor_cents=20_000))
    assert updated.reserve_floor_cents == 20_000

    with pytest.raises(service.AccountsError):
        service.create_account(conn, _giro("Other", reserve_floor_cents=-1))


# --- AK1c: bookability (extension 1a, 1b) -------------------------------------------------------


def test_booking_before_opening_date_is_refused(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 extension 1a: refused and the opening date is named."""
    account = service.create_account(conn, _giro())

    with pytest.raises(service.Conflict, match="2026-09-01"):
        service.ensure_bookable(conn, account.id, date(2026, 8, 31))
    assert service.ensure_bookable(conn, account.id, date(2026, 9, 1)).id == account.id


def test_archived_account_is_not_bookable(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 extension 1b: an archived account is not selectable for capture."""
    account = service.create_account(conn, _giro())
    service.set_archived(conn, account.id, True)

    with pytest.raises(service.Conflict, match="archived"):
        service.ensure_bookable(conn, account.id, date(2026, 9, 10))


def test_opening_date_after_first_booking_is_refused(conn: sqlite3.Connection, fake_ledger: FakeLedger) -> None:
    """Oracle: spec-anchored — UC-ZF-001 extension 1a: no bookings may lie before the opening date."""
    account = service.create_account(conn, _giro())
    fake_ledger.bookings.append(Booking(account.id, 0, date(2026, 9, 5), -1_000))

    with pytest.raises(service.Conflict, match="2026-09-05"):
        service.update_account(conn, account.id, _giro(opening_date=date(2026, 9, 6)))
    assert service.get_account(conn, account.id).opening_date == date(2026, 9, 1)
    service.update_account(conn, account.id, _giro(opening_date=date(2026, 9, 5)))


# --- AK2: delete vs archive ----------------------------------------------------------------------


def test_account_with_bookings_cannot_be_deleted(conn: sqlite3.Connection, fake_ledger: FakeLedger) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2: an account with bookings cannot be deleted, only archived."""
    account = service.create_account(conn, _giro())
    fake_ledger.bookings.append(Booking(account.id, 0, date(2026, 9, 5), -1_000))

    with pytest.raises(service.Conflict, match="Archive"):
        service.delete_account(conn, account.id)
    assert service.get_account(conn, account.id).archived_at is None

    assert service.set_archived(conn, account.id, True).archived_at is not None


def test_account_without_bookings_is_deleted(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 extension 1b: deleting is possible with 0 bookings."""
    account = service.create_account(conn, _giro())

    service.delete_account(conn, account.id)

    with pytest.raises(service.NotFound):
        service.get_account(conn, account.id)


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - fixed table names


def test_account_with_pot_allocation_cannot_be_deleted(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2 a deposit from the free balance writes only a pot allocation, no booking.

    The pot's RESTRICT key used to surface as an unhandled IntegrityError; now it is a Conflict.
    """
    account = service.create_account(conn, _giro())
    pot = pots.create_pot(conn, pots.PotFields(name="Holiday", account_id=account.id), today=date(2026, 9, 2))
    pots.deposit(conn, pot.id, 10_000, on=date(2026, 9, 2), today=date(2026, 9, 2))

    with pytest.raises(service.Conflict, match="pot"):
        service.delete_account(conn, account.id)

    assert service.get_account(conn, account.id).id == account.id
    assert _row_count(conn, "pot_allocation") == 1


def test_account_with_paused_recurring_item_cannot_be_deleted(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2 a paused item has no planned rows, so the account has 0 bookings.

    The delete used to cascade the item away silently.
    """
    account = service.create_account(conn, _giro())
    item = recurring.create_recurring(
        conn,
        recurring.RecurringFields(
            description="Rent",
            amount_cents=-50_000,
            account_id=account.id,
            interval="monthly",
            start_date=date(2026, 9, 1),
            day_of_month=1,
        ),
        today=date(2026, 9, 2),
    )
    recurring.set_active(conn, item.id, False, today=date(2026, 9, 2))
    assert service.account_booking_counts(conn).get(account.id, 0) == 0

    with pytest.raises(service.Conflict, match="recurring"):
        service.delete_account(conn, account.id)

    assert _row_count(conn, "recurring") == 1


def test_transfer_target_account_of_a_recurring_item_cannot_be_deleted(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2 the target side of a recurring transfer is a reference too."""
    source = service.create_account(conn, _giro("Source"))
    target = service.create_account(conn, _giro("Target", type="savings"))
    recurring.create_recurring(
        conn,
        recurring.RecurringFields(
            description="Savings plan",
            amount_cents=5_000,
            account_id=source.id,
            transfer_target_account_id=target.id,
            interval="monthly",
            start_date=date(2027, 12, 1),
            day_of_month=1,
        ),
        today=date(2026, 9, 2),
    )

    with pytest.raises(service.Conflict, match="recurring"):
        service.delete_account(conn, target.id)

    assert _row_count(conn, "recurring") == 1


def test_debt_account_cannot_be_deleted(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2: a debt without instalment has no bookings, yet its account is in use."""
    debt = debts.create_debt(
        conn, debts.DebtFields(creditor="Bank", area="private", total_amount_cents=100_000), today=date(2026, 9, 2)
    )

    with pytest.raises(service.Conflict, match="debt"):
        service.delete_account(conn, debt.account_id)

    assert _row_count(conn, "debt") == 1


@pytest.mark.parametrize(
    ("table_sql", "needle"),
    [
        (
            "INSERT INTO import_run (account_id, file_hash, counts, created_at) VALUES (:a, 'h', '{}', 'now')",
            "import",
        ),
        (
            "INSERT INTO tax_settings (id, business_account_id, updated_at) VALUES (1, :a, 'now')",
            "tax",
        ),
        (
            "INSERT INTO levy_settings (id, levy_rate_bp, account_id, updated_at) VALUES (1, 3500, :a, 'now')",
            "levy",
        ),
        (
            "INSERT INTO allocation_proposal (amount_cents, account_id, date, created_at)"
            " VALUES (100, :a, '2026-09-02', 'now')",
            "allocation",
        ),
    ],
)
def test_account_referenced_by_other_features_cannot_be_deleted(
    conn: sqlite3.Connection, table_sql: str, needle: str
) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2: imports, tax plan, levy and allocation proposals use the account."""
    account = service.create_account(conn, _giro())
    conn.execute(table_sql, {"a": account.id})

    with pytest.raises(service.Conflict, match=needle):
        service.delete_account(conn, account.id)

    assert service.get_account(conn, account.id).id == account.id


def test_archived_account_leaves_default_list(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2: an archived account does not appear in the capture form's list.

    It stays visible for evaluations (``include_archived``, UC 1b).
    """
    kept = service.create_account(conn, _giro("Kept"))
    archived = service.create_account(conn, _giro("Gone"))
    service.set_archived(conn, archived.id, True)

    assert [a.id for a in service.list_accounts(conn)] == [kept.id]
    assert {a.id for a in service.list_accounts(conn, include_archived=True)} == {kept.id, archived.id}


def test_unarchive_restores_account(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-007 AK2c: re-activating brings the account back."""
    account = service.create_account(conn, _giro())
    service.set_archived(conn, account.id, True)

    assert service.set_archived(conn, account.id, False).archived_at is None
    assert [a.id for a in service.list_accounts(conn)] == [account.id]


def test_duplicate_account_name_is_refused(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-007 AK6a: names are unique regardless of case."""
    service.create_account(conn, _giro("Cash"))

    with pytest.raises(service.Conflict):
        service.create_account(conn, _giro("cash", type="cash"))


# --- AK3: default category set -------------------------------------------------------------------


def test_default_set_offered_in_language(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 step 4 lists the German set; English is the translated equivalent."""
    assert service.offers_default_set(conn)

    german = service.default_categories("de")
    assert [c.name for c in german if c.area == "private"] == [
        "Wohnen",
        "Lebensmittel",
        "Essen gehen",
        "Kleidung",
        "Tanken",
        "Öffis",
        "Versicherung",
        "Freizeit",
        "Gesundheit",
        "Abos",
    ]
    assert [c.name for c in german if c.area == "business"] == [
        "Honorar",
        "Software",
        "Hardware",
        "Reise",
        "SVS",
        "Steuer",
        "Privatentnahme/-einlage",
    ]
    english = service.default_categories("en")
    assert len(english) == len(german)
    assert "SVS" in [c.name for c in english]
    assert "Groceries" in [c.name for c in english]
    assert service.default_categories("fr") == english


def test_setup_stores_edited_set_once(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK3: the set can be edited before adoption; it is stored as sent, once."""
    drafts = service.default_categories("de")[:3]
    drafts[0] = CategoryFields("Miete", "expense", "private")  # renamed
    drafts.pop(1)  # removed
    drafts.append(CategoryFields("Haustier", "expense", None))  # added, no area

    created = service.complete_setup(conn, drafts)

    assert [(c.name, c.kind, c.area) for c in created] == [
        ("Miete", "expense", "private"),
        ("Essen gehen", "expense", "private"),
        ("Haustier", "expense", None),
    ]
    assert get_setting(conn, service.FIRST_RUN_DONE_KEY) is True
    assert not service.offers_default_set(conn)
    with pytest.raises(service.Conflict):
        service.complete_setup(conn, drafts)
    assert len(service.list_categories(conn)) == 3


def test_setup_skip_ends_offer(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 step 4 "übernimmt oder anpasst": skipping adopts nothing and ends the offer."""
    assert service.complete_setup(conn, []) == []

    assert not service.offers_default_set(conn)
    assert service.list_categories(conn) == []


def test_no_offer_when_categories_exist(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — KB task: default set seeded only into an empty database."""
    service.create_category(conn, CategoryFields("Own", "expense"))

    assert not service.offers_default_set(conn)
    with pytest.raises(service.Conflict):
        service.complete_setup(conn, service.default_categories("en"))


def test_setup_rolls_back_on_duplicate_draft(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-007 contract sketch: adoption is one transaction."""
    drafts = [CategoryFields("Food", "expense", "private"), CategoryFields("food", "expense", "private")]

    with pytest.raises(service.Conflict):
        service.complete_setup(conn, drafts)

    assert service.list_categories(conn) == []
    assert service.offers_default_set(conn)


def test_adopted_categories_do_not_follow_language(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — orchestrator note: adopted categories are data, not translated on a language switch."""
    set_setting(conn, LANGUAGE_SETTING_KEY, "de")
    service.complete_setup(conn, service.default_categories("de"))

    set_setting(conn, LANGUAGE_SETTING_KEY, "en")

    assert "Lebensmittel" in _names(conn)
    assert "Groceries" not in _names(conn)


# --- AK4: categories -----------------------------------------------------------------------------


def test_category_with_bookings_needs_target(conn: sqlite3.Connection, fake_ledger: FakeLedger) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK4 / 3a: without a target the delete is refused and nothing changes."""
    food = service.create_category(conn, CategoryFields("Food", "expense"))
    fake_ledger.bookings.append(Booking(1, food.id, date(2026, 9, 5), -1_000))

    with pytest.raises(service.Conflict, match="Choose a category"):
        service.delete_category(conn, food.id)
    assert _names(conn) == ["Food"]


def test_category_delete_moves_bookings_to_target(conn: sqlite3.Connection, fake_ledger: FakeLedger) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK4 / 3a: the system re-books to the target instead of losing bookings."""
    food = service.create_category(conn, CategoryFields("Food", "expense"))
    groceries = service.create_category(conn, CategoryFields("Groceries", "expense"))
    fake_ledger.bookings += [
        Booking(1, food.id, date(2026, 9, 5), -1_000),
        Booking(1, food.id, date(2026, 9, 6), -2_000),
    ]

    assert service.delete_category(conn, food.id, groceries.id) == 2

    assert _names(conn) == ["Groceries"]
    assert {b.category_id for b in fake_ledger.bookings} == {groceries.id}


def test_category_delete_target_validation(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-007 AK4c: the target must be another existing category."""
    food = service.create_category(conn, CategoryFields("Food", "expense"))

    with pytest.raises(service.Conflict):
        service.delete_category(conn, food.id, food.id)
    with pytest.raises(service.NotFound):
        service.delete_category(conn, food.id, 999)
    assert _names(conn) == ["Food"]
    assert service.delete_category(conn, food.id) == 0
    assert _names(conn) == []


def test_category_tree_is_one_level(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 step 3: optional parent category, one level."""
    car = service.create_category(conn, CategoryFields("Car", "expense", "private"))
    fuel = service.create_category(conn, CategoryFields("Fuel", "expense", "private", parent_id=car.id))
    assert fuel.parent_id == car.id

    with pytest.raises(service.Conflict):
        service.create_category(conn, CategoryFields("Diesel", "expense", parent_id=fuel.id))
    with pytest.raises(service.Conflict):
        service.create_category(conn, CategoryFields("Refund", "income", parent_id=car.id))
    with pytest.raises(service.Conflict):
        service.update_category(conn, car.id, CategoryFields("Car", "expense", parent_id=fuel.id))
    with pytest.raises(service.Conflict):
        service.delete_category(conn, car.id)
    service.delete_category(conn, fuel.id)
    service.delete_category(conn, car.id)


# --- AK5: total reserve floor ------------------------------------------------------------------


def test_total_reserve_floor_setting(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC AK12 / UC TBD-5: a total reserve besides the account floors, in app_setting."""
    assert service.total_reserve_floor(conn) == 0

    service.set_total_reserve_floor(conn, 300_000)

    assert get_setting(conn, service.TOTAL_RESERVE_FLOOR_KEY) == 300_000
    assert service.total_reserve_floor(conn) == 300_000
    with pytest.raises(service.AccountsError):
        service.set_total_reserve_floor(conn, -1)


# --- AK6: HTTP API -------------------------------------------------------------------------------


def _account_body(name: str = "Test giro", **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "area": "business",
        "type": "giro",
        "opening_balance_cents": 123_456,
        "opening_date": "2026-09-01",
        "reserve_floor_cents": 10_000,
    }
    body.update(overrides)
    return body


def test_api_create_and_list_account(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC AK1: the list shows each account with its balance (= opening balance here)."""
    created = client.post("/api/accounts", json=_account_body())
    assert created.status_code == 201
    account_id = created.json()["id"]

    listed = client.get("/api/accounts", params={"on": "2026-09-30"}).json()

    assert [(a["id"], a["balance_cents"], a["reserve_floor_cents"], a["booking_count"]) for a in listed] == [
        (account_id, 123_456, 10_000, 0)
    ]
    balance = client.get(f"/api/accounts/{account_id}/balance", params={"on": "2026-08-31"}).json()
    assert balance == {"account_id": account_id, "on": "2026-08-31", "balance_cents": 0}


def test_api_account_lifecycle(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-001 1b: archive hides it from the default list; unarchive and delete work."""
    account_id = client.post("/api/accounts", json=_account_body()).json()["id"]

    updated = client.put(f"/api/accounts/{account_id}", json=_account_body("Renamed"))
    assert updated.json()["name"] == "Renamed"
    assert client.post(f"/api/accounts/{account_id}/archive").json()["archived_at"] is not None
    assert client.get("/api/accounts").json() == []
    assert len(client.get("/api/accounts", params={"include_archived": True}).json()) == 1
    assert client.post(f"/api/accounts/{account_id}/unarchive").json()["archived_at"] is None
    assert client.delete(f"/api/accounts/{account_id}").status_code == 204
    assert client.get(f"/api/accounts/{account_id}").status_code == 404


def test_api_refuses_float_amounts(client: TestClient) -> None:
    """Oracle: spec-anchored — ARCHITECTURE "Money": floats never carry money; the API takes integer cents only."""
    assert client.post("/api/accounts", json=_account_body(opening_balance_cents=100.5)).status_code == 422
    assert client.post("/api/accounts", json=_account_body(opening_balance_cents=100.0)).status_code == 422
    assert client.post("/api/accounts", json=_account_body(reserve_floor_cents=-1)).status_code == 422
    assert client.post("/api/accounts", json=_account_body(name="  ")).status_code == 422
    assert client.get("/api/accounts").json() == []


def test_api_errors_are_localized(client: TestClient) -> None:
    """Oracle: spec-anchored — ARCHITECTURE "Internationalisation": error details follow Accept-Language."""
    client.post("/api/accounts", json=_account_body("Cash"))

    english = client.post("/api/accounts", json=_account_body("Cash"))
    german = client.post("/api/accounts", json=_account_body("Cash"), headers={"Accept-Language": "de"})

    assert english.status_code == german.status_code == 409
    assert english.json()["detail"] == "An account named Cash already exists."
    assert german.json()["detail"] == "Ein Konto mit dem Namen Cash gibt es bereits."
    missing = client.get("/api/accounts/999", headers={"Accept-Language": "de"})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Konto nicht gefunden."


def test_api_categories_crud(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-001 step 3: kind, optional area, optional parent; delete without bookings."""
    parent = client.post("/api/categories", json={"name": "Car", "kind": "expense", "area": "private"}).json()
    child = client.post("/api/categories", json={"name": "Fuel", "kind": "expense", "parent_id": parent["id"]})
    assert child.status_code == 201
    child_id = child.json()["id"]

    renamed = client.put(
        f"/api/categories/{child_id}", json={"name": "Petrol", "kind": "expense", "parent_id": parent["id"]}
    )
    assert renamed.json()["name"] == "Petrol"
    assert [(c["name"], c["parent_id"], c["booking_count"]) for c in client.get("/api/categories").json()] == [
        ("Car", None, 0),
        ("Petrol", parent["id"], 0),
    ]
    assert client.delete(f"/api/categories/{parent['id']}").status_code == 409
    assert client.delete(f"/api/categories/{child_id}").status_code == 204
    assert client.post("/api/categories", json={"name": "X", "kind": "gift"}).status_code == 422


def test_api_setup_flow(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK3: offered at first start in the UI language, edited, adopted once."""
    offer = client.get("/api/accounts/setup", params={"language": "de"}).json()
    assert offer["offer_default_set"] is True
    assert offer["language"] == "de"
    assert offer["default_categories"][0] == {"name": "Wohnen", "kind": "expense", "area": "private"}

    edited = [*offer["default_categories"][:2], {"name": "Eigenes", "kind": "income", "area": None}]
    created = client.post("/api/accounts/setup", json={"categories": edited})

    assert created.status_code == 201
    assert [c["name"] for c in created.json()] == ["Wohnen", "Lebensmittel", "Eigenes"]
    assert client.get("/api/accounts/setup").json()["offer_default_set"] is False
    assert client.post("/api/accounts/setup", json={"categories": []}).status_code == 409


def test_api_settings(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC AK12: the total reserve is editable; negative and float values are refused."""
    assert client.get("/api/accounts/settings").json() == {"total_reserve_floor_cents": 0}

    assert client.put("/api/accounts/settings", json={"total_reserve_floor_cents": 250_000}).status_code == 200
    assert client.put("/api/accounts/settings", json={"total_reserve_floor_cents": -5}).status_code == 422
    assert client.put("/api/accounts/settings", json={"total_reserve_floor_cents": 1.5}).status_code == 422
    assert client.get("/api/accounts/settings").json() == {"total_reserve_floor_cents": 250_000}


# --- AK7: backup covers the new tables ---------------------------------------------------------


def test_backup_roundtrip_accounts_categories(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Oracle: spec-anchored — ARCHITECTURE "Backup and restore": tables of later migrations are covered unchanged."""
    account = service.create_account(conn, _giro(reserve_floor_cents=5_000))
    service.set_archived(conn, account.id, True)
    service.complete_setup(conn, service.default_categories("en")[:2])
    service.set_total_reserve_floor(conn, 70_000)
    raw = backup.dumps_export(backup.export_data(conn))

    target = connect(tmp_path / "target.sqlite3")
    try:
        migrate(target)
        result = backup.restore_export(target, raw)

        assert result.rows["account"] == 1
        assert result.rows["category"] == 2
        assert service.list_accounts(target, include_archived=True) == service.list_accounts(
            conn, include_archived=True
        )
        assert service.list_categories(target) == service.list_categories(conn)
        assert service.total_reserve_floor(target) == 70_000
        assert not service.offers_default_set(target)
    finally:
        target.close()


def test_api_delete_account_used_by_a_pot_is_a_localized_409(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2: deleting an account that a pot uses is a 409 in the request language."""
    account_id = client.post("/api/accounts", json=_account_body()).json()["id"]
    assert client.post("/api/pots", json={"name": "Holiday", "account_id": account_id}).status_code == 201

    english = client.delete(f"/api/accounts/{account_id}")
    german = client.delete(f"/api/accounts/{account_id}", headers={"Accept-Language": "de"})

    assert english.status_code == german.status_code == 409
    assert english.json()["detail"] == "This account has 1 savings pots and cannot be deleted. Archive it instead."
    assert (
        german.json()["detail"] == "Dieses Konto hat 1 Töpfe und kann nicht gelöscht werden. Archiviere es stattdessen."
    )
    assert client.get(f"/api/accounts/{account_id}").status_code == 200


def test_api_delete_maps_a_late_integrity_error_to_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2 a foreign key the service check missed must not become a 500."""

    def refuse(conn: sqlite3.Connection, account_id: int) -> None:
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(service, "delete_account", refuse)
    account_id = client.post("/api/accounts", json=_account_body()).json()["id"]

    response = client.delete(f"/api/accounts/{account_id}")

    assert response.status_code == 409
