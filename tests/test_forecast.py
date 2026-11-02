"""UC-ZF-006 liquidity forecast: curve, line after levies, reserve, what-if, items, emergency, API.

Expected values are hand-computed from the use case; every value is synthetic. "today" is
2026-09-19, so "the 10th" / "the 20th" of AK1 are 2026-10-10 / 2026-10-20 and the levy
back-payment of 2026 falls due on 2027-07-01 (inside the 12-month horizon, which ends 2027-09-18).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, AccountType, Area
from zfinance.domain.forecast import service
from zfinance.domain.forecast.service import ForecastDay, View, WhatIfItem
from zfinance.domain.levy import service as levy
from zfinance.domain.levy.service import LevyFields
from zfinance.domain.pots import service as pots
from zfinance.domain.pots.service import PotFields
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.schedule import Interval, add_months, due_dates
from zfinance.domain.recurring.service import RecurringFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransferFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
OPENED = date(2026, 1, 1)


# --- fixtures and helpers ----------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "forecast.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


def _account(
    conn: sqlite3.Connection,
    name: str,
    cents: int,
    *,
    reserve: int = 0,
    area: Area = "private",
    kind: AccountType = "giro",
) -> int:
    return accounts.create_account(conn, AccountFields(name, area, kind, cents, OPENED, reserve)).id


def _planned(conn: sqlite3.Connection, account: int, cents: int, on: date, *, today: date = TODAY) -> int:
    """A planned manual row (dated after ``today``, so the transactions service stores it planned)."""
    row = transactions.create_transaction(conn, BookingFields(on, cents, account, None, "Test item"), today=today)
    assert row.status == "planned"
    return row.id


def _on(result: service.Forecast, day: date) -> ForecastDay:
    return next(point for point in result.days if point.date == day)


def _booking_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute('SELECT count(*) FROM "transaction"').fetchone()[0])


# --- AK1, AK12, UC 3a --------------------------------------------------------------------------


def test_forecast_minimum_and_floor_breach(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK1 hand-computed: 2,000 - 1,500 = 500 € on the 10th
    (below the 1,000 € reserve from the 10th), + 3,000 = 3,500 € from the 20th; TBD-5: the total
    view checks the total reserve the same way; UC 3a: no reserve, no alarm."""
    giro = _account(conn, "Test giro", 200_000, reserve=100_000)
    _planned(conn, giro, -150_000, date(2026, 10, 10))
    _planned(conn, giro, 300_000, date(2026, 10, 20))

    result = service.forecast(conn, View(giro), 3, today=TODAY)
    assert (result.start, result.end, len(result.days)) == (TODAY, date(2026, 12, 18), 91)
    assert _on(result, date(2026, 10, 9)).balance_cents == 200_000
    assert (result.minimum_date, result.minimum_cents) == (date(2026, 10, 10), 50_000)
    assert result.first_breach_date == date(2026, 10, 10)
    assert result.shortfall_cents == 50_000
    assert _on(result, date(2026, 10, 20)).balance_cents == 350_000
    assert result.days[-1].available_cents == 350_000
    assert (result.planned_income_cents, result.planned_expense_cents) == (300_000, -150_000)
    assert (result.reserve_cents, result.reserve_missing, result.levy_applies) == (100_000, False, False)

    accounts.set_total_reserve_floor(conn, 100_000)
    total = service.forecast(conn, View(), 3, today=TODAY)
    assert (total.scope, total.minimum_date, total.minimum_cents) == ("total", date(2026, 10, 10), 50_000)
    assert total.first_breach_date == date(2026, 10, 10)

    accounts.set_total_reserve_floor(conn, 0)  # UC 3a: not set → no breach, a hint instead
    unset = service.forecast(conn, View(), 3, today=TODAY)
    assert (unset.reserve_missing, unset.first_breach_date, unset.shortfall_cents) == (True, None, 0)


