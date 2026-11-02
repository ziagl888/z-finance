"""The dashboard of UC NF5 / SPEC AK8: the three figures of the weekly routine and the cards.

Every amount is synthetic and hand-computed from the use cases; "today" is 2026-09-19, the month
ends on 2026-09-30. The business giro holds 5,000.00 €, the levy rate is 30 % and the levy pot sits
on that account — the setting of the core rule (`tests/test_free_amount_invariant.py`).
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
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.allocation import service as allocation
from zfinance.domain.allocation.lines import Target
from zfinance.domain.allocation.service import RuleFields
from zfinance.domain.dashboard import service
from zfinance.domain.debts import service as debts
from zfinance.domain.debts.service import DebtFields
from zfinance.domain.levy import service as levy
from zfinance.domain.levy.service import LevyFields
from zfinance.domain.pots import service as pots
from zfinance.domain.pots.service import PotFields
from zfinance.domain.receipts import service as receipts
from zfinance.domain.receipts.service import Extracted
from zfinance.domain.tax import service as tax
from zfinance.domain.tax.service import TaxFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
MONTH_END = date(2026, 9, 30)
INCOME_CENTS = 1_000_000  # 10,000.00 € — the income of the core rule
LEVY_SHARE = 300_000  # 30 % of it


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "dashboard.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def giro(conn: sqlite3.Connection) -> int:
    """The business giro account: 5,000.00 € from 2026-01-01."""
    return accounts.create_account(
        conn, AccountFields("Test business", "business", "giro", 500_000, date(2026, 1, 1))
    ).id


@pytest.fixture
def private_giro(conn: sqlite3.Connection) -> int:
    """The private giro account: 1,000.00 € from 2026-01-01."""
    return accounts.create_account(conn, AccountFields("Test private", "private", "giro", 100_000, date(2026, 1, 1))).id


@pytest.fixture
def levy_pot(conn: sqlite3.Connection, giro: int) -> int:
    """An empty levy pot on the business account with a levy rate of 30 % (UC-ZF-015)."""
    pot_id = pots.create_pot(conn, PotFields("Test levies", giro, is_levy_pot=True), today=TODAY).id
    levy.save_settings(conn, LevyFields(3000), today=TODAY)
    return pot_id


def _dashboard(conn: sqlite3.Connection, *, today: date = TODAY) -> service.Dashboard:
    return service.dashboard(conn, today=today)


def _income(conn: sqlite3.Connection, account_id: int, cents: int = INCOME_CENTS, *, on: date = TODAY) -> int:
    return transactions.create_transaction(
        conn, BookingFields(on, cents, account_id, None, "Test client fee"), today=TODAY
    ).id


def _planned(conn: sqlite3.Connection, account_id: int, day: date, cents: int, text: str) -> int:
    """An expected one-off item: a booking entered long before its date, hence planned."""
    return transactions.create_transaction(
        conn, BookingFields(day, cents, account_id, text=text), today=day - timedelta(days=60)
    ).id


# --- AK1: the one figure (core rule) ------------------------------------------------------------


def test_dashboard_free_until_month_end(conn: sqlite3.Connection, giro: int, levy_pot: int) -> None:
    """Oracle: spec-anchored — UC NF5 / SPEC AK8 + "Kernregel" hand-computed: 5,000 € on the account,
    an income of 10,000 € at 30 % raises "free until the end of the month" by exactly 7,000 € (to
    12,000 €) and the sub line "of which reserved for levies" to 3,000 €."""
    before = _dashboard(conn)
    assert before.month_end == MONTH_END
    assert (before.free.free_cents, before.free.levy_gap_cents) == (500_000, 0)

    _income(conn, giro)
    after = _dashboard(conn)
    assert after.free.free_cents - before.free.free_cents == 700_000  # never 10,000 €
    assert (after.free.free_cents, after.free.levy_gap_cents) == (1_200_000, LEVY_SHARE)
    # The figure is the levy engine's, not one of our own.
    assert after.free.free_cents == levy.free_amount(conn, MONTH_END, today=TODAY)


# --- AK2, AK3: remaining debt with trend, overdue items -----------------------------------------


def test_dashboard_total_debt_with_trend(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC NF5 / SPEC AK8 hand-computed: a debt of 1,000 € opened on
    2026-08-01, a special payment of 200 € on 2026-09-10 → remaining 800 €, end of August 1,000 €,
    trend -200 € (the debt is shrinking)."""
    debt = debts.create_debt(
        conn, DebtFields("Test lender", "business", 100_000, pay_account_id=giro), today=date(2026, 8, 1)
    )
    debts.pay_extra(conn, debt.id, 20_000, on=date(2026, 9, 10), today=TODAY)

    figure = _dashboard(conn).debt
    assert (figure.total_cents, figure.previous_month_cents, figure.trend_cents) == (80_000, 100_000, -20_000)


