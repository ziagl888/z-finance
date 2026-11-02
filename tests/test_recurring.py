"""UC-ZF-003 recurring and expected items: schedule, generation engine, moves, pause, replace, API.

Expected values are hand-computed from the use case and the calendar; every value is synthetic.
"today" is 2026-09-19 (a Saturday), so the 12-month horizon ends on 2027-09-19.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.config import Settings
from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.backup import service as backup
from zfinance.domain.recurring import service
from zfinance.domain.recurring.schedule import Schedule, add_months, due_dates
from zfinance.domain.recurring.service import RecurringFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "recurring.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


def _account(conn: sqlite3.Connection, name: str) -> int:
    fields = AccountFields(name, "private", "giro", 100_000, date(2026, 9, 1))  # type: ignore[arg-type]
    return accounts.create_account(conn, fields).id


def _category(conn: sqlite3.Connection, name: str = "Test rent") -> int:
    return accounts.create_category(conn, CategoryFields(name, "expense")).id  # type: ignore[arg-type]


def _monthly(conn: sqlite3.Connection, account_id: int, day: int, **overrides: object) -> service.Recurring:
    values: dict[str, object] = {
        "description": "Test rent",
        "amount_cents": -80_000,
        "account_id": account_id,
        "interval": "monthly",
        "start_date": date(2026, 10, 1),
        "day_of_month": day,
    }
    values.update(overrides)
    return service.create_recurring(conn, RecurringFields(**values), today=TODAY)  # type: ignore[arg-type]


def _rows(conn: sqlite3.Connection, recurring_id: int) -> list[tuple[str, str, int, str]]:
    """(date, due_date, amount, status) of every row of the item, by date."""
    return [
        (row["date"], row["due_date"], row["amount_cents"], row["status"])
        for row in conn.execute(
            'SELECT * FROM "transaction" WHERE recurring_id = ? ORDER BY date, amount_cents', (recurring_id,)
        )
    ]


def _count(conn: sqlite3.Connection, recurring_id: int | None = None) -> int:
    sql = 'SELECT count(*) FROM "transaction" WHERE ? IS NULL OR recurring_id = ?'
    return int(conn.execute(sql, (recurring_id, recurring_id)).fetchone()[0])


def _confirm(conn: sqlite3.Connection, recurring_id: int, due: str) -> None:
    """Stand-in for confirming a due item (UC-ZF-004, its own task): the row becomes booked."""
    conn.execute(
        "UPDATE \"transaction\" SET status = 'booked' WHERE recurring_id = ? AND due_date = ?", (recurring_id, due)
    )


# --- AK1: day 31 in a short month ---------------------------------------------------------------


def test_recurring_day_clamps_to_month_end(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-003 AK1 / 1a, SPEC AK3: day 31 falls on 28 Feb 2027 (and 29 Feb 2028)."""
    giro = _account(conn, "Test giro")
    item = _monthly(conn, giro, 31)

    dates = [row[0] for row in _rows(conn, item.id)]
    assert dates == [
        "2026-10-31", "2026-11-30", "2026-12-31", "2027-01-31", "2027-02-28", "2027-03-31",
        "2027-04-30", "2027-05-31", "2027-06-30", "2027-07-31", "2027-08-31",
    ]  # fmt: skip
    assert due_dates(Schedule("monthly", date(2028, 1, 1), None, 31), date(2028, 2, 1), date(2028, 2, 29)) == [
        date(2028, 2, 29)
    ]
    # Quarterly and yearly repeat in the start month; weekly on the weekday (0 = Monday).
    assert due_dates(Schedule("quarterly", date(2026, 11, 15), None, 1), TODAY, date(2027, 9, 19)) == [
        date(2027, 2, 1), date(2027, 5, 1), date(2027, 8, 1)
    ]  # fmt: skip
    assert due_dates(Schedule("yearly", date(2024, 2, 29), None, 29), TODAY, date(2028, 12, 31)) == [
        date(2027, 2, 28), date(2028, 2, 29)
    ]  # fmt: skip
    assert due_dates(Schedule("weekly", TODAY, date(2026, 10, 5), None, 0), TODAY, date(2027, 1, 1)) == [
        date(2026, 9, 21), date(2026, 9, 28), date(2026, 10, 5)
    ]  # fmt: skip


# --- AK2: idempotent generation -----------------------------------------------------------------