def test_forecast_total_floor_breach(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC AK12: accounts of 3,000 € and 1,000 € without own reserve,
    total reserve 5,000 € → the total view (4,000 €) is breached from today, each account view not."""
    first = _account(conn, "Test giro", 300_000)
    second = _account(conn, "Test savings", 100_000, kind="savings")
    accounts.set_total_reserve_floor(conn, 500_000)

    total = service.forecast(conn, View(), 3, today=TODAY)
    assert (total.minimum_cents, total.first_breach_date, total.shortfall_cents) == (400_000, TODAY, 100_000)
    for account in (first, second):
        view = service.forecast(conn, View(account), 3, today=TODAY)
        assert (view.reserve_missing, view.first_breach_date) == (True, None)


# --- AK2, UC 2c --------------------------------------------------------------------------------


def test_forecast_transfer_neutral_in_total(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK2 / 2a: a planned transfer of 500 € on 2026-10-01 lowers
    the source by 500 €, raises the target by 500 € and leaves the total view unchanged."""
    giro = _account(conn, "Test giro", 300_000)
    savings = _account(conn, "Test savings", 100_000, kind="savings")
    before = service.forecast(conn, View(), 6, today=TODAY)

    transactions.create_transfer(conn, TransferFields(date(2026, 10, 1), 50_000, giro, savings), today=TODAY)

    after = service.forecast(conn, View(), 6, today=TODAY)
    assert after.days == before.days
    assert (after.planned_income_cents, after.planned_expense_cents) == (0, 0)
    source = service.forecast(conn, View(giro), 6, today=TODAY)
    target = service.forecast(conn, View(savings), 6, today=TODAY)
    assert _on(source, date(2026, 10, 1)).balance_cents == 250_000
    assert _on(target, date(2026, 10, 1)).balance_cents == 150_000
    assert source.planned_expense_cents == -50_000 and target.planned_income_cents == 50_000


def test_forecast_liability_hidden_instalment_counts(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 2c: an instalment of 200 € into a loan is an outflow of the
    paying account; the liability account (-5,000 €) is in no view and refused as one."""
    giro = _account(conn, "Test giro", 300_000)
    loan = _account(conn, "Test loan", -500_000, kind="liability")
    transactions.create_transfer(conn, TransferFields(date(2026, 10, 1), 20_000, giro, loan), today=TODAY)

    total = service.forecast(conn, View(), 3, today=TODAY)
    assert total.days[0].balance_cents == 300_000
    assert _on(total, date(2026, 10, 1)).balance_cents == 280_000
    assert total.planned_expense_cents == -20_000
    with pytest.raises(service.Conflict):
        service.forecast(conn, View(loan), 3, today=TODAY)


# --- AK6 ---------------------------------------------------------------------------------------


def test_forecast_pot_expense_on_target_date(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK6 / 2d: pot target 3,000 €, target date on the
    last day of the 6-month window (2027-03-18), "plan expense" → the savings curve drops by 3,000 €
    on that day; the savings
    plan (500 € on the 1st, from the giro) moves money between accounts, the total view never
    falls on those days."""
    giro = _account(conn, "Test giro", 500_000)
    savings = _account(conn, "Test savings", 0, kind="savings")
    target = date(2027, 3, 18)
    pot = pots.create_pot(
        conn,
        PotFields("Test holiday", savings, target_amount_cents=300_000, target_date=target, plan_expense=True),
        today=TODAY,
    )
    pots.set_savings_plan(conn, pot.id, giro, amount_cents=50_000, start_date=date(2026, 10, 1), today=TODAY)

    saving = service.forecast(conn, View(savings), 6, today=TODAY)
    before, on_target = _on(saving, target - timedelta(days=1)), _on(saving, target)
    assert on_target.balance_cents - before.balance_cents == -300_000

    total = service.forecast(conn, View(), 6, today=TODAY)
    deposit_days = [date(2026, month, 1) for month in (10, 11, 12)] + [date(2027, month, 1) for month in (1, 2, 3)]
    for day in deposit_days:
        assert _on(total, day).balance_cents == _on(total, day - timedelta(days=1)).balance_cents
    assert _on(total, target).balance_cents == 500_000 - 300_000
    assert total.planned_expense_cents == -300_000


# --- AK7, core rule ----------------------------------------------------------------------------


def _levy_setup(conn: sqlite3.Connection) -> int:
    """Business account 2,000 € + booked income 10,000 € at 30 % → balance 12,000 €, gap 3,000 €,
    reserve 10,000 €; the sync plans "Levy back-payment 2026" -3,000 € on 2027-07-01."""
    business = _account(conn, "Test business", 200_000, reserve=1_000_000, area="business")
    transactions.create_transaction(conn, BookingFields(TODAY, 1_000_000, business, None, "Test client"), today=TODAY)
    levy.save_settings(conn, LevyFields(3000, account_id=business), today=TODAY)
    accounts.set_total_reserve_floor(conn, 1_000_000)
    return business


def test_forecast_available_after_levy_ak7(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK7: balance 12,000 €, open levy gap 3,000 €, reserve
    10,000 € → "available after levies" 9,000 €, reserve breached although the balance is above it;
    a private account view does not carry the levy."""
    business = _levy_setup(conn)
    private = _account(conn, "Test giro", 50_000)
    assert levy.levy_gap(conn, TODAY) == 300_000

    for view in (View(business), View(area="business")):
        result = service.forecast(conn, view, 3, today=TODAY)
        assert (result.days[0].balance_cents, result.days[0].available_cents) == (1_200_000, 900_000)
        assert (result.minimum_cents, result.first_breach_date) == (900_000, TODAY)
        assert (result.levy_applies, result.levy_gap_cents, result.shortfall_cents) == (True, 300_000, 100_000)
    total = service.forecast(conn, View(), 3, today=TODAY)
    assert (total.days[0].available_cents, total.first_breach_date) == (950_000, TODAY)

    own = service.forecast(conn, View(private), 3, today=TODAY)
    assert own.levy_applies is False
    assert all(point.available_cents == point.balance_cents == 50_000 for point in own.days)


def test_forecast_backpayment_not_counted_twice(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — core rule + orchestrator decision: the back-payment item (-3,000 € on
    2027-07-01) counts in the balance on its day and the year's gap is not subtracted from then on:
    day before 12,000 / 9,000 €, due day 9,000 / 9,000 € — the available line never drops twice.
    Over the turn of the year the 2026 gap stays (2027 has no booked income)."""
    business = _levy_setup(conn)
    due = date(2027, 7, 1)
    result = service.forecast(conn, View(business), 12, today=TODAY)

    for day in (date(2026, 12, 31), date(2027, 1, 1), due - timedelta(days=1)):
        point = _on(result, day)
        assert (point.balance_cents, point.available_cents) == (1_200_000, 900_000), day
    on_due = _on(result, due)
    assert (on_due.balance_cents, on_due.available_cents) == (900_000, 900_000)
    assert result.days[-1].available_cents == 900_000
    assert [item.amount_cents for item in service.items_at(conn, due, View(business), today=TODAY)] == [-300_000]


# --- UC 1a, step 4, 2b -------------------------------------------------------------------------


def test_forecast_what_if_stores_nothing(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 1a / AK3 engine part: a hypothetical -1,000 € on 2026-10-05
    lowers the curve from that day by 1,000 € and stores nothing (booking count unchanged)."""
    giro = _account(conn, "Test giro", 200_000)
    other = _account(conn, "Test cash", 10_000, kind="cash")
    count = _booking_count(conn)
    item = WhatIfItem(date(2026, 10, 5), -100_000, giro, "Test laptop")

    result = service.what_if(conn, [item, WhatIfItem(date(2026, 10, 5), -5_000, other)], View(giro), 3, today=TODAY)
    assert _on(result, date(2026, 10, 4)).balance_cents == 200_000
    assert _on(result, date(2026, 10, 5)).balance_cents == 100_000  # the cash item is outside the view
    assert result.planned_expense_cents == -100_000
    assert _booking_count(conn) == count
    assert service.forecast(conn, View(giro), 3, today=TODAY).days[-1].balance_cents == 200_000

    for bad in (WhatIfItem(date(2026, 10, 5), 0, giro), WhatIfItem(TODAY - timedelta(days=1), -1, giro)):
        with pytest.raises(service.ForecastError):
            service.what_if(conn, [bad], View(giro), 3, today=TODAY)
    with pytest.raises(accounts.NotFound):
        service.what_if(conn, [WhatIfItem(TODAY, -1, 9999)], View(), 3, today=TODAY)


def test_forecast_items_at_and_overdue(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 step 4 / AK4 engine part and 2b: an unconfirmed item of
    -200 € dated 2026-09-10 counts from today and is overdue; clicking a day lists exactly the rows
    of that day, and their sum is the day's change of the curve."""
    giro = _account(conn, "Test giro", 200_000)
    overdue = _planned(conn, giro, -20_000, date(2026, 9, 10), today=date(2026, 9, 1))
    later = _planned(conn, giro, -150_000, date(2026, 10, 10))
    _planned(conn, giro, 30_000, date(2026, 10, 11))

    result = service.forecast(conn, View(), 3, today=TODAY)
    assert result.overdue_count == 1
    assert result.days[0].balance_cents == 180_000

    today_items = service.items_at(conn, TODAY, today=TODAY)
    assert [(i.transaction_id, i.overdue, i.date) for i in today_items] == [(overdue, True, date(2026, 9, 10))]
    day = date(2026, 10, 10)
    items = service.items_at(conn, day, today=TODAY)
    assert [(i.transaction_id, i.kind, i.amount_cents) for i in items] == [(later, "planned", -150_000)]
    assert (
        sum(i.amount_cents for i in items)
        == _on(result, day).balance_cents - _on(result, day - timedelta(1)).balance_cents
    )
    extra = service.items_at(conn, day, what_if=[WhatIfItem(day, -1_000, giro)], today=TODAY)
    assert [i.kind for i in extra] == ["planned", "what_if"]
    with pytest.raises(service.ForecastError):
        service.items_at(conn, TODAY - timedelta(days=1), today=TODAY)


# --- UC 3b -------------------------------------------------------------------------------------


def test_forecast_emergency_suggestion(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 3b with the pots' emergency order (UC-ZF-011): balance
    2,000 €, reserve 3,000 € → shortfall 1,000 €; pots rank 1 300 €, locked 500 €, rank 2 1,000 €
    → 300 € from rank 1, 700 € from rank 2, the locked pot never."""
    giro = _account(conn, "Test giro", 200_000, reserve=300_000)
    first = pots.create_pot(conn, PotFields("Test TV", giro), today=TODAY).id
    locked = pots.create_pot(conn, PotFields("Test fund", giro, locked=True), today=TODAY).id
    second = pots.create_pot(conn, PotFields("Test trip", giro), today=TODAY).id
    for pot_id, cents in ((first, 30_000), (locked, 50_000), (second, 100_000)):
        pots.deposit(conn, pot_id, cents, today=TODAY)

    result = service.forecast(conn, View(giro), 3, today=TODAY)
    assert result.shortfall_cents == 100_000
    suggestion = service.emergency_suggestion(conn, result, today=TODAY)
    assert suggestion is not None
    assert [(line.pot_id, line.amount_cents) for p in suggestion.proposals for line in p.lines] == [
        (first, 30_000),
        (second, 70_000),
    ]
    assert (suggestion.covered_cents, suggestion.uncovered_cents) == (100_000, 0)

    accounts.update_account(conn, giro, AccountFields("Test giro", "private", "giro", 200_000, OPENED, 0))
    assert service.emergency_suggestion(conn, service.forecast(conn, View(giro), 3, today=TODAY)) is None


# --- AK5 ---------------------------------------------------------------------------------------


@pytest.mark.perf
def test_forecast_performance_10k_bookings(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK5: the 12-month forecast over all accounts with 10,000
    bookings and 200 planned rows (and levy settings, so the levy line is computed) takes < 1 s."""
    business = _account(conn, "Test business", 1_000_000, area="business")
    giro = _account(conn, "Test giro", 500_000)
    savings = _account(conn, "Test savings", 0, kind="savings")
    levy.save_settings(conn, LevyFields(3000, account_id=business), today=TODAY)
    created = "2026-01-01T00:00:00+00:00"
    booked = [
        ((OPENED + timedelta(days=i % 261)).isoformat(), (i % 7 - 3 or 1) * 1_000, (business, giro, savings)[i % 3])
        for i in range(10_000)
    ]
    planned = [
        ((TODAY + timedelta(days=1 + i * 365 // 200)).isoformat(), (i % 5 - 2 or -1) * 5_000, (business, giro)[i % 2])
        for i in range(200)
    ]
    conn.executemany(
        'INSERT INTO "transaction" (date, amount_cents, account_id, text, status, created_at)'
        " VALUES (?, ?, ?, 'Test row', 'booked', ?)",
        [(*row, created) for row in booked],
    )
    conn.executemany(
        'INSERT INTO "transaction" (date, amount_cents, account_id, text, status, created_at)'
        " VALUES (?, ?, ?, 'Test plan', 'planned', ?)",
        [(*row, created) for row in planned],
    )
    assert _booking_count(conn) == 10_200

    started = time.perf_counter()
    result = service.forecast(conn, View(), 12, today=TODAY)
    elapsed = time.perf_counter() - started
    print(f"forecast 12 months, 10,000 bookings + 200 planned: {elapsed * 1000:.0f} ms")
    assert elapsed < 1.0
    assert result.levy_applies and len(result.days) == 365
    expected_end = sum(accounts.balance_at(conn, a, TODAY) for a in (business, giro, savings)) + sum(
        row[1] for row in planned
    )
    assert result.days[-1].balance_cents == expected_end


# --- time window: half-open like the generation ------------------------------------------------


def _item_on_the_window_edge(today: date, interval: Interval, account: int) -> RecurringFields:
    """A recurring item whose occurrences hit ``today`` and the excluded day ``today + 12 months``."""
    if interval == "monthly":
        return RecurringFields("Item", -10_000, account, interval, today, day_of_month=today.day)
    return RecurringFields("Item", -10_000, account, interval, today, weekday=add_months(today, 12).weekday())


@pytest.mark.parametrize("today", [date(2026, 11, 1), date(2026, 11, 15), date(2026, 3, 31)])
@pytest.mark.parametrize("interval", ["monthly", "weekly"])
def test_forecast_window_matches_recurring_generation(
    conn: sqlite3.Connection, today: date, interval: Interval
) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-HAR-9130-007 AK1-AK3 (hand-counted from the window rule
    ``[today, add_months(today, 12))`` of ARCHITECTURE.md "Time windows"): the curve ends on the
    day before ``today + 12 months``; an item falling on that excluded day is neither generated nor
    in the curve; every other occurrence is counted exactly once."""
    account = _account(conn, "Main", 1_000_000)
    excluded = add_months(today, 12)
    fields = _item_on_the_window_edge(today, interval, account)
    item = recurring.create_recurring(conn, fields, today=today)
    result = service.forecast(conn, View(), 12, today=today)

    end = excluded - timedelta(days=1)
    assert result.end == end
    assert result.days[-1].date == end
    assert len(result.days) == (end - today).days + 1
    expected = due_dates(fields.schedule(), today, end)  # inclusive helper, window end passed as last day
    stored = [
        date.fromisoformat(row[0])
        for row in conn.execute('SELECT date FROM "transaction" WHERE recurring_id = ? ORDER BY date', (item.id,))
    ]
    assert stored == expected
    assert excluded not in stored
    assert result.planned_expense_cents == -10_000 * len(expected)
    assert result.days[-1].balance_cents == 1_000_000 - 10_000 * len(expected)
    # today's own day counts once, not twice; a monthly item always starts on it
    assert stored.count(today) == (1 if interval == "monthly" else stored.count(today))


def test_forecast_what_if_on_the_excluded_day_is_outside(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-HAR-9130-007 AK1: ``today + 12 months`` is outside the window."""
    account = _account(conn, "Main", 100_000)
    outside = WhatIfItem(add_months(TODAY, 12), -5_000, account)
    inside = WhatIfItem(add_months(TODAY, 12) - timedelta(days=1), -5_000, account)
    assert service.what_if(conn, [outside], today=TODAY).planned_expense_cents == 0
    assert service.what_if(conn, [inside], today=TODAY).planned_expense_cents == -5_000


# --- API ---------------------------------------------------------------------------------------


def test_forecast_api_roundtrip(client: TestClient) -> None:
    """Oracle: spec-anchored — the /api/forecast contract of this task: views, horizons (400 for
    anything but 3/6/12, 422 for float amounts), what-if without storing, items of a day, emergency
    ``null`` while the reserve holds, a liability view refused with 409."""
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test giro",
            "area": "private",
            "type": "giro",
            "opening_balance_cents": 200_000,
            "opening_date": "2026-01-01",
        },
    ).json()["id"]
    today = date.today()
    later = today + timedelta(days=10)
    assert client.post(
        "/api/recurring/expected",
        json={"date": later.isoformat(), "amount_cents": -50_000, "account_id": account, "text": "Test item"},
    ).status_code in (200, 201)

    curve = client.get("/api/forecast", params={"horizon": 3})
    assert curve.status_code == 200, curve.text
    body = curve.json()
    assert (body["scope"], body["minimum_cents"], body["reserve_missing"]) == ("total", 150_000, True)
    assert client.get("/api/forecast", params={"account_id": account, "horizon": 6}).json()["scope"] == "account"
    assert client.get("/api/forecast", params={"area": "private"}).json()["horizon_months"] == 12
    assert client.get("/api/forecast", params={"horizon": 5}).status_code == 400

    items = client.get("/api/forecast/items", params={"date": later.isoformat()}).json()
    assert [item["amount_cents"] for item in items] == [-50_000]

    what_if = {"horizon": 3, "items": [{"date": later.isoformat(), "amount_cents": -10_000, "account_id": account}]}
    assert client.post("/api/forecast/what-if", json=what_if).json()["minimum_cents"] == 140_000
    assert client.get("/api/forecast", params={"horizon": 3}).json()["minimum_cents"] == 150_000
    floats = {"items": [{"date": later.isoformat(), "amount_cents": 1.5, "account_id": account}]}
    assert client.post("/api/forecast/what-if", json=floats).status_code == 422

    assert client.get("/api/forecast/emergency").json() is None
    loan = client.post(
        "/api/accounts",
        json={
            "name": "Test loan",
            "area": "private",
            "type": "liability",
            "opening_balance_cents": -100_000,
            "opening_date": "2026-01-01",
        },
    ).json()["id"]
    assert client.get("/api/forecast", params={"account_id": loan}).status_code == 409
    assert client.get("/api/forecast", params={"account_id": 9999}).status_code == 404


def test_forecast_endpoints_sync_backpayments_first(client: TestClient) -> None:
    """Oracle: spec-anchored — levy.levy_status ("call sync_backpayments first") + core rule: after an
    income booked since the last sync, the what-if curve must not keep the old back-payment item
    while dropping the year's full gap on its due date — its line equals the synced forecast's."""
    today = date.today()
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test business",
            "area": "business",
            "type": "giro",
            "opening_balance_cents": 0,
            "opening_date": today.replace(month=1, day=1).isoformat(),
        },
    ).json()["id"]
    booking = {"date": today.isoformat(), "account_id": account, "text": "Test client"}
    assert client.post("/api/transactions", json={**booking, "amount_cents": 100_000}).status_code in (200, 201)
    due = f"{today.month:02d}-{min(today.day, 28):02d}"  # due next year within the 12-month horizon
    assert (
        client.put(
            "/api/levy/settings", json={"levy_rate_bp": 3000, "account_id": account, "due_month_day": due}
        ).status_code
        == 200
    )
    assert client.post("/api/transactions", json={**booking, "amount_cents": 200_000}).status_code in (200, 201)

    what_if = client.post("/api/forecast/what-if", json={"horizon": 12, "items": []}).json()
    synced = client.get("/api/forecast", params={"horizon": 12}).json()
    assert what_if["days"][-1] == synced["days"][-1]
    assert synced["days"][-1]["available_cents"] == 300_000 - 90_000
