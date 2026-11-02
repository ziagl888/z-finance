"""UC-ZF-010 debts: liability account, instalments, remaining amount, end, payoff, overview, API.

Expected values are hand-computed from the use case and the calendar; every value is synthetic.
"today" is 2026-09-19, so the 12-month horizon ends on 2027-09-19.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.app_settings import get_setting
from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields
from zfinance.domain.debts import service
from zfinance.domain.debts.service import DebtFields
from zfinance.domain.recurring import service as recurring
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
ALL = date.max

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "debts.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def giro(conn: sqlite3.Connection) -> int:
    """A paying account with 10,000.00 € from 2026-09-01."""
    fields = AccountFields("Test giro", "private", "giro", 1_000_000, date(2026, 9, 1))
    return accounts.create_account(conn, fields).id


def _loan(conn: sqlite3.Connection, giro: int, **overrides: object) -> service.Debt:
    """UC-ZF-010 AK1: 5,000 €, 1,000 € repaid, 250 € monthly from 2026-10-01."""
    values: dict[str, object] = {
        "creditor": "Test bank",
        "area": "private",
        "total_amount_cents": 500_000,
        "installment_cents": 25_000,
        "interval": "monthly",
        "first_due": date(2026, 10, 1),
        "pay_account_id": giro,
    }
    repaid = int(overrides.pop("repaid_cents", 100_000))  # type: ignore[call-overload]
    values.update(overrides)
    return service.create_debt(conn, DebtFields(**values), repaid_cents=repaid, today=TODAY)  # type: ignore[arg-type]


def _planned(conn: sqlite3.Connection, recurring_id: int | None) -> list[tuple[str, int]]:
    """(due date, instalment) of the planned instalments, by due date (outgoing leg only)."""
    rows = conn.execute(
        "SELECT due_date, amount_cents FROM \"transaction\" WHERE recurring_id = ? AND status = 'planned'"
        " AND amount_cents < 0 ORDER BY due_date",
        (recurring_id,),
    )
    return [(row[0], -row[1]) for row in rows]


def _confirm(conn: sqlite3.Connection, recurring_id: int, due: str) -> None:
    """Stand-in for confirming a due instalment (UC-ZF-004, task -011): both legs become booked."""
    group = conn.execute(
        'SELECT transfer_group_id FROM "transaction" WHERE recurring_id = ? AND due_date = ?', (recurring_id, due)
    ).fetchone()[0]
    conn.execute("UPDATE \"transaction\" SET status = 'booked' WHERE transfer_group_id = ?", (group,))


def _total(conn: sqlite3.Connection) -> int:
    """Sum of all account balances over every booked row."""
    return sum(accounts.balance_at(conn, a.id, ALL) for a in accounts.list_accounts(conn, include_archived=True))


# --- AK1: create -------------------------------------------------------------------------------


def test_debt_created_with_remaining_end_and_planned_instalments(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK1: remaining 4,000 €, end 01.01. in 16 months, 12 planned.

    4,000 / 250 = 16 instalments from 2026-10-01: the 16th is 2028-01-01. Horizon 2026-09-19 ..
    2027-09-19 holds 2026-10-01 .. 2027-09-01 = 12.
    """
    debt = _loan(conn, giro)

    assert debt.remaining_cents == 400_000
    assert debt.paid_cents == 100_000
    assert debt.plan is not None
    assert (debt.plan.count, debt.plan.next_due, debt.plan.end) == (16, date(2026, 10, 1), date(2028, 1, 1))
    assert debt.plan.last_installment_cents == 25_000
    planned = _planned(conn, debt.recurring_id)
    assert len(planned) == 12
    assert planned[0] == ("2026-10-01", 25_000)
    assert planned[-1] == ("2027-09-01", 25_000)
    # The liability account carries the remaining amount; the instalment is a transfer to it.
    liability = accounts.get_account(conn, debt.account_id)
    assert (liability.type, liability.opening_balance_cents, liability.name) == ("liability", -400_000, "Test bank")
    assert debt.recurring_id is not None
    item = recurring.get_recurring(conn, debt.recurring_id)
    assert (item.account_id, item.transfer_target_account_id, item.end_date) == (
        giro,
        debt.account_id,
        date(2028, 1, 1),
    )


