"""UC-ZF-002 quick entry: bookings, transfers, planned items, suggestions, HTTP API, backup.

Expected values are hand-computed from the use case; every value is synthetic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.backup import service as backup
from zfinance.domain.recurring import service as recurring
from zfinance.domain.transactions import service
from zfinance.domain.transactions.service import BookingFields, TransferFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "transactions.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


def _account(conn: sqlite3.Connection, name: str, area: str = "private", opening: int = 100_000) -> int:
    fields = AccountFields(name, area, "giro", opening, date(2026, 9, 1))  # type: ignore[arg-type]
    return accounts.create_account(conn, fields).id


def _category(conn: sqlite3.Connection, name: str, kind: str = "expense") -> int:
    return accounts.create_category(conn, CategoryFields(name, kind)).id  # type: ignore[arg-type]


def _book(conn: sqlite3.Connection, account_id: int, cents: int, **overrides: object) -> service.Transaction:
    values: dict[str, object] = {"date": date(2026, 9, 10), "amount_cents": cents, "account_id": account_id}
    values.update(overrides)
    return service.create_transaction(conn, BookingFields(**values), today=TODAY)  # type: ignore[arg-type]


def _total(conn: sqlite3.Connection, on: date) -> int:
    return sum(accounts.balance_at(conn, a.id, on) for a in accounts.list_accounts(conn, include_archived=True))


def _count(conn: sqlite3.Connection) -> int:
    return int(conn.execute('SELECT count(*) FROM "transaction"').fetchone()[0])


# --- AK2: a booking is stored --------------------------------------------------------------------


def test_create_booking_stores_fields(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 postcondition: date, amount, account, category, text stored as booked."""
    giro = _account(conn, "Test giro")
    food = _category(conn, "Test food")

    item = _book(conn, giro, -1_250, category_id=food, text=" Test shop ", counterparty="Shop Ltd", memo="  ")

    assert (item.date, item.amount_cents, item.account_id, item.category_id) == (date(2026, 9, 10), -1_250, giro, food)
    assert (item.text, item.counterparty, item.memo) == ("Test shop", "Shop Ltd", None)
    assert (item.status, item.source, item.transfer_group_id) == ("booked", "manual", None)
    assert accounts.balance_at(conn, giro, TODAY) == 100_000 - 1_250


