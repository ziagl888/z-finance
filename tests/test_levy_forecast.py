"""UC-ZF-015 levy reserve: target/actual/gap, back-payment, required revenue, charts, API.

Expected values are hand-computed from the use case; every value is synthetic. "today" is
2026-09-19, so the back-payment of 2026 falls due on 2027-07-01.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields
from zfinance.domain.debts import service as debts
from zfinance.domain.debts.service import DebtFields
from zfinance.domain.levy import service
from zfinance.domain.levy.service import LevyFields, RevenueBlocks, YearFigures
from zfinance.domain.pots import service as pots
from zfinance.domain.pots.service import PotFields
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.service import RecurringFields
from zfinance.domain.tax import service as tax
from zfinance.domain.tax.service import TaxFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
BACKPAYMENT_2026 = "Levy back-payment 2026"

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "levy.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def business(conn: sqlite3.Connection) -> int:
    """The business account: 50,000.00 € from 2026-01-01."""
    return accounts.create_account(
        conn, AccountFields("Test business", "business", "giro", 5_000_000, date(2026, 1, 1))
    ).id


@pytest.fixture
def levy_pot(conn: sqlite3.Connection, business: int) -> int:
    """An empty levy pot on the business account."""
    return pots.create_pot(conn, PotFields("Levies", business, is_levy_pot=True), today=TODAY).id


def _income(conn: sqlite3.Connection, account: int, cents: int, on: date = TODAY) -> int:
    """A booked business income of ``cents`` on ``on`` (booked as of that day)."""
    return transactions.create_transaction(
        conn, BookingFields(on, cents, account, None, "Test client"), today=max(on, TODAY)
    ).id


def _prepayments(conn: sqlite3.Connection, account: int, cents: int) -> None:
    """``cents`` of booked income-tax prepayment in 2026: one plan due date, its row confirmed."""
    tax.save_settings(conn, TaxFields(account, cents, None, est_due_dates=("03-15",)), today=date(2026, 1, 1))
    conn.execute(
        "UPDATE \"transaction\" SET status = 'booked' WHERE text = 'Income tax prepayment' AND date = '2026-03-15'"
    )
    assert tax.prepayments_ytd(conn, 2026) == cents


def _settings(conn: sqlite3.Connection, rate_bp: int, **overrides: object) -> None:
    service.save_settings(conn, LevyFields(rate_bp, **overrides), today=TODAY)  # type: ignore[arg-type]


def _year(conn: sqlite3.Connection, year: int = 2026) -> service.YearStatus:
    return next(y for y in service.levy_status(conn, today=TODAY).years if y.year == year)


def _backpayments(conn: sqlite3.Connection) -> list[tuple[str, int, str]]:
    """(date, signed amount, status) of every back-payment row ever written."""
    rows = conn.execute(
        'SELECT date, amount_cents, status FROM "transaction" WHERE text LIKE ? ORDER BY id', ("Levy back-payment%",)
    )
    return [(row[0], row[1], row[2]) for row in rows]


def _monthly_expense(conn: sqlite3.Connection, account: int, cents: int, text: str = "Test rent") -> None:
    recurring.create_recurring(
        conn, RecurringFields(text, -cents, account, "monthly", date(2026, 1, 1), day_of_month=1), today=TODAY
    )


# --- pure rules --------------------------------------------------------------------------------


def test_levy_pure_rules() -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK1/AK3 hand-computed: 35 % of 20,000 € = 7,000 €;
    3,000 € ÷ 0.65 = 4,615.38 €; pot attributed oldest year first, the newest takes the rest."""
    assert service.reserve_target(3500, 2_000_000) == 700_000
    assert service.reserve_target(3500, -50_000) == 0  # a loss reserves nothing
    assert service.reserve_target(3333, 1) == 0 and service.reserve_target(5000, 1) == 1  # half up

    required = service.required_revenue(
        RevenueBlocks(Fraction(200_000), Fraction(50_000), Fraction(30_000), Fraction(20_000)), 3500
    )
    assert required.required_cents == 461_538  # 4,615 € (AK3)
    assert (required.fixed_costs_cents, required.installments_cents, required.payoff_budget_cents) == (
        200_000,
        50_000,
        30_000,
    )
    assert required.levy_cents == 461_538 - 300_000

    one = service.year_gaps([YearFigures(2026, 700_000, 300_000)], 500_000)
    assert (one[0].actual_cents, one[0].gap_cents) == (800_000, 0)
    two = service.year_gaps([YearFigures(2026, 300_000, 0), YearFigures(2025, 400_000, 100_000)], 400_000)
    assert [(y.year, y.pot_share_cents, y.gap_cents) for y in two] == [(2025, 300_000, 0), (2026, 100_000, 200_000)]
    assert service.monthly_equivalent("quarterly", 30_000) == 10_000
    assert service.due_date("07-01", 2026) == date(2027, 7, 1)


