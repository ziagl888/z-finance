"""UC-ZF-012 payoff plan: order, month-by-month plan, moving special payments, progress, what-if.

Expected values are hand-computed from the use case and the calendar; every value is synthetic.
"today" is 2026-09-19, so the plan starts in October 2026 (the month after today) and the average
income is taken over June, July and August 2026.
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
from zfinance.domain.accounts.service import AccountFields
from zfinance.domain.debts import service as debts
from zfinance.domain.debts.service import DebtFields
from zfinance.domain.due import service as due
from zfinance.domain.levy import service as levy
from zfinance.domain.levy.service import LevyFields
from zfinance.domain.payoff import service
from zfinance.domain.payoff.plan import PlanDebt, build_plan, progress
from zfinance.domain.payoff.service import PayoffError, PlanFields
from zfinance.domain.pots import service as pots
from zfinance.domain.pots.service import PotFields
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.service import RecurringFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
INCOME_MONTHS = (date(2026, 6, 10), date(2026, 7, 10), date(2026, 8, 10))

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "payoff.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def giro(conn: sqlite3.Connection) -> int:
    """The private paying account with 1,000.00 € from 2026-01-01."""
    return accounts.create_account(conn, AccountFields("Test giro", "private", "giro", 100_000, date(2026, 1, 1))).id


@pytest.fixture
def business(conn: sqlite3.Connection) -> int:
    """The business account, empty, from 2026-01-01."""
    return accounts.create_account(conn, AccountFields("Test business", "business", "giro", 0, date(2026, 1, 1))).id


def _income(conn: sqlite3.Connection, account: int, cents: int, days: tuple[date, ...] = INCOME_MONTHS) -> None:
    for day in days:
        transactions.create_transaction(conn, BookingFields(day, cents, account, None, "Test client"), today=TODAY)


def _debt(conn: sqlite3.Connection, giro: int, name: str, total: int, rate: int, interest: str | None = None) -> int:
    """A monthly debt paid from ``giro``, first instalment 2026-10-01, nothing repaid yet."""
    fields = DebtFields(name, "private", total, rate, "monthly", date(2026, 10, 1), giro, interest)
    return debts.create_debt(conn, fields, today=TODAY).id


@pytest.fixture
def ak1(conn: sqlite3.Connection, giro: int) -> tuple[int, int]:
    """UC AK1: A 2,000 € at 100 €, B 4,000 € at 200 €; 3,000 € income a month, so there is room."""
    _income(conn, giro, 300_000)
    return _debt(conn, giro, "Test creditor A", 200_000, 10_000, "3"), _debt(
        conn, giro, "Test creditor B", 400_000, 20_000, "9"
    )


def _save(conn: sqlite3.Connection, strategy: str = "snowball", budget: int = 30_000, **kw: object) -> service.Status:
    return service.save_plan(conn, PlanFields(strategy, budget, **kw), today=TODAY)  # type: ignore[arg-type]


def _special_payments(conn: sqlite3.Connection) -> list[tuple[str, str, int, str]]:
    """(month, creditor, amount, status) of the plan's special payments, outgoing leg, by month."""
    rows = conn.execute(
        "SELECT pt.month, d.creditor, -t.amount_cents, t.status FROM payoff_transfer pt"
        ' JOIN debt d ON d.id = pt.debt_id JOIN "transaction" t'
        " ON t.transfer_group_id = pt.transfer_group_id AND t.amount_cents < 0"
        " ORDER BY pt.month, d.creditor"
    )
    return [tuple(row) for row in rows]  # type: ignore[misc]


# --- AK1 ---------------------------------------------------------------------------------------


def test_payoff_snowball_dates() -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK1 by hand: A 2,000/100, B 4,000/200, budget 600 € = 300 €
    instalments + 300 € extra. Months 1-5: A gets 100 + 300 → 0 after month 5; B gets 200 → 3,000.
    Months 6-10: B gets 200 + 300 + A's freed 100 = 600 → 0 after month 10."""
    plan = build_plan([PlanDebt(1, 200_000, 10_000), PlanDebt(2, 400_000, 20_000)], 30_000, "snowball")

    assert plan.order == (1, 2)
    assert plan.ends == {1: 4, 2: 9}  # plan months 0-based: A in month 5, debt-free in month 10
    assert plan.end == 9
    assert [(m.installments_cents, m.extra_cents) for m in plan.months] == [(30_000, 30_000)] * 5 + [
        (20_000, 40_000)
    ] * 5
    assert [(p.debt_id, p.installment_cents, p.extra_cents) for p in plan.months[0].payments] == [
        (1, 10_000, 30_000),
        (2, 20_000, 0),
    ]
    assert plan.months[4].remaining_cents == 300_000
    assert plan.months[-1].remaining_cents == 0