def test_single_booking_update_and_delete(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 6b: a booking is editable and deletable; the balance follows."""
    giro = _account(conn, "Test giro")
    item = _book(conn, giro, -5_000, text="Typo")

    changed = service.update_transaction(
        conn, item.id, BookingFields(date(2026, 9, 11), -4_000, giro, text="Fixed"), today=TODAY
    )
    assert (changed.date, changed.amount_cents, changed.text) == (date(2026, 9, 11), -4_000, "Fixed")
    assert accounts.balance_at(conn, giro, TODAY) == 96_000

    assert service.delete_transaction(conn, item.id) == 1
    assert _count(conn) == 0
    assert accounts.balance_at(conn, giro, TODAY) == 100_000
    with pytest.raises(service.NotFound):
        service.get_transaction(conn, item.id)


def test_deleting_a_planned_recurring_row_skips_it(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-003 step 6 (an occurrence that does not happen is skipped) and the
    skip contract of ``skip_planned`` (recurring_id and due_date stay): a planned occurrence deleted in
    the booking list must not come back on the next generation run."""
    giro = _account(conn, "Test giro")
    item = recurring.create_recurring(
        conn,
        recurring.RecurringFields("Gym", -3_000, giro, "monthly", date(2026, 10, 1), day_of_month=1),
        today=TODAY,
    )
    october = date(2026, 10, 1)
    first = next(t for t in recurring.list_items(conn, item.id) if t.due_date == october)
    planned_before = sum(1 for t in recurring.list_items(conn, item.id) if t.status == "planned")

    assert service.delete_transaction(conn, first.id) == 0
    recurring.generate_planned(conn, today=TODAY)

    occurrences = [t for t in recurring.list_items(conn, item.id) if t.due_date == october]
    assert [(t.id, t.status) for t in occurrences] == [(first.id, "skipped")]
    assert sum(1 for t in recurring.list_items(conn, item.id) if t.status == "planned") == planned_before - 1

    # A second delete on the skipped row must not free the key either (review finding on #53).
    assert service.delete_transaction(conn, first.id) == 0
    recurring.generate_planned(conn, today=TODAY)
    occurrences = [t for t in recurring.list_items(conn, item.id) if t.due_date == october]
    assert [(t.id, t.status) for t in occurrences] == [(first.id, "skipped")]


def test_deleting_a_plain_planned_row_still_deletes_it(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 6b (delete after asking): a planned row no recurring item owns,
    such as an expected one-off item, is deleted as before."""
    giro = _account(conn, "Test giro")
    planned = _book(conn, giro, -5_000, date=date(2026, 12, 1))
    assert planned.status == "planned"
    assert service.delete_transaction(conn, planned.id) == 1
    assert _count(conn) == 0


def test_category_is_optional_in_the_table(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC column contract: category_id nullable (the form asks for it, imports may not)."""
    giro = _account(conn, "Test giro")

    assert _book(conn, giro, 700).category_id is None
    with pytest.raises(accounts.NotFound):
        _book(conn, giro, 700, category_id=999)


# --- AK3: suggestions ----------------------------------------------------------------------------


def test_suggestion_from_second_time(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK3: from the second time on, "Billa" offers the category last used for it."""
    giro = _account(conn, "Test giro")
    groceries = _category(conn, "Test groceries")
    household = _category(conn, "Test household")

    assert service.suggest(conn, "Billa") == []

    _book(conn, giro, -2_345, category_id=groceries, text="Billa", counterparty="Billa AG")
    first = service.suggest(conn, "Billa")
    assert [(s.text, s.category_id, s.counterparty) for s in first] == [("Billa", groceries, "Billa AG")]

    _book(conn, giro, -999, category_id=household, text="billa", date=date(2026, 9, 5))
    second = service.suggest(conn, "BILLA")
    assert [(s.category_id, s.counterparty) for s in second] == [(household, None)]


def test_suggestion_ranking(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC Definitions "Suggestion": exact > prefix > contains, no transfers, max 8."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    for text in ("Market Hall", "Super Market", "Market"):
        _book(conn, giro, -100, text=text)
    service.create_transfer(
        conn, TransferFields(date(2026, 9, 10), 500, giro, savings, text="Market transfer"), today=TODAY
    )

    assert [s.text for s in service.suggest(conn, "market")] == ["Market", "Market Hall", "Super Market"]
    assert service.suggest(conn, "   ") == []

    for number in range(10):
        _book(conn, giro, -100, text=f"Shop {number}")
    many = service.suggest(conn, "shop")
    assert len(many) == 8
    assert many[0].text == "Shop 9"  # most recent first


# --- AK4: transfers ------------------------------------------------------------------------------


def test_transfer_sum_zero(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK4 + TBD-6: owner draw business → private, 300.00 €, total unchanged."""
    business = _account(conn, "Test business", area="business", opening=500_000)
    private = _account(conn, "Test private", opening=20_000)
    before = _total(conn, TODAY)

    source, target = service.create_transfer(
        conn, TransferFields(date(2026, 9, 15), 30_000, business, private, text="Owner draw"), today=TODAY
    )

    assert (source.account_id, source.amount_cents, target.account_id, target.amount_cents) == (
        business,
        -30_000,
        private,
        30_000,
    )
    assert source.transfer_group_id == target.transfer_group_id is not None
    assert (source.transfer_account_id, target.transfer_account_id) == (private, business)
    assert _total(conn, TODAY) == before
    assert accounts.balance_at(conn, business, TODAY) == 470_000
    assert accounts.balance_at(conn, private, TODAY) == 50_000


def test_transfer_legs_move_together(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 3a: deleting one leg deletes the other; changes apply to both legs."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    cash = _account(conn, "Test cash")
    source, target = service.create_transfer(
        conn, TransferFields(date(2026, 9, 10), 10_000, giro, savings), today=TODAY
    )

    # Editing the target leg: new amount and account on it, the source leg mirrors the rest.
    service.update_transaction(
        conn, target.id, BookingFields(date(2026, 9, 12), 7_000, cash, text="To cash"), today=TODAY
    )
    new_source, new_target = service.get_transfer(conn, source.transfer_group_id or "")
    assert (new_source.id, new_source.account_id, new_source.amount_cents) == (source.id, giro, -7_000)
    assert (new_target.id, new_target.account_id, new_target.amount_cents) == (target.id, cash, 7_000)
    assert new_source.date == new_target.date == date(2026, 9, 12)
    assert new_source.text == new_target.text == "To cash"

    # The transfer as a whole.
    service.update_transfer(
        conn, source.transfer_group_id or "", TransferFields(date(2026, 9, 13), 2_500, savings, giro), today=TODAY
    )
    moved = service.get_transfer(conn, source.transfer_group_id or "")
    assert [(leg.account_id, leg.amount_cents) for leg in moved] == [(savings, -2_500), (giro, 2_500)]

    assert service.delete_transaction(conn, target.id) == 2
    assert _count(conn) == 0


def test_transfer_refusals(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC AK4c: same account, amount <= 0 and an archived account are each refused."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")

    with pytest.raises(service.Conflict):
        service.create_transfer(conn, TransferFields(date(2026, 9, 10), 100, giro, giro), today=TODAY)
    for cents in (0, -100):
        with pytest.raises(service.TransactionsError):
            service.create_transfer(conn, TransferFields(date(2026, 9, 10), cents, giro, savings), today=TODAY)
    accounts.set_archived(conn, savings, True)
    with pytest.raises(accounts.Conflict, match="archived"):
        service.create_transfer(conn, TransferFields(date(2026, 9, 10), 100, giro, savings), today=TODAY)
    assert _count(conn) == 0


# --- AK5: future date → planned ----------------------------------------------------------------


def test_future_date_is_planned(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK5/3c: a future booking is planned and not in the balance."""
    giro = _account(conn, "Test giro")

    item = _book(conn, giro, -40_000, date=date(2026, 9, 30))

    assert item.status == "planned"
    assert accounts.balance_at(conn, giro, TODAY) == 100_000
    assert accounts.balance_at(conn, giro, date(2026, 9, 30)) == 100_000

    booked = service.update_transaction(conn, item.id, BookingFields(TODAY, -40_000, giro), today=TODAY)
    assert booked.status == "booked"
    assert accounts.balance_at(conn, giro, TODAY) == 60_000


def test_moving_booking_into_future_plans_it(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC Definitions: the status of a manual booking follows its date on every save."""
    giro = _account(conn, "Test giro")
    item = _book(conn, giro, -1_000, date=TODAY)
    assert item.status == "booked"

    planned = service.update_transaction(conn, item.id, BookingFields(date(2026, 10, 1), -1_000, giro), today=TODAY)

    assert planned.status == "planned"
    assert accounts.balance_at(conn, giro, TODAY) == 100_000


def test_future_transfer_is_planned(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 3c applies to both legs of a transfer."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")

    legs = service.create_transfer(conn, TransferFields(date(2026, 12, 1), 5_000, giro, savings), today=TODAY)

    assert [leg.status for leg in legs] == ["planned", "planned"]


def test_skip_planned_marks_row_and_transfer_pair(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 step 7 / AK3: a skipped item is `skipped`, in no balance, both legs."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    expected = _book(conn, giro, -40_000, date=date(2026, 9, 30), text="Test invoice")
    legs = service.create_transfer(conn, TransferFields(date(2026, 9, 25), 5_000, giro, savings), today=TODAY)

    skipped = service.skip_planned(conn, expected.id)
    skipped_leg = service.skip_planned(conn, legs[1].id)  # either leg takes the pair

    assert (skipped.status, skipped.text, skipped.amount_cents) == ("skipped", "Test invoice", -40_000)
    assert skipped_leg.status == "skipped"
    assert [leg.status for leg in service.get_transfer(conn, legs[0].transfer_group_id or "")] == ["skipped"] * 2
    # Skipped rows never count: hand-computed 100,000 at each account's opening balance.
    assert (
        accounts.balance_at(conn, giro, date(2026, 12, 31)),
        accounts.balance_at(conn, savings, date(2026, 12, 31)),
    ) == (
        100_000,
        100_000,
    )
    # A skipped row stays skipped when it is saved again (also with a future date).
    again = service.update_transaction(conn, expected.id, BookingFields(date(2026, 10, 5), -40_000, giro), today=TODAY)
    assert again.status == "skipped"


def test_skip_planned_refuses_anything_but_planned(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 postcondition: only a planned item can be skipped, else nothing changes."""
    giro = _account(conn, "Test giro")
    booked = _book(conn, giro, -1_000)
    planned = _book(conn, giro, -2_000, date=date(2026, 10, 1))
    service.skip_planned(conn, planned.id)
    before = [tuple(row) for row in conn.execute('SELECT * FROM "transaction" ORDER BY id')]

    with pytest.raises(service.Conflict):
        service.skip_planned(conn, booked.id)
    with pytest.raises(service.Conflict):
        service.skip_planned(conn, planned.id)  # already skipped
    with pytest.raises(service.NotFound):
        service.skip_planned(conn, 999)
    assert [tuple(row) for row in conn.execute('SELECT * FROM "transaction" ORDER BY id')] == before


# --- AK6 / AK7: refusals -----------------------------------------------------------------------


def test_zero_amount_refused(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-002 3b: amount 0 is refused, nothing stored."""
    giro = _account(conn, "Test giro")

    with pytest.raises(service.TransactionsError, match="other than 0"):
        _book(conn, giro, 0)
    item = _book(conn, giro, -100)
    with pytest.raises(service.TransactionsError):
        service.update_transaction(conn, item.id, BookingFields(item.date, 0, giro), today=TODAY)
    assert _count(conn) == 1


def test_bookings_respect_ensure_bookable(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 1a/1b via ensure_bookable, on create and on date/account change."""
    giro = _account(conn, "Test giro")
    old = _account(conn, "Test old")

    with pytest.raises(accounts.Conflict, match="2026-09-01"):
        _book(conn, giro, -100, date=date(2026, 8, 31))
    item = _book(conn, giro, -100)
    with pytest.raises(accounts.Conflict, match="2026-09-01"):
        service.update_transaction(conn, item.id, BookingFields(date(2026, 8, 31), -100, giro), today=TODAY)

    accounts.set_archived(conn, old, True)
    with pytest.raises(accounts.Conflict, match="archived"):
        _book(conn, old, -100)
    with pytest.raises(accounts.Conflict, match="archived"):
        service.update_transaction(conn, item.id, BookingFields(item.date, -100, old), today=TODAY)
    assert service.get_transaction(conn, item.id).account_id == giro


# --- AK8: HTTP API -----------------------------------------------------------------------------


def _setup(client: TestClient) -> tuple[int, int, int]:
    def account(name: str) -> int:
        body = {
            "name": name,
            "area": "private",
            "type": "giro",
            "opening_balance_cents": 0,
            "opening_date": "2026-01-01",
        }
        return int(client.post("/api/accounts", json=body).json()["id"])

    category = client.post("/api/categories", json={"name": "Test food", "kind": "expense"}).json()["id"]
    return account("Test giro"), account("Test savings"), int(category)


def test_api_flow(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC AK8: create, list, update, suggest, transfer, delete over HTTP."""
    giro, savings, food = _setup(client)
    today = date.today().isoformat()

    created = client.post(
        "/api/transactions",
        json={"date": today, "amount_cents": -1_999, "account_id": giro, "category_id": food, "text": "Test deli"},
    )
    assert created.status_code == 201
    booking = created.json()
    assert (booking["status"], booking["source"], booking["amount_cents"]) == ("booked", "manual", -1_999)

    assert [s["category_id"] for s in client.get("/api/transactions/suggest", params={"q": "test d"}).json()] == [food]

    updated = client.put(
        f"/api/transactions/{booking['id']}",
        json={"date": today, "amount_cents": -2_999, "account_id": giro, "category_id": food, "text": "Test deli"},
    )
    assert updated.json()["amount_cents"] == -2_999

    transfer = client.post(
        "/api/transactions/transfers",
        json={"date": today, "amount_cents": 5_000, "from_account_id": giro, "to_account_id": savings},
    )
    assert transfer.status_code == 201
    legs = transfer.json()
    assert [leg["amount_cents"] for leg in legs] == [-5_000, 5_000]
    group = legs[0]["transfer_group_id"]
    moved = client.put(
        f"/api/transactions/transfers/{group}",
        json={"date": today, "amount_cents": 6_000, "from_account_id": savings, "to_account_id": giro},
    ).json()
    assert [(leg["account_id"], leg["amount_cents"]) for leg in moved] == [(savings, -6_000), (giro, 6_000)]

    listed = client.get("/api/transactions").json()
    assert len(listed) == 3
    assert client.get(f"/api/accounts/{giro}/balance").json()["balance_cents"] == -2_999 + 6_000

    assert client.delete(f"/api/transactions/{legs[1]['id']}").status_code == 204
    assert len(client.get("/api/transactions").json()) == 1
    assert client.get(f"/api/transactions/{legs[0]['id']}").status_code == 404


def test_api_refusals(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC AK8 + UC 3b: floats 422, 0 → 400, unknown → 404, early → 409."""
    giro, savings, _food = _setup(client)
    body = {"date": "2026-09-10", "account_id": giro}

    assert client.post("/api/transactions", json={**body, "amount_cents": 12.5}).status_code == 422
    assert client.post("/api/transactions", json={**body, "amount_cents": 100.0}).status_code == 422

    zero = client.post("/api/transactions", json={**body, "amount_cents": 0})
    assert zero.status_code == 400
    assert zero.json()["detail"] == "Enter an amount other than 0."
    zero_de = client.post("/api/transactions", json={**body, "amount_cents": 0}, headers={"Accept-Language": "de"})
    assert zero_de.json()["detail"] == "Gib einen Betrag ungleich 0 ein."

    early = client.post("/api/transactions", json={**body, "date": "2025-12-31", "amount_cents": -1})
    assert early.status_code == 409
    assert "2026-01-01" in early.json()["detail"]

    same = client.post(
        "/api/transactions/transfers",
        json={"date": "2026-09-10", "amount_cents": 100, "from_account_id": giro, "to_account_id": giro},
    )
    assert same.status_code == 409
    negative = client.post(
        "/api/transactions/transfers",
        json={"date": "2026-09-10", "amount_cents": -100, "from_account_id": giro, "to_account_id": savings},
    )
    assert negative.status_code == 422

    assert client.get("/api/transactions/999").status_code == 404
    assert client.delete("/api/transactions/999").status_code == 404
    assert client.put("/api/transactions/transfers/nope", json={**body, "amount_cents": 1}).status_code == 422
    assert client.get("/api/transactions").json() == []


# --- AK9: backup -------------------------------------------------------------------------------


def test_backup_roundtrip_transactions(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Oracle: spec-anchored — ARCHITECTURE "Backup and restore": tables of later migrations are covered unchanged."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    food = _category(conn, "Test food")
    _book(conn, giro, -1_234, category_id=food, text="Test deli", counterparty="Deli", memo="note")
    _book(conn, giro, -5_000, date=date(2026, 10, 1))
    service.create_transfer(conn, TransferFields(date(2026, 9, 12), 2_000, giro, savings), today=TODAY)
    raw = backup.dumps_export(backup.export_data(conn))

    target = connect(tmp_path / "target.sqlite3")
    try:
        migrate(target)
        result = backup.restore_export(target, raw)

        assert result.rows["transaction"] == 4
        assert service.list_transactions(target) == service.list_transactions(conn)
        assert accounts.balance_at(target, giro, TODAY) == accounts.balance_at(conn, giro, TODAY)
    finally:
        target.close()
