"""UC-ZF-004 due items: the list with its 14-day window and overdue flag, confirm, postpone, API.

Expected values are hand-computed from the use case and the calendar; every value is synthetic.
"today" is 2026-09-19 unless a test names another day. A recurring item only generates rows from
"today" on, so overdue rows are created by generating at an earlier "today" and reviewing later.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, AccountsError
from zfinance.domain.debts import service as debts
from zfinance.domain.debts.service import DebtFields
from zfinance.domain.due import service
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.service import RecurringFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransactionsError
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "due.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


def _account(conn: sqlite3.Connection, name: str, opening_cents: int = 100_000) -> int:
    fields = AccountFields(name, "private", "giro", opening_cents, date(2026, 1, 1))  # type: ignore[arg-type]
    return accounts.create_account(conn, fields).id


def _monthly(
    conn: sqlite3.Connection, account_id: int, day: int, generated_on: date, **overrides: object
) -> recurring.Recurring:
    values: dict[str, object] = {
        "description": "Test rent",
        "amount_cents": -80_000,
        "account_id": account_id,
        "interval": "monthly",
        "start_date": date(2026, 7, 1),
        "day_of_month": day,
    }
    values.update(overrides)
    return recurring.create_recurring(conn, RecurringFields(**values), today=generated_on)  # type: ignore[arg-type]


def _planned_id(conn: sqlite3.Connection, recurring_id: int, due: str) -> int:
    """Id of the occurrence with this due date (for a transfer: its outgoing leg)."""
    row = conn.execute(
        'SELECT id FROM "transaction" WHERE recurring_id = ? AND due_date = ? AND amount_cents < 0',
        (recurring_id, due),
    ).fetchone()
    return int(row["id"])


def _snapshot(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    """Every booking row, to prove that a refused call changed nothing."""
    return [tuple(row) for row in conn.execute('SELECT * FROM "transaction" ORDER BY id')]


def _one_off(conn: sqlite3.Connection, account_id: int, day: date, cents: int, text: str) -> int:
    """An expected one-off item: a manual booking that was dated after the day it was entered."""
    return transactions.create_transaction(
        conn, BookingFields(day, cents, account_id, text=text), today=day - timedelta(days=60)
    ).id


# --- AK1: confirm books the item ----------------------------------------------------------------


def test_confirm_planned_books_and_changes_balance(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 AK1 / main 2-3: booked at the planned date and amount, 100,000 - 80,000."""
    giro = _account(conn, "Test giro")
    rent = _monthly(conn, giro, 1, TODAY, start_date=date(2026, 10, 1))
    today = date(2026, 10, 2)  # the rent of 2026-10-01 is one day overdue
    planned_id = _planned_id(conn, rent.id, "2026-10-01")
    assert accounts.balance_at(conn, giro, today) == 100_000  # planned items never count

    booked = service.confirm(conn, planned_id, today=today)

    assert (booked.status, booked.date, booked.amount_cents) == ("booked", date(2026, 10, 1), -80_000)
    assert (booked.recurring_id, booked.account_id, booked.text) == (rent.id, giro, "Test rent")
    assert accounts.balance_at(conn, giro, today) == 20_000
    assert planned_id not in [item.id for item in service.list_due(conn, today=today)]
    row = conn.execute('SELECT due_date FROM "transaction" WHERE id = ?', (booked.id,)).fetchone()
    assert row["due_date"] == "2026-10-01"  # the due date counts as done


# --- AK2: another amount or date does not touch the recurring item -------------------------------