def test_dashboard_overdue_count(conn: sqlite3.Connection, private_giro: int) -> None:
    """Oracle: spec-anchored — UC NF5 / SPEC AK8 + UC-ZF-004: of three planned items (2026-09-01,
    2026-09-25, 2026-10-10) exactly one is overdue on 2026-09-19."""
    _planned(conn, private_giro, date(2026, 9, 1), -10_000, "Test overdue")
    _planned(conn, private_giro, date(2026, 9, 25), -20_000, "Test upcoming")
    _planned(conn, private_giro, date(2026, 10, 10), -30_000, "Test far away")

    assert _dashboard(conn).overdue_count == 1


# --- AK4: the due card --------------------------------------------------------------------------


def test_dashboard_due_next_14_days(conn: sqlite3.Connection, private_giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK8 ("fällige Posten der nächsten 14 Tage") + UC-ZF-004 main 1:
    the item of 2026-10-10 is outside the window, the overdue one leads the list with 18 days."""
    overdue = _planned(conn, private_giro, date(2026, 9, 1), -10_000, "Test overdue")
    upcoming = _planned(conn, private_giro, date(2026, 9, 25), -20_000, "Test upcoming")
    _planned(conn, private_giro, date(2026, 10, 10), -30_000, "Test far away")

    items = _dashboard(conn).due_items
    assert [item.id for item in items] == [overdue, upcoming]
    assert (items[0].days_overdue, items[0].text) == (18, "Test overdue")
    assert items[1].days_overdue == 0


# --- AK5, AK6: the month and the balances -------------------------------------------------------


def test_dashboard_month_overview(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK8 ("laufender Monat Einnahmen vs. Ausgaben nach Kategorie")
    hand-computed: an income of 2,000 € and an expense of 500 € in September → 2,000 / -500 / 1,500,
    with both category lines."""
    fees = accounts.create_category(conn, CategoryFields("Test fees", "income")).id
    rent = accounts.create_category(conn, CategoryFields("Test rent", "expense")).id
    transactions.create_transaction(conn, BookingFields(TODAY, 200_000, giro, fees, "Test client"), today=TODAY)
    transactions.create_transaction(conn, BookingFields(TODAY, -50_000, giro, rent, "Test office"), today=TODAY)

    month = _dashboard(conn).month
    assert month.month == "2026-09"
    assert (month.income_cents, month.expense_cents, month.balance_cents) == (200_000, -50_000, 150_000)
    assert [(line.category_name, line.amount_cents) for line in month.top_lines] == [
        ("Test fees", 200_000),
        ("Test rent", -50_000),
    ]


def test_dashboard_account_balances(conn: sqlite3.Connection, giro: int, private_giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK8 ("Kontostände privat / business / gesamt") hand-computed:
    1,000 € private + 5,000 € business = 6,000 €; the liability account of a debt is not a balance
    (it is the debt figure)."""
    debts.create_debt(conn, DebtFields("Test lender", "business", 100_000, pay_account_id=giro), today=TODAY)

    balances = _dashboard(conn).balances
    assert (balances.by_area["private"], balances.by_area["business"]) == (100_000, 500_000)
    assert balances.total_cents == 600_000


# --- AK7: the mini curve ------------------------------------------------------------------------


def test_dashboard_mini_curve_is_90_days(conn: sqlite3.Connection, giro: int, private_giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK8 ("Mini-Kurve 90 Tage") hand-computed: 90 days from today,
    starting at the total balance of 6,000 € with no planned item to move it."""
    curve = _dashboard(conn).mini_curve
    assert len(curve) == service.CURVE_DAYS == 90
    assert (curve[0].date, curve[-1].date) == (TODAY, TODAY + timedelta(days=89))
    assert (curve[0].balance_cents, curve[0].available_cents) == (600_000, 600_000)


# --- AK8, AK9: allocation proposals and receipts to check ---------------------------------------


def test_dashboard_allocation_proposals_flag_seven_days(
    conn: sqlite3.Connection, giro: int, levy_pot: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 4b / AK5: a proposal open for seven days or more is flagged
    on the dashboard and still books nothing; a fresh one is listed without the flag. The proposal's
    creation stamp is pinned to TODAY: left on the wall clock it would lie after ``TODAY + 30`` once
    the real date passes 2026-10-19 and ``days_open`` would clamp to 0."""
    monkeypatch.setattr(allocation, "_now", lambda: f"{TODAY.isoformat()}T12:00:00+00:00")
    allocation.create_rule(
        conn,
        RuleFields(
            name="Test business income",
            area="business",
            min_amount_cents=50_000,
            targets=(Target("reserve", percent_bp=2000),),
        ),
    )
    _income(conn, giro, 400_000)

    fresh = _dashboard(conn).proposals
    assert len(fresh) == 1 and fresh[0].overdue is False
    assert (fresh[0].amount_cents, fresh[0].rule_name) == (400_000, "Test business income")

    later = _dashboard(conn, today=TODAY + timedelta(days=30)).proposals
    assert later[0].overdue is True and later[0].days_open >= 7
    # Nothing was booked by looking at it (UC-ZF-014: never an automatic booking). The only rows the
    # read may write are the levy engine's back-payment items, never an allocation booking.
    assert conn.execute("SELECT count(*) FROM \"transaction\" WHERE source = 'allocation'").fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM "transaction" WHERE transfer_group_id IS NOT NULL').fetchone()[0] == 0


def test_dashboard_receipts_review_count(conn: sqlite3.Connection, tmp_path: Path, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-013 step 7 ("Prüfposten stehen in einer Liste Belege prüfen"):
    a receipt below the confidence threshold raises the counter, a booked one does not."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    unsure = Extracted(date=TODAY, amount_cents=1_234, merchant="Test shop", confidence=0.4)
    assert receipts.submit_receipt(conn, data_dir, b"\xff\xd8\xff\xe0unsure\xff\xd9", unsure).status == "review"

    assert _dashboard(conn).receipts_review_count == 1


# --- AK10, AK11: the two hints ------------------------------------------------------------------


def test_dashboard_hint_levies_not_covered(conn: sqlite3.Connection, giro: int, levy_pot: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 2b hand-computed: an income of 10,000 € at 30 % leaves a gap
    of 3,000 €, covered by the 15,000 € on the account; after spending 14,000 € only 1,000 € are left
    and the dashboard warns "levies not covered"."""
    _income(conn, giro)
    assert _dashboard(conn).hints.levies_not_covered is False

    transactions.create_transaction(conn, BookingFields(TODAY, -1_400_000, giro, None, "Test equipment"), today=TODAY)
    assert _dashboard(conn).hints.levies_not_covered is True


def test_dashboard_hint_tax_plan_missing(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-008 1a: while no amounts are entered the dashboard shows
    "taxes and levies not set"; entering the quarterly prepayment clears the hint."""
    assert _dashboard(conn).hints.tax_plan_missing is True

    tax.save_settings(conn, TaxFields(business_account_id=giro, est_vz_quarter_cents=50_000), today=TODAY)
    assert _dashboard(conn).hints.tax_plan_missing is False


# --- AK12: the endpoint -------------------------------------------------------------------------


def test_dashboard_api_roundtrip(client: TestClient) -> None:
    """Oracle: spec-anchored — task contract "ein Aggregat-Endpunkt /api/dashboard": one GET without
    parameters answers with every block of the start page, and the booked income of 1,000 € at 30 %
    is in the free amount net of its 300 € levy share."""
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test business",
            "area": "business",
            "type": "giro",
            "opening_balance_cents": 0,
            "opening_date": "2026-01-01",
        },
    ).json()
    client.post("/api/pots", json={"name": "Test levies", "account_id": account["id"], "is_levy_pot": True})
    client.put("/api/levy/settings", json={"levy_rate_bp": 3000})
    client.post(
        "/api/transactions",
        json={
            "date": date.today().isoformat(),
            "amount_cents": 100_000,
            "account_id": account["id"],
            "text": "Test client fee",
        },
    )

    response = client.get("/api/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "today",
        "month_end",
        "free",
        "debt",
        "overdue_count",
        "due_items",
        "balances",
        "month",
        "mini_curve",
        "proposals",
        "receipts_review_count",
        "hints",
    }
    assert (body["free"]["levy_gap_cents"], body["free"]["free_cents"]) == (30_000, 70_000)
    assert body["balances"]["business_cents"] == 100_000
    assert body["hints"]["tax_plan_missing"] is True


def test_dashboard_empty_database(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC AK8 with an empty ledger: the start page of a fresh installation
    answers with zeros instead of an error, so the first start shows a screen, not a stack trace."""
    body = client.get("/api/dashboard").json()

    assert (body["free"]["free_cents"], body["free"]["levy_gap_cents"]) == (0, 0)
    assert (body["debt"]["total_cents"], body["debt"]["trend_cents"]) == (0, 0)
    assert (body["overdue_count"], body["due_items"], body["proposals"]) == (0, [], [])
    assert body["balances"]["total_cents"] == 0
    assert body["month"]["top_lines"] == []
    assert body["receipts_review_count"] == 0