# --- AK1 / 2a: target, actual, gap -------------------------------------------------------------


def test_levy_gap(conn: sqlite3.Connection, business: int, levy_pot: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK1: 35 %, 20,000 € income, pot 4,000 €, prepayments
    3,000 € → target 7,000, actual 7,000, gap 0; pot 1,000 € → gap 3,000 €. 2a: no pot → actual
    = prepayments only (3,000 €), gap 4,000 €."""
    _settings(conn, 3500)
    _income(conn, business, 2_000_000, date(2026, 3, 1))
    _prepayments(conn, business, 300_000)
    pots.deposit(conn, levy_pot, 400_000, today=TODAY)

    year = _year(conn)
    assert (year.base_cents, year.target_cents, year.actual_cents, year.gap_cents) == (2_000_000, 700_000, 700_000, 0)
    assert [y.year for y in service.levy_status(conn, today=TODAY).years] == [2026]

    pots.withdraw(conn, levy_pot, 300_000, today=TODAY)
    status = service.levy_status(conn, today=TODAY)
    assert (status.years[0].actual_cents, status.levy_gap_cents, status.levy_pot_missing) == (400_000, 300_000, False)

    pots.close_pot(conn, levy_pot, today=TODAY)
    status = service.levy_status(conn, today=TODAY)
    assert (status.levy_pot_missing, status.years[0].actual_cents, status.levy_gap_cents) == (True, 300_000, 400_000)


def test_levy_profit_base_uses_business_expenses(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 assumption "rate on profit": (20,000 - 5,000) x 35 % = 5,250 €."""
    _settings(conn, 3500, levy_base="profit")
    _income(conn, business, 2_000_000, date(2026, 3, 1))
    transactions.create_transaction(
        conn, BookingFields(date(2026, 4, 1), -500_000, business, None, "Test"), today=TODAY
    )
    assert _year(conn).target_cents == 525_000


# --- AK2: back-payment -------------------------------------------------------------------------


def test_levy_backpayment_planned(conn: sqlite3.Connection, business: int, levy_pot: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK2: target 7,000 €, prepayments 3,000 € → one planned
    -4,000 € "Levy back-payment" on 01.07. of the following year; idempotent, kept in line,
    removed at ≤ 0, never recreated after it was confirmed."""
    _income(conn, business, 2_000_000, date(2026, 3, 1))
    _prepayments(conn, business, 300_000)
    _settings(conn, 3500)

    assert _backpayments(conn) == [("2027-07-01", -400_000, "planned")]
    service.sync_backpayments(conn, today=TODAY)
    service.sync_backpayments(conn, today=TODAY)
    assert _backpayments(conn) == [("2027-07-01", -400_000, "planned")]  # no second item per year
    assert _year(conn).backpayment_cents == 400_000

    _income(conn, business, 1_000_000)  # target 10,500 € → back-payment 7,500 €
    service.sync_backpayments(conn, today=TODAY)
    assert _backpayments(conn) == [("2027-07-01", -750_000, "planned")]

    _settings(conn, 1000)  # 10 % of 30,000 € = 3,000 € = prepayments → nothing to pay
    assert _backpayments(conn) == []
    _settings(conn, 3500)
    assert _backpayments(conn) == [("2027-07-01", -750_000, "planned")]

    # Confirmed (UC-ZF-004 swaps the planned row for a booking): the year is done for good.
    planned_id = _year(conn).backpayment_transaction_id
    assert planned_id is not None
    transactions.delete_transaction(conn, planned_id)
    service.sync_backpayments(conn, today=TODAY)
    assert _backpayments(conn) == []
    assert _year(conn).backpayment_state == "settled"


def test_levy_backpayment_archive(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 3a: the assessment replaces the estimate, which is kept."""
    _income(conn, business, 2_000_000, date(2026, 3, 1))
    _settings(conn, 3500, account_id=business)
    service.archive_backpayment(conn, 2026)
    assert _backpayments(conn) == []
    row = conn.execute("SELECT estimate_cents, archived_at FROM levy_backpayment WHERE year = 2026").fetchone()
    assert row[0] == 700_000 and row[1] is not None
    with pytest.raises(service.Conflict):
        service.archive_backpayment(conn, 2026)
    with pytest.raises(service.NotFound):
        service.archive_backpayment(conn, 2020)


# --- AK3: required revenue ---------------------------------------------------------------------


def test_required_revenue(conn: sqlite3.Connection, business: int, levy_pot: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK3: fixed costs 2,000 €, instalments 500 €, payoff budget
    300 €, pot rates 200 €, 35 % → 3,000 € ÷ 0.65 = 4,615 € per month. Tax-plan items and the levy
    pot's savings plan are not costs (the levy is what the division covers)."""
    _settings(conn, 3500)
    _monthly_expense(conn, business, 150_000)
    recurring.create_recurring(
        conn,
        RecurringFields("Test insurance", -150_000, business, "quarterly", date(2026, 1, 1), day_of_month=1),
        today=TODAY,
    )  # 500 € a month
    debts.create_debt(
        conn,
        DebtFields("Test lender", "business", 1_000_000, 50_000, "monthly", date(2026, 10, 1), business),
        today=TODAY,
    )
    debts.set_payoff_budget(conn, 30_000)
    savings = accounts.create_account(conn, AccountFields("Test savings", "private", "savings", 0, date(2026, 1, 1))).id
    holiday = pots.create_pot(conn, PotFields("Holiday", savings), today=TODAY)
    pots.set_savings_plan(conn, holiday.id, business, amount_cents=20_000, today=TODAY)
    pots.set_savings_plan(conn, levy_pot, savings, amount_cents=99_900, today=TODAY)  # excluded
    tax.save_settings(conn, TaxFields(business, 300_000, None), today=TODAY)  # excluded

    required = service.required_revenue_monthly(conn, today=TODAY)
    assert (
        required.fixed_costs_cents,
        required.installments_cents,
        required.payoff_budget_cents,
        required.pot_rates_cents,
        required.reserve_build_cents,
    ) == (200_000, 50_000, 30_000, 20_000, 0)
    assert required.required_cents == 461_538


# --- AK4: income chart -------------------------------------------------------------------------


def test_income_chart_marks_months_below_need(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK4: need 4,615 € (3,000 € costs at 35 %); a month with
    3,000 € income is marked, a month with 5,000 € is not; 24 months, 12 back and 12 ahead."""
    _settings(conn, 3500)
    _monthly_expense(conn, business, 300_000)
    _income(conn, business, 300_000, date(2026, 7, 10))
    _income(conn, business, 500_000, date(2026, 8, 10))
    recurring.create_expected(
        conn, BookingFields(date(2026, 12, 1), 600_000, business, None, "Test client"), today=TODAY
    )

    chart = {month.month: month for month in service.income_chart(conn, today=TODAY)}
    assert next(iter(chart)) == "2025-10" and list(chart)[-1] == "2027-09" and len(chart) == 24
    assert (chart["2026-07"].actual_cents, chart["2026-07"].required_cents, chart["2026-07"].below_required) == (
        300_000,
        461_538,
        True,
    )
    assert chart["2026-08"].below_required is False
    assert (chart["2026-12"].planned_cents, chart["2026-12"].below_required) == (600_000, False)


# --- AK5: rate change --------------------------------------------------------------------------


def test_levy_rate_change_recomputes(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK5 hand-computed: 20,000 € income, 3,000 € costs;
    35 % → target 7,000, back-payment 7,000, need 4,615.38 €; 40 % → 8,000, 8,000, 5,000 €."""
    _income(conn, business, 2_000_000, date(2026, 3, 1))
    _monthly_expense(conn, business, 300_000)
    _settings(conn, 3500, account_id=business)
    assert (_year(conn).target_cents, _backpayments(conn)) == (700_000, [("2027-07-01", -700_000, "planned")])
    assert service.required_revenue_monthly(conn, today=TODAY).required_cents == 461_538

    _settings(conn, 4000, account_id=business)
    assert (_year(conn).target_cents, _backpayments(conn)) == (800_000, [("2027-07-01", -800_000, "planned")])
    assert service.required_revenue_monthly(conn, today=TODAY).required_cents == 500_000


# --- AK7 / 2b ----------------------------------------------------------------------------------


def test_levy_income_raises_target_and_gap_ak7(conn: sqlite3.Connection, business: int, levy_pot: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK7: 10,000 € income at 30 % → target +3,000 €, gap
    +3,000 € right after the booking; the deposit into the levy pot closes the gap."""
    _settings(conn, 3000)
    before = service.levy_status(conn, today=TODAY)
    _income(conn, business, 1_000_000)
    after = service.levy_status(conn, today=TODAY)
    assert after.years[-1].target_cents - before.years[-1].target_cents == 300_000
    assert after.levy_gap_cents - before.levy_gap_cents == 300_000

    pots.deposit(conn, levy_pot, 300_000, today=TODAY)
    assert service.levy_status(conn, today=TODAY).levy_gap_cents == 0


def test_levy_uncovered_flag(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-015 2b: gap 3,000 € (30 % of 10,000 €) against 1,000 € free
    on all accounts → "levies not covered"; with enough free money → no warning."""
    small = accounts.create_account(conn, AccountFields("Test small", "business", "giro", 0, date(2026, 1, 1))).id
    _settings(conn, 3000)
    _income(conn, small, 1_000_000, date(2026, 2, 1))
    transactions.create_transaction(conn, BookingFields(date(2026, 2, 2), -900_000, small, None, "Test"), today=TODAY)
    status = service.levy_status(conn, today=TODAY)
    assert (status.levy_gap_cents, status.free_balance_cents, status.levy_uncovered) == (300_000, 100_000, True)

    _income(conn, small, 500_000, date(2026, 2, 3))  # gap 4,500 €, free 6,000 €
    assert service.levy_status(conn, today=TODAY).levy_uncovered is False


def test_previous_year_stays_open_until_its_backpayment(conn: sqlite3.Connection, business: int, levy_pot: int) -> None:
    """Oracle: spec-anchored — core rule across the year change, hand-computed: 10,000 € in 2026 at
    30 % stay reserved in January 2027 (the 2026 back-payment is due 2027-07-01) next to 2027."""
    _settings(conn, 3000)
    _income(conn, business, 1_000_000, date(2026, 5, 1))
    january = date(2027, 1, 15)
    _income(conn, business, 200_000, january)
    service.sync_backpayments(conn, today=january)
    years = service.levy_status(conn, today=january).years
    assert [(y.year, y.target_cents, y.gap_cents) for y in years] == [(2026, 300_000, 300_000), (2027, 60_000, 60_000)]
    assert service.levy_gap(conn, january) == 360_000


# --- AK6 (data): expense chart -----------------------------------------------------------------


def test_expense_chart(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-015 step 5 hand-computed: an 800 € expense in 2026-08; a debt
    of 1,000 € at 500 €/month from 2026-10-01 → liabilities 500 € in Oct and Nov, remaining debt
    1,000 € at the end of Sep, 500 € in Oct, 0 € in Nov."""
    transactions.create_transaction(conn, BookingFields(date(2026, 8, 5), -80_000, business, None, "Test"), today=TODAY)
    debts.create_debt(
        conn,
        DebtFields("Test lender", "business", 100_000, 50_000, "monthly", date(2026, 10, 1), business),
        today=TODAY,
    )
    chart = {month.month: month for month in service.expense_chart(conn, "business", today=TODAY)}
    assert len(chart) == 24
    assert [(c.category_id, c.amount_cents) for c in chart["2026-08"].categories] == [(None, 80_000)]
    assert chart["2026-08"].planned is False and chart["2026-10"].planned is True
    assert [chart[m].liabilities_cents for m in ("2026-09", "2026-10", "2026-11")] == [0, 50_000, 50_000]
    assert [chart[m].remaining_debt_cents for m in ("2026-09", "2026-10", "2026-11")] == [100_000, 50_000, 0]


# --- API ---------------------------------------------------------------------------------------


def test_levy_api_roundtrip(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK1/AK2 over HTTP: 35 % of 20,000 € = 7,000 € target and
    back-payment; a float rate is refused (422), a rate above 90 % too (400); charts have 24 months."""
    today = date.today()
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test business",
            "area": "business",
            "type": "giro",
            "opening_balance_cents": 0,
            "opening_date": (today - timedelta(days=30)).isoformat(),
        },
    ).json()["id"]
    assert client.get("/api/levy/settings").json()["settings_missing"] is True
    assert client.post(
        "/api/transactions",
        json={"date": today.isoformat(), "amount_cents": 2_000_000, "account_id": account, "text": "Test client"},
    ).status_code in (200, 201)

    assert client.put("/api/levy/settings", json={"levy_rate_bp": 35.5}).status_code == 422
    assert client.put("/api/levy/settings", json={"levy_rate_bp": 9500}).status_code == 400
    assert client.put("/api/levy/settings", json={"levy_rate_bp": 3500, "due_month_day": "13-01"}).status_code == 400
    saved = client.put("/api/levy/settings", json={"levy_rate_bp": 3500, "account_id": account})
    assert saved.status_code == 200 and saved.json()["settings_missing"] is False

    status = client.get("/api/levy/status").json()
    current = status["years"][-1]
    assert (current["target_cents"], current["gap_cents"], current["backpayment_cents"]) == (700_000, 700_000, 700_000)
    assert status["levy_pot_missing"] is True

    free = client.get("/api/levy/free-amount").json()
    assert free["levy_gap_cents"] == 700_000
    assert free["free_cents"] == free["accounts_cents"] + free["planned_cents"] - free["reserve_cents"] - 700_000
    assert client.get("/api/levy/free-amount", params={"as_of": "2000-01-01"}).status_code == 400
    assert client.get("/api/levy/required-revenue").json()["levy_rate_bp"] == 3500
    assert len(client.get("/api/levy/charts/income").json()) == 24
    assert len(client.get("/api/levy/charts/expenses", params={"area": "business"}).json()) == 24
    assert client.post("/api/levy/backpayments/1999/archive").status_code == 404
    assert client.post(f"/api/levy/backpayments/{today.year}/archive").status_code == 204