def test_recurring_generation_idempotent(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-003 AK2 / 2a, SPEC AK3; counts hand-computed from the calendar.

    Monthly on the 1st from 2026-10-01: Oct 2026 .. Sep 2027 = 12. Weekly Mondays from 2026-09-21
    to 2027-09-19: 52. A monthly transfer on the 15th: 12 occurrences = 24 rows.
    """
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    rent = _monthly(conn, giro, 1, category_id=_category(conn))
    weekly = service.create_recurring(
        conn,
        RecurringFields("Test market", -2_000, giro, "weekly", TODAY, weekday=0),
        today=TODAY,
    )
    saving = service.create_recurring(
        conn,
        RecurringFields(
            "Test saving", 10_000, giro, "monthly", TODAY, transfer_target_account_id=savings, day_of_month=15
        ),
        today=TODAY,
    )

    assert (_count(conn, rent.id), _count(conn, weekly.id), _count(conn, saving.id)) == (12, 52, 24)
    assert _rows(conn, rent.id)[0] == ("2026-10-01", "2026-10-01", -80_000, "planned")
    assert {row["source"] for row in conn.execute('SELECT source FROM "transaction"')} == {"recurring"}
    assert service.get_recurring(conn, saving.id).planned_count == 12
    # A transfer occurrence is a real transfer pair: two legs, one group, sum 0 (via -008).
    legs = conn.execute(
        "SELECT account_id, amount_cents FROM \"transaction\" WHERE recurring_id = ? AND due_date = '2026-10-15'"
        " ORDER BY amount_cents",
        (saving.id,),
    ).fetchall()
    assert [tuple(leg) for leg in legs] == [(giro, -10_000), (savings, 10_000)]

    before = _count(conn)
    assert service.generate_planned(conn, today=TODAY) == service.GenerationReport(created=0, failed=[])
    assert _count(conn) == before

    # One month later the horizon grows by exactly one due date per monthly item (rent: 2027-10-01).
    later = service.generate_planned(conn, today=date(2026, 10, 19), recurring_id=rent.id)
    assert later.created == 1
    assert _count(conn, rent.id) == 13


def test_generation_starts_today_and_respects_opening_and_end(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-003 step 2 (from now 12 months ahead), 1b (end date), UC-ZF-001 1a."""
    giro = _account(conn, "Test giro")
    # Started in the past: no overdue rows are created, the first is the next due date.
    past = _monthly(conn, giro, 5, start_date=date(2026, 1, 5), end_date=date(2026, 12, 31))
    assert [row[0] for row in _rows(conn, past.id)] == ["2026-10-05", "2026-11-05", "2026-12-05"]

    # Ending the item on 2026-10-31 removes the later planned rows; a booked one stays.
    _confirm(conn, past.id, "2026-10-05")
    fields = RecurringFields("Test rent", -80_000, giro, "monthly", date(2026, 1, 5), day_of_month=5)
    service.update_recurring(conn, past.id, replace(fields, end_date=date(2026, 10, 31)), today=TODAY)
    assert _rows(conn, past.id) == [("2026-10-05", "2026-10-05", -80_000, "booked")]


def test_generation_window_is_half_open(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-003 Erfolgsgarantie (exactly one planned row per due date in the
    next 12 months) and the owner decision of T-2026-CU-9130-047 (window [today, today + 12 months));
    dates hand-computed from the calendar.

    Both items start today (2026-09-19) on the 19th. The window ends before 2027-09-19, so the yearly
    item has one row (not this year's and next year's) and the monthly one 2026-09-19 .. 2027-08-19 = 12.
    """
    giro = _account(conn, "Test giro")
    yearly = service.create_recurring(
        conn, RecurringFields("Test insurance", -30_000, giro, "yearly", TODAY, day_of_month=19), today=TODAY
    )
    monthly = service.create_recurring(
        conn, RecurringFields("Test gym", -3_000, giro, "monthly", TODAY, day_of_month=19), today=TODAY
    )

    assert [row[1] for row in _rows(conn, yearly.id)] == ["2026-09-19"]
    monthly_dues = [row[1] for row in _rows(conn, monthly.id)]
    assert (len(monthly_dues), monthly_dues[0], monthly_dues[-1]) == (12, "2026-09-19", "2027-08-19")

    # A second run on the same day (app start) adds nothing; the next day the window reaches the anniversary.
    assert service.generate_planned(conn, today=TODAY).created == 0
    assert service.generate_planned(conn, today=date(2026, 9, 20), recurring_id=yearly.id).created == 1
    assert [row[1] for row in _rows(conn, yearly.id)] == ["2026-09-19", "2027-09-19"]


# --- AK3: deactivate ----------------------------------------------------------------------------


def test_deactivate_removes_unconfirmed_keeps_confirmed(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-003 AK3 / 1c: no unconfirmed rows remain, confirmed ones stay."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    rent = _monthly(conn, giro, 1)
    saving = service.create_recurring(
        conn,
        RecurringFields(
            "Test saving", 10_000, giro, "monthly", TODAY, transfer_target_account_id=savings, day_of_month=15
        ),
        today=TODAY,
    )
    _confirm(conn, rent.id, "2026-10-01")
    _confirm(conn, saving.id, "2026-10-15")
    moved = next(item for item in service.list_items(conn, rent.id) if item.due_date == date(2026, 11, 1))
    service.move_item(conn, moved.id, date(2026, 11, 3), 80_000, today=TODAY)

    for item_id in (rent.id, saving.id):
        paused = service.set_active(conn, item_id, False, today=TODAY)
        assert (paused.active, paused.planned_count, paused.next_due_date) == (False, 0, None)
    assert _rows(conn, rent.id) == [("2026-10-01", "2026-10-01", -80_000, "booked")]
    assert [row[2:] for row in _rows(conn, saving.id)] == [(-10_000, "booked"), (10_000, "booked")]
    assert service.generate_planned(conn, today=TODAY).created == 0  # paused items are not generated

    # Resuming fills the horizon again, but not the due date that is already booked.
    resumed = service.set_active(conn, rent.id, True, today=TODAY)
    assert resumed.planned_count == 11
    assert _count(conn, rent.id) == 12


# --- AK4: a moved row stays ---------------------------------------------------------------------


def test_moved_item_survives_regeneration(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-003 AK4 / 2b: a moved row keeps its new date after the next run."""
    giro = _account(conn, "Test giro")
    rent = _monthly(conn, giro, 1)
    items = {item.due_date: item for item in service.list_items(conn, rent.id)}

    moved = service.move_item(conn, items[date(2026, 11, 1)].id, date(2026, 11, 4), 81_000, today=TODAY)
    assert (moved.date, moved.due_date, moved.amount_cents, moved.moved) == (
        date(2026, 11, 4), date(2026, 11, 1), -81_000, True
    )  # fmt: skip
    # Moved from the booking list instead (quick entry, -008): date differs from the due date.
    december = items[date(2026, 12, 1)]
    transactions.update_transaction(
        conn, december.id, BookingFields(date(2026, 12, 2), -80_000, giro, None, "Test rent"), today=TODAY
    )

    assert service.generate_planned(conn, today=TODAY).created == 0
    by_due = {item.due_date: item for item in service.list_items(conn, rent.id)}
    assert (by_due[date(2026, 11, 1)].date, by_due[date(2026, 11, 1)].amount_cents) == (date(2026, 11, 4), -81_000)
    assert by_due[date(2026, 12, 1)].date == date(2026, 12, 2)
    assert by_due[date(2026, 12, 1)].moved
    assert _count(conn, rent.id) == 12

    # Changing the amount of the item rewrites the unmoved planned rows, not the moved ones.
    fields = RecurringFields("Test rent", -85_000, giro, "monthly", date(2026, 10, 1), day_of_month=1)
    service.update_recurring(conn, rent.id, fields, today=TODAY)
    by_due = {item.due_date: item for item in service.list_items(conn, rent.id)}
    assert len(by_due) == 12
    assert by_due[date(2026, 10, 1)].amount_cents == -85_000
    assert (by_due[date(2026, 11, 1)].date, by_due[date(2026, 11, 1)].amount_cents) == (date(2026, 11, 4), -81_000)
    assert by_due[date(2026, 12, 1)].date == date(2026, 12, 2)


# --- replace_planned (contract for the CSV import) ----------------------------------------------


def test_replace_planned_marks_due_date_done(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC AK6 + orchestrator contract: an imported booking replaces the planned row."""
    giro = _account(conn, "Test giro")
    savings = _account(conn, "Test savings")
    rent = _monthly(conn, giro, 1)
    planned = next(item for item in service.list_items(conn, rent.id) if item.due_date == date(2026, 10, 1))
    real = transactions.create_transaction(
        conn, BookingFields(date(2026, 9, 30), -80_050, giro, text="TEST BANK LINE"), today=date(2026, 10, 2)
    )

    replaced = service.replace_planned(conn, planned.id, real.id)
    assert (replaced.recurring_id, replaced.status, replaced.amount_cents) == (rent.id, "booked", -80_050)
    assert _count(conn, rent.id) == 12
    assert service.generate_planned(conn, today=TODAY).created == 0  # 2026-10-01 counts as done

    # A planned transfer leg: the real booking becomes the leg, the other leg is booked mirrored.
    saving = service.create_recurring(
        conn,
        RecurringFields(
            "Test saving", 10_000, giro, "monthly", TODAY, transfer_target_account_id=savings, day_of_month=15
        ),
        today=TODAY,
    )
    leg = next(item for item in service.list_items(conn, saving.id) if item.due_date == date(2026, 10, 15))
    bank = transactions.create_transaction(
        conn, BookingFields(date(2026, 10, 16), -10_000, giro, text="TEST TRANSFER"), today=date(2026, 10, 16)
    )
    service.replace_planned(conn, leg.id, bank.id)
    pair = transactions.get_transfer(conn, leg.transfer_group_id or "")
    assert [(p.id, p.amount_cents, p.status, p.date) for p in pair] == [
        (bank.id, -10_000, "booked", date(2026, 10, 16)),
        (pair[1].id, 10_000, "booked", date(2026, 10, 16)),
    ]
    assert service.generate_planned(conn, today=TODAY).created == 0


def test_replace_planned_refusals(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — replace_planned docstring: each refusal leaves both rows unchanged."""
    giro = _account(conn, "Test giro")
    other = _account(conn, "Test other")
    rent = _monthly(conn, giro, 1)
    planned = service.list_items(conn, rent.id)[0]
    elsewhere = transactions.create_transaction(conn, BookingFields(date(2026, 9, 10), -1, other), today=TODAY)
    future = transactions.create_transaction(conn, BookingFields(date(2026, 12, 24), -1, giro), today=TODAY)

    with pytest.raises(service.Conflict):
        service.replace_planned(conn, planned.id, elsewhere.id)  # different account
    with pytest.raises(service.Conflict):
        service.replace_planned(conn, planned.id, future.id)  # the "real" row is only planned
    with pytest.raises(service.NotFound):
        service.replace_planned(conn, 999, elsewhere.id)
    assert _count(conn, rent.id) == 12
    assert transactions.get_transaction(conn, elsewhere.id).recurring_id is None


# --- expected one-off items, failures, API, backup ------------------------------------------------


def test_expected_item_is_planned_without_recurring(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-003 step 3 / postcondition: date and amount stored as a planned item."""
    giro = _account(conn, "Test giro")
    item = service.create_expected(
        conn, BookingFields(date(2026, 11, 20), 300_000, giro, text="Test invoice"), today=TODAY
    )
    assert (item.status, item.recurring_id, item.amount_cents) == ("planned", None, 300_000)
    assert [e.id for e in service.list_expected(conn)] == [item.id]
    with pytest.raises(service.RecurringError):
        service.create_expected(conn, BookingFields(TODAY, -100, giro), today=TODAY)


def test_generation_failure_is_isolated(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Oracle: spec-anchored — orchestrator note: a failing item or run is logged and never stops the app."""
    giro = _account(conn, "Test giro")
    closed = _account(conn, "Test closed")
    ok = _monthly(conn, giro, 1)
    broken = _monthly(conn, closed, 1)
    accounts.set_archived(conn, closed, True)

    with caplog.at_level(logging.WARNING):
        report = service.generate_planned(conn, today=date(2026, 10, 19))
    assert report == service.GenerationReport(created=1, failed=[broken.id])
    assert _count(conn, ok.id) == 13
    assert f"recurring item {broken.id} not generated" in caplog.text

    def explode(*_args: object, **_kwargs: object) -> service.GenerationReport:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(service, "generate_planned", explode)
    settings.data_dir.mkdir(parents=True)
    assert service.run_startup_generation(settings) is None


def _freeze_today(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    """Make ``date.today()`` return ``day`` in the services the HTTP flow reaches without a ``today``."""

    class FrozenDate(date):
        @classmethod
        def today(cls) -> FrozenDate:
            return cls(day.year, day.month, day.day)

    monkeypatch.setattr(service, "date", FrozenDate)
    monkeypatch.setattr(transactions, "date", FrozenDate)


# The monthly item starts on the 1st of the next month and the window is [today, today + 12 months).
# Hand-counted: on the 1st of a month the 12th due date falls on the open boundary, so 11 remain.
_API_FLOW_DAYS = [
    pytest.param(date(2026, 10, 1), 11, id="first-of-month"),
    pytest.param(date(2026, 10, 15), 12, id="mid-month"),
    pytest.param(date(2026, 10, 28), 12, id="28th"),
    pytest.param(date(2026, 10, 31), 12, id="31st"),
    pytest.param(date(2026, 12, 31), 12, id="new-years-eve"),
    pytest.param(date(2027, 1, 1), 11, id="new-years-day"),
]


@pytest.mark.parametrize(("today", "planned"), _API_FLOW_DAYS)
def test_api_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch, today: date, planned: int) -> None:
    """Oracle: spec-anchored — UC-ZF-003 steps 1-3 over HTTP, errors localized (SPEC AK18); the window
    is half-open (SPEC T-2026-CU-9130-047), so the count is 11 on the 1st of a month and 12 on any
    other day — pinned per calendar day, never read from the wall clock."""
    _freeze_today(monkeypatch, today)
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test giro",
            "area": "private",
            "type": "giro",
            "opening_balance_cents": 0,
            "opening_date": "2026-01-01",
            "reserve_floor_cents": 0,
        },
    ).json()["id"]
    body = {
        "description": "Test subscription",
        "amount_cents": -999,
        "account_id": account,
        "interval": "monthly",
        "day_of_month": 1,
        "start_date": add_months(today.replace(day=1), 1).isoformat(),
    }
    created = client.post("/api/recurring", json=body)
    assert created.status_code == 201
    item = created.json()
    assert (item["active"], item["planned_count"]) == (True, planned)
    items = client.get(f"/api/recurring/{item['id']}/items").json()
    assert len(items) == item["planned_count"]

    moved = client.put(f"/api/recurring/items/{items[0]['id']}", json={"date": items[0]["date"], "amount_cents": 1_099})
    assert (moved.status_code, moved.json()["amount_cents"], moved.json()["moved"]) == (200, -1_099, True)
    assert client.post("/api/recurring/generate").json() == {"created": 0, "failed": []}

    paused = client.post(f"/api/recurring/{item['id']}/deactivate").json()
    assert paused["planned_count"] == 0
    assert client.get(f"/api/recurring/{item['id']}/items").json() == []

    bad = client.post("/api/recurring", json={**body, "end_date": "2020-01-01"}, headers={"Accept-Language": "de"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Das Enddatum darf nicht vor dem Beginn liegen."
    assert client.post("/api/recurring", json={**body, "amount_cents": 9.99}).status_code == 422
    assert client.get("/api/recurring/999").status_code == 404

    expected = client.post(
        "/api/recurring/expected",
        json={"date": "2099-01-15", "amount_cents": 50_000, "account_id": account, "text": "Test invoice"},
    )
    assert expected.status_code == 201
    assert [e["id"] for e in client.get("/api/recurring/expected").json()] == [expected.json()["id"]]

    assert client.delete(f"/api/recurring/{item['id']}").status_code == 204
    assert client.get("/api/recurring").json() == []


def test_backup_roundtrip_recurring(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Oracle: spec-anchored — ARCHITECTURE "Backup and restore": the new table and columns are covered."""
    giro = _account(conn, "Test giro")
    rent = _monthly(conn, giro, 31)
    moved = service.list_items(conn, rent.id)[0]
    service.move_item(conn, moved.id, date(2026, 10, 30), 80_000, today=TODAY)
    raw = backup.dumps_export(backup.export_data(conn))

    target = connect(tmp_path / "target.sqlite3")
    try:
        migrate(target)
        result = backup.restore_export(target, raw)
        assert (result.rows["recurring"], result.rows["transaction"]) == (1, 11)
        assert service.list_items(target, rent.id) == service.list_items(conn, rent.id)
        assert service.generate_planned(target, today=TODAY).created == 0
    finally:
        target.close()