def test_confirm_planned_keeps_recurring(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 AK2 / SPEC AK4: confirming with 850.00 leaves the item and the next 800.00."""
    giro = _account(conn, "Test giro")
    rent = _monthly(conn, giro, 1, TODAY, start_date=date(2026, 10, 1))
    today = date(2026, 10, 5)

    booked = service.confirm(
        conn, _planned_id(conn, rent.id, "2026-10-01"), amount_cents=85_000, on_date=date(2026, 10, 3), today=today
    )

    assert (booked.amount_cents, booked.date) == (-85_000, date(2026, 10, 3))  # the sign of the item stays
    after = recurring.get_recurring(conn, rent.id)
    assert (after.amount_cents, after.description, after.day_of_month, after.active) == (-80_000, "Test rent", 1, True)
    november = next(i for i in recurring.list_items(conn, rent.id) if i.due_date == date(2026, 11, 1))
    assert (november.amount_cents, november.status, november.moved) == (-80_000, "planned", False)
    # The confirmed due date is not generated again, not even by a run that moves the horizon.
    recurring.generate_planned(conn, today=today)
    rows = conn.execute('SELECT id FROM "transaction" WHERE recurring_id = ? AND due_date = ?', (rent.id, "2026-10-01"))
    assert [row["id"] for row in rows] == [booked.id]


# --- AK3: transfer ------------------------------------------------------------------------------


def test_confirm_transfer_books_both_legs(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 main 3 + UC-ZF-002 transfer rule: both legs booked, total unchanged."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    saving = _monthly(
        conn,
        giro,
        15,
        TODAY,
        description="Test saving",
        amount_cents=10_000,
        transfer_target_account_id=savings,
        start_date=date(2026, 10, 1),
    )
    today = date(2026, 10, 16)
    leg_id = _planned_id(conn, saving.id, "2026-10-15")
    assert [(item.id, item.amount_cents) for item in service.list_due(conn, today=today)] == [(leg_id, -10_000)]

    booked = service.confirm(conn, leg_id, amount_cents=12_000, today=today)

    assert (booked.status, booked.amount_cents, booked.account_id) == ("booked", -12_000, giro)
    assert booked.transfer_group_id is not None
    source, target = transactions.get_transfer(conn, booked.transfer_group_id)
    assert [(leg.account_id, leg.amount_cents, leg.status) for leg in (source, target)] == [
        (giro, -12_000, "booked"),
        (savings, 12_000, "booked"),
    ]
    assert (accounts.balance_at(conn, giro, today), accounts.balance_at(conn, savings, today)) == (88_000, 112_000)
    assert service.list_due(conn, today=today) == []


# --- AK5: overdue flag, window, order -----------------------------------------------------------


def test_overdue_highlight_after_30_days(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 AK4 / 1a: 31 days overdue is flagged, 30 is overdue but not flagged."""
    giro = _account(conn, "Test giro")
    # Monthly on the 19th, generated in July: 2026-07-19 (62 days overdue), 08-19 (31), 09-19 (today), 10-19.
    _monthly(conn, giro, 19, date(2026, 7, 1))
    thirty = _one_off(conn, giro, date(2026, 8, 20), -500, "Test thirty days")  # 30 days overdue

    items = {item.date: item for item in service.list_due(conn, today=TODAY)}

    assert (items[date(2026, 7, 19)].days_overdue, items[date(2026, 7, 19)].long_overdue) == (62, True)
    assert (items[date(2026, 8, 19)].days_overdue, items[date(2026, 8, 19)].long_overdue) == (31, True)
    assert (items[date(2026, 8, 20)].id, items[date(2026, 8, 20)].days_overdue) == (thirty, 30)
    assert items[date(2026, 8, 20)].long_overdue is False
    assert (items[date(2026, 9, 19)].days_overdue, items[date(2026, 9, 19)].long_overdue) == (0, False)
    # An overdue item does not disappear by itself, and a plain confirm books it on its planned date.
    assert items[date(2026, 8, 19)].confirm_date == date(2026, 8, 19)


def test_due_list_window_and_order(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 main 1 (today + 14 days, overdue first), 2a (matched items gone)."""
    giro = _account(conn, "Test giro")
    overdue = _one_off(conn, giro, date(2026, 9, 1), -100, "Test overdue")
    today_item = _one_off(conn, giro, TODAY, -200, "Test today")
    edge = _one_off(conn, giro, date(2026, 10, 3), -300, "Test edge")  # today + 14 days: listed
    _one_off(conn, giro, date(2026, 10, 4), -400, "Test later")  # today + 15 days: not listed
    booked = _one_off(conn, giro, date(2026, 9, 5), -500, "Test booked")
    skipped = _one_off(conn, giro, date(2026, 9, 6), -600, "Test skipped")
    conn.execute("UPDATE \"transaction\" SET status = 'booked' WHERE id = ?", (booked,))  # arrange only
    conn.execute("UPDATE \"transaction\" SET status = 'skipped' WHERE id = ?", (skipped,))  # arrange only
    # A recurring occurrence that the CSV import matched (replace_planned) is not planned any more.
    rent = _monthly(conn, giro, 10, date(2026, 9, 1), start_date=date(2026, 9, 1))
    matched = _planned_id(conn, rent.id, "2026-09-10")
    bank = transactions.create_transaction(
        conn, BookingFields(date(2026, 9, 11), -80_000, giro, text="TEST BANK LINE"), today=TODAY
    )
    recurring.replace_planned(conn, matched, bank.id)

    listed = service.list_due(conn, today=TODAY)

    october_rent = _planned_id(conn, rent.id, "2026-10-10")  # today + 21 days: not listed
    assert [item.id for item in listed] == [overdue, today_item, edge]
    assert october_rent not in [item.id for item in listed]
    assert [item.confirm_date for item in listed] == [date(2026, 9, 1), TODAY, TODAY]  # a future item books today
    assert service.list_due(conn, today=TODAY, horizon_days=0)[-1].id == today_item


# --- AK6: postpone ------------------------------------------------------------------------------


def test_postpone_keeps_planned_and_moved(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 4a: status stays planned; UC-ZF-003 2b: a moved row is not regenerated."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    rent = _monthly(conn, giro, 1, TODAY, start_date=date(2026, 10, 1))
    saving = _monthly(
        conn, giro, 5, TODAY, description="Test saving", amount_cents=10_000, transfer_target_account_id=savings
    )
    invoice = _one_off(conn, giro, date(2026, 10, 2), 30_000, "Test invoice")
    rent_id = _planned_id(conn, rent.id, "2026-10-01")

    moved = service.postpone(conn, rent_id, date(2026, 10, 8), today=TODAY)

    assert (moved.status, moved.date, moved.amount_cents) == ("planned", date(2026, 10, 8), -80_000)
    row = conn.execute('SELECT due_date, manually_moved FROM "transaction" WHERE id = ?', (rent_id,)).fetchone()
    assert (row["due_date"], row["manually_moved"]) == ("2026-10-01", 1)
    recurring.generate_planned(conn, today=TODAY)
    fields = RecurringFields("Test rent", -90_000, giro, "monthly", date(2026, 10, 1), day_of_month=1)
    recurring.update_recurring(conn, rent.id, fields, today=TODAY)
    assert (
        transactions.get_transaction(conn, rent_id).date,
        transactions.get_transaction(conn, rent_id).amount_cents,
    ) == (
        date(2026, 10, 8),
        -80_000,
    )  # the amount change of the item does not reach the postponed row
    assert rent_id in [item.id for item in service.list_due(conn, today=date(2026, 10, 8))]

    # An expected one-off item stays a planned booking; a transfer moves with its pair.
    assert service.postpone(conn, invoice, date(2026, 10, 9), today=TODAY).status == "planned"
    leg_id = _planned_id(conn, saving.id, "2026-10-05")
    service.postpone(conn, leg_id, date(2026, 10, 12), today=TODAY)
    pair = transactions.get_transfer(conn, transactions.get_transaction(conn, leg_id).transfer_group_id or "")
    assert [(leg.date, leg.status) for leg in pair] == [(date(2026, 10, 12), "planned")] * 2

    # "After today" is strict: today itself and earlier days are refused, nothing changes.
    before = _snapshot(conn)
    for refused in (TODAY, TODAY - timedelta(days=1)):
        with pytest.raises(service.DueError):
            service.postpone(conn, invoice, refused, today=TODAY)
    assert _snapshot(conn) == before


# --- AK4: skip ----------------------------------------------------------------------------------


def test_skip_keeps_recurring_going(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 AK3 / main 6-7: a skipped item is in no balance or list, the item goes on."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    rent = _monthly(conn, giro, 1, TODAY, start_date=date(2026, 10, 1))
    saving = _monthly(
        conn,
        giro,
        5,
        TODAY,
        description="Test saving",
        amount_cents=10_000,
        transfer_target_account_id=savings,
        start_date=date(2026, 10, 1),
    )
    today = date(2026, 10, 10)
    rent_id = _planned_id(conn, rent.id, "2026-10-01")
    leg_id = _planned_id(conn, saving.id, "2026-10-05")
    assert [item.id for item in service.list_due(conn, today=today)] == [rent_id, leg_id]

    skipped = service.skip(conn, rent_id, today=today)
    service.skip(conn, leg_id, today=today)

    assert (skipped.status, skipped.recurring_id, skipped.amount_cents) == ("skipped", rent.id, -80_000)
    pair = transactions.get_transfer(conn, transactions.get_transaction(conn, leg_id).transfer_group_id or "")
    assert [leg.status for leg in pair] == ["skipped", "skipped"]
    # Neither balance moved: hand-computed, each account still at its opening 100,000.
    assert (accounts.balance_at(conn, giro, today), accounts.balance_at(conn, savings, today)) == (100_000, 100_000)
    assert service.list_due(conn, today=today) == []
    # The recurring items go on: the other months stay planned and no run recreates the skipped date.
    november = next(i for i in recurring.list_items(conn, rent.id) if i.due_date == date(2026, 11, 1))
    assert (november.status, november.amount_cents) == ("planned", -80_000)
    recurring.generate_planned(conn, today=today)
    rows = conn.execute(
        'SELECT status FROM "transaction" WHERE recurring_id = ? AND due_date = ?', (rent.id, "2026-10-01")
    )
    assert [row["status"] for row in rows] == ["skipped"]
    assert recurring.get_recurring(conn, rent.id).active


def test_skip_refusals_change_nothing(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-004 postcondition: only a planned item can be skipped, else nothing changes."""
    giro = _account(conn, "Test giro")
    rent = _monthly(conn, giro, 1, TODAY, start_date=date(2026, 10, 1))
    today = date(2026, 10, 20)
    booked = service.confirm(conn, _planned_id(conn, rent.id, "2026-10-01"), today=today)
    before = _snapshot(conn)

    with pytest.raises(TransactionsError):  # unknown id
        service.skip(conn, 999, today=today)
    with pytest.raises(TransactionsError):  # a booked item cannot be skipped
        service.skip(conn, booked.id, today=today)
    assert _snapshot(conn) == before


def test_skip_refused_when_a_receipt_is_attached(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-013 5b attaches a receipt to a planned item; a receipt shows the
    payment happened, so skipping (UC-ZF-004 "did not happen") is refused and nothing changes.
    """
    giro = _account(conn, "Test giro")
    item = _one_off(conn, giro, date(2026, 10, 5), -1_990, "Test subscription")
    conn.execute(
        "INSERT INTO receipt (transaction_id, file_path, media_type, image_hash, status, created_at)"
        " VALUES (?, 'receipts/2026/1.png', 'image/png', 'hash-test', 'booked', '2026-10-05T10:00:00+00:00')",
        (item,),
    )
    before = _snapshot(conn)

    with pytest.raises(service.Conflict, match="receipt"):
        service.skip(conn, item, today=date(2026, 10, 6))
    assert _snapshot(conn) == before


# --- debts: confirming the last instalment closes the debt --------------------------------------


def test_confirm_last_instalment_closes_debt(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK3 via UC-ZF-004: 500.00 at 250.00 = two instalments, the second closes it."""
    giro = _account(conn, "Test giro")
    debt = debts.create_debt(
        conn, DebtFields("Test supplier", "business", 50_000, 25_000, "monthly", date(2026, 10, 15), giro), today=TODAY
    )
    recurring_id = debt.recurring_id or 0
    today = date(2026, 11, 16)

    service.confirm(conn, _planned_id(conn, recurring_id, "2026-10-15"), today=today)
    middle = debts.get_debt(conn, debt.id, today=today)
    assert (middle.remaining_cents, middle.closed_at) == (25_000, None)

    service.confirm(conn, _planned_id(conn, recurring_id, "2026-11-15"), today=today)
    closed = debts.get_debt(conn, debt.id, today=today)
    assert (closed.remaining_cents, closed.paid_cents) == (0, 50_000)
    assert closed.closed_at is not None
    assert not recurring.get_recurring(conn, recurring_id).active
    planned = conn.execute(
        'SELECT count(*) FROM "transaction" WHERE recurring_id = ? AND status = ?', (recurring_id, "planned")
    )
    assert planned.fetchone()[0] == 0  # no planned instalment is left
    assert accounts.balance_at(conn, giro, today) == 100_000 - 50_000


def test_skipped_instalment_counts_as_done(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — debts contract (-013): a skipped instalment is done, the end moves one due date."""
    giro = _account(conn, "Test giro")
    debt = debts.create_debt(
        conn, DebtFields("Test supplier", "business", 50_000, 25_000, "monthly", date(2026, 10, 15), giro), today=TODAY
    )
    recurring_id = debt.recurring_id or 0
    assert debt.plan is not None and debt.plan.end == date(2026, 11, 15)

    service.skip(conn, _planned_id(conn, recurring_id, "2026-10-15"), today=date(2026, 10, 16))

    after = debts.get_debt(conn, debt.id, today=date(2026, 10, 16))
    assert after.remaining_cents == 50_000  # nothing was paid
    assert after.plan is not None and after.plan.end == date(2026, 12, 15)


# --- AK7: refusals ------------------------------------------------------------------------------


def test_confirm_refusals_change_nothing(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — confirm docstring: each refusal leaves every row unchanged (also a transfer's legs)."""
    giro = _account(conn, "Test giro")
    closed = _account(conn, "Test closed")
    savings = _account(conn, "Test savings")
    rent = _monthly(conn, giro, 1, TODAY, start_date=date(2026, 10, 1))
    old = _monthly(conn, closed, 1, TODAY, start_date=date(2026, 10, 1), description="Test old")
    saving = _monthly(
        conn,
        giro,
        5,
        TODAY,
        description="Test saving",
        amount_cents=10_000,
        transfer_target_account_id=savings,
        start_date=date(2026, 10, 1),
    )
    accounts.set_archived(conn, closed, True)
    accounts.set_archived(conn, savings, True)
    today = date(2026, 10, 20)
    rent_id = _planned_id(conn, rent.id, "2026-10-01")
    before = _snapshot(conn)

    with pytest.raises(TransactionsError):  # unknown id
        service.confirm(conn, 999, today=today)
    with pytest.raises(service.DueError):  # amount 0 and negative
        service.confirm(conn, rent_id, amount_cents=0, today=today)
    with pytest.raises(service.DueError):
        service.confirm(conn, rent_id, amount_cents=-5, today=today)
    with pytest.raises(service.DueError):  # a booking date after today would be planned again
        service.confirm(conn, rent_id, on_date=today + timedelta(days=1), today=today)
    with pytest.raises(AccountsError):  # archived account
        service.confirm(conn, _planned_id(conn, old.id, "2026-10-01"), today=today)
    with pytest.raises(AccountsError):  # the transfer's target is archived: the first leg is rolled back
        service.confirm(conn, _planned_id(conn, saving.id, "2026-10-05"), today=today)
    assert _snapshot(conn) == before

    booked = service.confirm(conn, rent_id, today=today)
    with pytest.raises(service.Conflict):  # a booked item is no longer planned
        service.confirm(conn, booked.id, today=today)
    with pytest.raises(service.Conflict):
        service.postpone(conn, booked.id, today + timedelta(days=5), today=today)


# --- AK8: API -----------------------------------------------------------------------------------


def test_api_flow(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-004 main 1-5 and 4a over HTTP, errors localized (SPEC AK18)."""
    today = date.today()
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test giro",
            "area": "private",
            "type": "giro",
            "opening_balance_cents": 100_000,
            "opening_date": "2020-01-01",
            "reserve_floor_cents": 0,
        },
    ).json()["id"]

    def expected(offset: int, cents: int) -> int:
        body = {"date": (today + timedelta(days=offset)).isoformat(), "amount_cents": cents, "account_id": account}
        created = client.post("/api/recurring/expected", json={**body, "text": "Test invoice"})
        assert created.status_code == 201
        return int(created.json()["id"])

    soon, later = expected(3, -20_000), expected(2, -1_000)
    listed = client.get("/api/due").json()
    assert [item["id"] for item in listed] == [later, soon]
    assert (listed[1]["confirm_date"], listed[1]["days_overdue"], listed[1]["long_overdue"]) == (
        today.isoformat(),
        0,
        False,
    )

    bad = client.post(
        f"/api/due/{soon}/confirm",
        json={"date": (today + timedelta(days=1)).isoformat()},
        headers={"Accept-Language": "de"},
    )
    assert bad.status_code == 400
    assert (
        bad.json()["detail"]
        == "Ein bestätigter Posten braucht ein Datum von heute oder früher; zum Abwarten verschiebe ihn."
    )
    assert client.post(f"/api/due/{soon}/confirm", json={"amount_cents": 9.99}).status_code == 422
    assert client.post("/api/due/999/confirm", json={}).status_code == 404

    booked = client.post(f"/api/due/{soon}/confirm", json={"amount_cents": 25_000})
    assert booked.status_code == 200
    assert (booked.json()["status"], booked.json()["amount_cents"], booked.json()["date"]) == (
        "booked",
        -25_000,
        today.isoformat(),
    )
    balance = client.get(f"/api/accounts/{account}/balance").json()["balance_cents"]
    assert balance == 100_000 - 25_000  # opening balance minus the confirmed amount
    # The planned row is replaced by the booking (new id): the old id is gone, the booking is not planned.
    assert client.post(f"/api/due/{soon}/confirm", json={}).status_code == 404
    again = client.post(f"/api/due/{booked.json()['id']}/confirm", json={}, headers={"Accept-Language": "de"})
    assert (again.status_code, again.json()["detail"]) == (409, "Hier lassen sich nur geplante Posten ändern.")

    third = expected(1, -500)
    skipped = client.post(f"/api/due/{third}/skip")
    assert (skipped.status_code, skipped.json()["status"]) == (200, "skipped")
    assert third not in [item["id"] for item in client.get("/api/due").json()]
    again_skipped = client.post(f"/api/due/{third}/skip", headers={"Accept-Language": "de"})
    assert (again_skipped.status_code, again_skipped.json()["detail"]) == (
        409,
        "Hier lassen sich nur geplante Posten ändern.",
    )
    assert client.post("/api/due/999/skip").status_code == 404

    refused = client.post(f"/api/due/{later}/postpone", json={"date": today.isoformat()})
    assert refused.status_code == 400
    moved = client.post(f"/api/due/{later}/postpone", json={"date": (today + timedelta(days=30)).isoformat()})
    assert (moved.status_code, moved.json()["status"]) == (200, "planned")
    assert client.get("/api/due").json() == []  # beyond today + 14 days