def test_payoff_plan_books_moving_special_payments(conn: sqlite3.Connection, ak1: tuple[int, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK1 + step 4 on the calendar: plan months October 2026 to
    July 2027; 300 € special payment on A from October to February, then 400 € (300 € + A's freed
    100 €) on B from March to July; debt-free in July 2027. A second sync changes nothing."""
    result = _save(conn)

    assert result.has_plan and result.saved
    assert result.total_monthly_cents == 60_000
    assert [(d.creditor, d.end_month) for d in result.debts] == [
        ("Test creditor A", "2027-02"),
        ("Test creditor B", "2027-07"),
    ]
    assert result.debt_free_month == result.baseline_end_month == "2027-07"
    a = [(m, "Test creditor A", 30_000, "planned") for m in ("2026-10", "2026-11", "2026-12", "2027-01", "2027-02")]
    b = [(m, "Test creditor B", 40_000, "planned") for m in ("2027-03", "2027-04", "2027-05", "2027-06", "2027-07")]
    assert _special_payments(conn) == a + b

    groups = conn.execute("SELECT transfer_group_id FROM payoff_transfer ORDER BY 1").fetchall()
    service.status(conn, today=TODAY)
    assert conn.execute("SELECT transfer_group_id FROM payoff_transfer ORDER BY 1").fetchall() == groups


def test_skipped_special_payment_of_plan_month_is_not_recreated(conn: sqlite3.Connection, ak1: tuple[int, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-004 skip + UC-ZF-012 step 4: October's special payment is in the
    due list on 2026-09-19 (today + 14 days); skipping it leaves one skipped October row for A and no
    new planned one — the next sync must not undo the user's skip."""
    _save(conn)
    october = conn.execute(
        'SELECT t.id FROM payoff_transfer pt JOIN "transaction" t ON t.transfer_group_id = pt.transfer_group_id'
        " AND t.amount_cents < 0 WHERE pt.month = '2026-10'"
    ).fetchone()[0]
    due.skip(conn, october, today=TODAY)

    service.status(conn, today=TODAY)

    assert [row for row in _special_payments(conn) if row[0] == "2026-10"] == [
        ("2026-10", "Test creditor A", 30_000, "skipped")
    ]


# --- AK2 ---------------------------------------------------------------------------------------


def test_strategy_change_changes_order_not_debts(conn: sqlite3.Connection, ak1: tuple[int, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK2, hand-computed for avalanche (B at 9 % before A at 3 %):
    B gets 200 + 300 = 500 € → 0 after 8 months (May 2027); A pays 100 € until then (1,200 € left),
    then 100 + 300 + 200 € → June 600 €, July 0. Debts and recurring items stay untouched."""
    _save(conn, "snowball")
    debts_before = conn.execute("SELECT * FROM debt ORDER BY id").fetchall()
    items_before = conn.execute("SELECT * FROM recurring ORDER BY id").fetchall()

    result = _save(conn, "avalanche")

    assert [(d.creditor, d.end_month) for d in result.debts] == [
        ("Test creditor B", "2027-05"),
        ("Test creditor A", "2027-07"),
    ]
    assert conn.execute("SELECT * FROM debt ORDER BY id").fetchall() == debts_before
    assert conn.execute("SELECT * FROM recurring ORDER BY id").fetchall() == items_before
    assert _special_payments(conn)[0] == ("2026-10", "Test creditor B", 30_000, "planned")


# --- AK3 ---------------------------------------------------------------------------------------


def test_payoff_negative_available_no_plan(conn: sqlite3.Connection, giro: int, ak1: tuple[int, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK3 / 2a by hand: income 3,000 €, fixed costs 3,500 €,
    instalments 300 € → available -800 € a month: no plan, no special payment, shortfall 800 €.
    A plan saved before loses its planned special payments."""
    _save(conn)
    recurring.create_recurring(
        conn, RecurringFields("Test rent", -350_000, giro, "monthly", date(2026, 10, 3), day_of_month=3), today=TODAY
    )

    result = _save(conn)

    assert result.availability.available_cents == -80_000
    assert (result.has_plan, result.shortfall_cents) == (False, 80_000)
    assert (result.months, result.debt_free_month, result.baseline_end_month) == ([], None, None)
    assert _special_payments(conn) == []
    assert all(d.end_month is None for d in result.debts)


# --- AK4 ---------------------------------------------------------------------------------------


def test_payoff_missed_month_shifts_date(conn: sqlite3.Connection, ak1: tuple[int, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK4 / 6a by hand: the October instalments (300 €) are
    confirmed, the October special payment (300 €) is skipped. On 2026-11-02: October planned 600 €,
    booked 300 € → behind, catch-up 300 €. From December: A 1,500 € (after November's 400 €), B 3,600 €
    → A done in March with 100 € left over for B, B done in August: debt-free 2027-08 instead of
    2027-07, one month later."""
    _save(conn)
    november = date(2026, 11, 2)
    for item in due.list_due(conn, today=november):
        if item.date.month != 10:
            continue
        if "Special payment" in item.text:
            transactions.skip_planned(conn, item.id)
        else:
            due.confirm(conn, item.id, today=november)

    result = service.status(conn, today=november)

    assert [(r.month, r.planned_cents, r.actual_cents, r.completed) for r in result.progress] == [
        ("2026-10", 60_000, 30_000, True),
        ("2026-11", 60_000, 0, False),
    ]
    assert (result.behind, result.catch_up_cents) == (True, 30_000)
    assert (result.debt_free_month, result.baseline_end_month, result.shift_months) == ("2027-08", "2027-07", 1)
    assert conn.execute("SELECT actual_cents FROM payoff_month WHERE month = '2026-10'").fetchone()[0] == 30_000


# --- AK5 ---------------------------------------------------------------------------------------


def test_what_if_changes_date_and_stores_nothing(conn: sqlite3.Connection, ak1: tuple[int, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK5 by hand: +100 € → 400 € extra: A 500 € a month → 0 after
    4 months (January), B 3,200 € then 700 € a month → 400 € after April, 0 in June 2027 (was July).
    The database is byte-for-byte the same afterwards."""
    _save(conn)
    before = list(conn.iterdump())

    result = service.what_if(conn, 10_000, today=TODAY)

    assert (result.budget_cents, result.total_monthly_cents) == (40_000, 70_000)
    assert result.debt_free_month == "2027-06"
    assert result.ends == {ak1[0]: "2027-01", ak1[1]: "2027-06"}
    assert list(conn.iterdump()) == before
    assert service.status(conn, today=TODAY).debt_free_month == "2027-07"


# --- available amount (core rule) --------------------------------------------------------------


def test_available_amount_is_net_of_levy(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — SPEC "Kernregel" + orchestrator note: at 30 % a business income of
    3,000 € booked in August raises the 3-month average by 1,000 € and the available amount by only
    700 € (= (1 - 0.30) * 1,000 €)."""
    levy.save_settings(conn, LevyFields(3000), today=TODAY)
    before = service.availability(conn, today=TODAY).available_cents

    _income(conn, business, 300_000, (date(2026, 8, 20),))
    after = service.availability(conn, today=TODAY)

    assert after.available_cents - before == 70_000
    assert (after.income_cents, after.levy_cents, after.levy_rate_bp) == (100_000, 30_000, 3000)


def test_available_amount_terms(conn: sqlite3.Connection, giro: int, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-012 step 2, hand-computed: private income 2,400 € in June, July
    and August, business income 600 € in August → Ø 2,600 €; levy 30 % of 600 € = 180 € ÷ 3 = 60 €;
    rent 500 €, instalment 100 €, savings plan 200 € → 2,600 - 500 - 100 - 200 - 60 = 1,740 €.
    Override 3,000 €: levy share scaled 180 * 3,000 ÷ 7,800 = 69.23 € → 3,000 - 800 - 69.23 = 2,130.77 €."""
    levy.save_settings(conn, LevyFields(3000), today=TODAY)
    _income(conn, giro, 240_000)
    _income(conn, business, 60_000, (date(2026, 8, 12),))
    recurring.create_recurring(
        conn, RecurringFields("Test rent", -50_000, giro, "monthly", date(2026, 10, 3), day_of_month=3), today=TODAY
    )
    _debt(conn, giro, "Test creditor A", 200_000, 10_000)
    savings = accounts.create_account(conn, AccountFields("Test savings", "private", "savings", 0, date(2026, 1, 1)))
    pot = pots.create_pot(conn, PotFields("Test holiday", savings.id), today=TODAY)
    pots.set_savings_plan(conn, pot.id, giro, amount_cents=20_000, start_date=date(2026, 10, 5), today=TODAY)

    terms = service.availability(conn, today=TODAY)

    assert terms.income_months == ("2026-06", "2026-07", "2026-08")
    assert (terms.income_cents, terms.fixed_costs_cents, terms.installments_cents) == (260_000, 50_000, 10_000)
    assert (terms.pot_rates_cents, terms.levy_cents, terms.available_cents) == (20_000, 6_000, 174_000)
    overridden = service.availability(conn, today=TODAY, override=300_000)
    assert (overridden.income_overridden, overridden.levy_cents, overridden.available_cents) == (True, 6_923, 213_077)


def test_over_budget_warning_and_raise_suggestion(conn: sqlite3.Connection, giro: int, ak1: tuple[int, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 3a and 6b: available 2,700 € (3,000 € - 300 €); a budget of
    3,000 € warns but still plans. Another 1,500 € booked in August raises the average by 500 € →
    500 € more budget suggested (3,500 €), the stored budget stays 3,000 €."""
    result = _save(conn, budget=300_000)
    assert (result.availability.available_cents, result.over_budget, result.has_plan) == (270_000, True, True)
    assert result.suggested_budget_cents is None

    _income(conn, giro, 150_000, (date(2026, 8, 25),))
    later = service.status(conn, today=TODAY)

    assert (later.suggested_budget_cents, later.budget_cents) == (350_000, 300_000)
    assert debts.payoff_budget(conn) == 300_000


def test_manual_order_and_validation(conn: sqlite3.Connection, ak1: tuple[int, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 4a: the user's order (B before A) is used as given; an
    unknown or repeated debt in the order and a negative budget are refused (dev)."""
    a, b = ak1

    result = _save(conn, "manual", manual_order=(b, a))

    assert [d.id for d in result.debts] == [b, a]
    assert result.manual_order == (b, a)
    for bad in (
        PlanFields("manual", 30_000, (b, 999)),
        PlanFields("manual", 30_000, (b, b)),
        PlanFields("snowball", -1),
    ):
        with pytest.raises(PayoffError):
            service.save_plan(conn, bad, today=TODAY)


def test_progress_catch_up() -> None:
    """Oracle: spec-anchored — UC-ZF-012 6a by hand: planned 600 € in October and November, booked
    300 € and 700 € → 200 € short over the completed months; the running December does not count."""
    rows, catch_up = progress(
        {"2026-10": 60_000, "2026-11": 60_000, "2026-12": 60_000, "2027-01": 60_000},
        {"2026-10": 30_000, "2026-11": 70_000},
        "2026-12",
    )
    assert [(r.month, r.difference_cents, r.completed) for r in rows] == [
        ("2026-10", -30_000, True),
        ("2026-11", 10_000, True),
        ("2026-12", -60_000, False),
    ]
    assert catch_up == 20_000


# --- HTTP --------------------------------------------------------------------------------------


def test_payoff_api_roundtrip(client: TestClient) -> None:
    """Oracle: spec-anchored — AK15 API contract: before saving, the plan is unsaved and has no
    debts; floats and negative budgets are refused with 422; a save returns the stored strategy and
    budget; what-if answers without saving."""
    first = client.get("/api/payoff")
    assert first.status_code == 200
    assert (first.json()["saved"], first.json()["debts"]) == (False, [])

    assert client.put("/api/payoff", json={"strategy": "snowball", "budget_cents": 10.5}).status_code == 422
    assert client.put("/api/payoff", json={"strategy": "snowball", "budget_cents": -1}).status_code == 422
    assert client.put("/api/payoff", json={"strategy": "fastest", "budget_cents": 0}).status_code == 422

    saved = client.put("/api/payoff", json={"strategy": "avalanche", "budget_cents": 25_000})
    assert saved.status_code == 200
    assert (saved.json()["saved"], saved.json()["strategy"], saved.json()["budget_cents"]) == (
        True,
        "avalanche",
        25_000,
    )
    assert client.get("/api/debts/settings").json() == {"payoff_budget_cents": 25_000}

    what_if = client.get("/api/payoff/what-if", params={"delta_cents": 10_000})
    assert what_if.status_code == 200
    assert what_if.json()["budget_cents"] == 35_000
    assert client.get("/api/debts/settings").json() == {"payoff_budget_cents": 25_000}