# --- AK2: a booked instalment ------------------------------------------------------------------


def test_debt_installment_reduces_balance(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK2 / SPEC AK13: remaining and giro -250 € each, total unchanged.

    Before: 10,000 - 4,000 = 6,000 €. Confirmed 2026-10-01: remaining 3,750, giro 9,750, total
    6,000, 15 instalments left. Imported 2026-11 (replace_planned, UC step 3): remaining 3,500.
    """
    debt = _loan(conn, giro)
    assert debt.recurring_id is not None
    assert _total(conn) == 600_000

    _confirm(conn, debt.recurring_id, "2026-10-01")
    service.sync_debts(conn, today=date(2026, 10, 1))

    after = service.get_debt(conn, debt.id, today=date(2026, 10, 1))
    assert after.remaining_cents == 375_000
    assert accounts.balance_at(conn, giro, ALL) == 975_000
    assert _total(conn) == 600_000
    assert after.plan is not None
    assert (after.plan.count, after.plan.next_due, after.plan.end) == (15, date(2026, 11, 1), date(2028, 1, 1))

    # The bank statement line replaces the planned instalment (contract of task -009).
    planned_leg = conn.execute(
        "SELECT id FROM \"transaction\" WHERE recurring_id = ? AND due_date = '2026-11-01' AND amount_cents < 0",
        (debt.recurring_id,),
    ).fetchone()[0]
    real = transactions.create_transaction(
        conn, BookingFields(date(2026, 11, 2), -25_000, giro, None, "Test bank"), today=date(2026, 11, 2)
    )
    recurring.replace_planned(conn, planned_leg, real.id)

    assert service.get_debt(conn, debt.id, today=date(2026, 11, 2)).remaining_cents == 350_000
    assert accounts.balance_at(conn, giro, ALL) == 950_000
    assert _total(conn) == 600_000


# --- AK3 + 5a: paid off ------------------------------------------------------------------------


def test_debt_closed_removes_planned(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK3, 5a / SPEC AK13: remaining 0 → closed, no planned instalment.

    After one instalment 3,750 € are open; a special payment of 5,000 € is capped to 3,750 €, so
    the giro keeps 10,000 - 250 - 3,750 = 6,000 €.
    """
    debt = _loan(conn, giro)
    assert debt.recurring_id is not None
    _confirm(conn, debt.recurring_id, "2026-10-01")

    payment = service.pay_extra(conn, debt.id, 500_000, on=TODAY, today=TODAY)

    assert (payment.booked_cents, payment.capped) == (375_000, True)
    assert payment.debt.remaining_cents == 0
    assert payment.debt.closed_at is not None
    assert payment.debt.plan is None
    assert _planned(conn, debt.recurring_id) == []
    assert not recurring.get_recurring(conn, debt.recurring_id).active
    assert accounts.balance_at(conn, giro, ALL) == 600_000
    # A second run creates nothing again, and a paid-off debt takes no further payment.
    recurring.generate_planned(conn, today=TODAY)
    assert _planned(conn, debt.recurring_id) == []
    with pytest.raises(service.Conflict):
        service.pay_extra(conn, debt.id, 1_000, on=TODAY, today=TODAY)


def test_debt_paid_off_by_confirmed_instalment_is_closed_by_sync(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-010 step 7: reaching 0 through a confirmed instalment closes the debt.

    500 € at 250 €: two instalments 2026-10-01 and 2026-11-01; after both are booked, 0 remain.
    """
    debt = _loan(conn, giro, total_amount_cents=50_000, repaid_cents=0)
    assert debt.recurring_id is not None
    _confirm(conn, debt.recurring_id, "2026-10-01")
    _confirm(conn, debt.recurring_id, "2026-11-01")

    assert service.sync_debts(conn, today=TODAY) == []

    closed = service.get_debt(conn, debt.id, today=TODAY)
    assert (closed.remaining_cents, closed.closed_at is not None) == (0, True)


# --- AK4: overview -----------------------------------------------------------------------------


def test_debt_overview_sums_per_area(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK4: remaining, instalment, end per debt; sums per area and total.

    Private: 4,000 € (AK1 loan) + 1,200 € (no instalment) = 5,200 €. Business: 500 € at 100 €
    monthly from 2026-10-15 = 5 instalments, end 2027-02-15. Total 5,700 €.
    """
    _loan(conn, giro)
    service.create_debt(conn, DebtFields("Test friend", "private", 120_000), today=TODAY)
    service.create_debt(
        conn,
        DebtFields("Test supplier", "business", 50_000, 10_000, "monthly", date(2026, 10, 15), giro),
        today=TODAY,
    )

    result = service.overview(conn, today=TODAY)

    assert result.remaining_by_area == {"private": 520_000, "business": 50_000}
    assert result.remaining_total_cents == 570_000
    rows = {d.creditor: (d.remaining_cents, d.installment_cents, d.plan and d.plan.end) for d in result.debts}
    assert rows == {
        "Test bank": (400_000, 25_000, date(2028, 1, 1)),
        "Test friend": (120_000, None, None),
        "Test supplier": (50_000, 10_000, date(2027, 2, 15)),
    }


# --- extensions --------------------------------------------------------------------------------


def test_debt_without_installment_required_rate(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-010 1a: no recurring item; 1,200 € by 2027-09-30 needs 100 € a month.

    October 2026 .. September 2027 = 12 monthly payments; 1,200 / 12 = 100 €. 1,000 € over 3
    months (target 2026-12-31) = 333.33… rounded up to 333.34 €.
    """
    debt = service.create_debt(
        conn, DebtFields("Test friend", "private", 120_000, target_end=date(2027, 9, 30)), today=TODAY
    )

    assert debt.recurring_id is None
    assert debt.plan is None
    assert debt.required_monthly_cents == 10_000
    assert service.required_monthly_cents(100_000, date(2026, 12, 31), TODAY) == 33_334
    assert conn.execute("SELECT count(*) FROM recurring").fetchone()[0] == 0


def test_debt_last_installment_is_remainder(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-010 step 2 (remaining ÷ instalment): 600 € at 250 € = 250, 250, 100.

    Three instalments 2026-10-01, 11-01, 12-01; the recurring item ends on the last one.
    """
    debt = _loan(conn, giro, total_amount_cents=60_000, repaid_cents=0)

    assert debt.plan is not None
    assert (debt.plan.count, debt.plan.end, debt.plan.last_installment_cents) == (3, date(2026, 12, 1), 10_000)
    assert _planned(conn, debt.recurring_id) == [("2026-10-01", 25_000), ("2026-11-01", 25_000), ("2026-12-01", 10_000)]
    # The liability leg is mirrored, so the pair still sums to 0.
    legs = conn.execute(
        "SELECT sum(amount_cents) FROM \"transaction\" WHERE recurring_id = ? AND due_date = '2026-12-01'",
        (debt.recurring_id,),
    ).fetchone()[0]
    assert legs == 0


def test_debt_increase_recomputes_end(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-010 7a: raising the total by 500 € books the difference, end moves.

    4,500 / 250 = 18 instalments from 2026-10-01: end 2028-03-01.
    """
    debt = _loan(conn, giro)
    raised = DebtFields("Test bank", "private", 550_000, 25_000, "monthly", date(2026, 10, 1), giro)

    after = service.update_debt(conn, debt.id, raised, today=TODAY)

    assert after.remaining_cents == 450_000
    assert after.plan is not None
    assert (after.plan.count, after.plan.end) == (18, date(2028, 3, 1))
    increase = [
        (t.date, t.amount_cents, t.status) for t in service.history(conn, debt.id) if t.transfer_group_id is None
    ]
    assert increase == [(TODAY, -50_000, "booked")]
    assert debt.recurring_id is not None
    assert recurring.get_recurring(conn, debt.recurring_id).end_date == date(2028, 3, 1)
    # Lowering the total to what is already repaid (1,000 €) is refused.
    with pytest.raises(service.DebtsError):
        service.update_debt(
            conn, debt.id, DebtFields("Test bank", "private", 100_000, 25_000, "monthly", date(2026, 10, 1), giro)
        )


def test_debt_input_rules(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-010 step 1: an instalment needs interval, first due date and paying account."""
    with pytest.raises(service.DebtsError):
        service.create_debt(conn, DebtFields("Test bank", "private", 500_000, 25_000), today=TODAY)
    with pytest.raises(service.DebtsError):
        service.create_debt(
            conn, DebtFields("Test bank", "private", 500_000, 25_000, "monthly", date(2026, 10, 1)), today=TODAY
        )
    with pytest.raises(service.DebtsError):
        _loan(conn, giro, repaid_cents=500_000)
    with pytest.raises(service.DebtsError):
        _loan(conn, giro, interest_rate_pct="abc")
    assert _loan(conn, giro, creditor="Test bank 2", interest_rate_pct="4,5").interest_rate_pct == "4.5"


# --- payoff budget -----------------------------------------------------------------------------


def test_payoff_budget_setting(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — orchestrator contract: app_setting debts.payoff_budget_cents, default 0, ≥ 0."""
    assert service.payoff_budget(conn) == 0
    assert service.set_payoff_budget(conn, 30_000) == 30_000
    assert service.payoff_budget(conn) == 30_000
    assert get_setting(conn, "debts.payoff_budget_cents") == 30_000
    with pytest.raises(service.DebtsError):
        service.set_payoff_budget(conn, -1)


# --- API ---------------------------------------------------------------------------------------


def test_debts_api_roundtrip(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK1/5a over HTTP: remaining 4,000 €, a 5,000 € payment capped at 4,000 €."""
    today = date.today()
    giro = client.post(
        "/api/accounts",
        json={
            "name": "Test giro",
            "area": "private",
            "type": "giro",
            "opening_balance_cents": 1_000_000,
            "opening_date": (today - timedelta(days=30)).isoformat(),
        },
    ).json()["id"]
    body = {
        "creditor": "Test bank",
        "area": "private",
        "total_amount_cents": 500_000,
        "repaid_cents": 100_000,
        "installment_cents": 25_000,
        "interval": "monthly",
        "first_due": (today + timedelta(days=5)).isoformat(),
        "pay_account_id": giro,
    }

    created = client.post("/api/debts", json=body)
    assert created.status_code == 201
    debt = created.json()
    assert (debt["remaining_cents"], debt["plan"]["count"], debt["closed"]) == (400_000, 16, False)

    listed = client.get("/api/debts").json()
    assert listed["remaining_total_cents"] == 400_000
    assert listed["remaining_by_area"] == {"private": 400_000, "business": 0}

    paid = client.post(f"/api/debts/{debt['id']}/payments", json={"amount_cents": 500_000})
    assert paid.status_code == 201
    assert (paid.json()["booked_cents"], paid.json()["capped"], paid.json()["debt"]["closed"]) == (400_000, True, True)
    assert client.get(f"/api/debts/{debt['id']}/history").json()[0]["amount_cents"] == 400_000

    assert client.put("/api/debts/settings", json={"payoff_budget_cents": 20_000}).json() == {
        "payoff_budget_cents": 20_000
    }
    assert client.put("/api/debts/settings", json={"payoff_budget_cents": 1.5}).status_code == 422
    assert client.get("/api/debts/999").status_code == 404
    assert client.delete(f"/api/debts/{debt['id']}").status_code == 204
    assert client.get("/api/debts").json()["debts"] == []
