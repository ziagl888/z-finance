"""UC-ZF-011 savings pots: rate, status, free balance, expense, move, emergency order, savings plan, API.

Expected values are hand-computed from the use case and the calendar; every value is synthetic.
"today" is 2026-09-19 unless a test says otherwise.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields
from zfinance.domain.pots import service
from zfinance.domain.pots.service import EmergencyCandidate, PotFields
from zfinance.domain.recurring import service as recurring
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "pots.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def savings(conn: sqlite3.Connection) -> int:
    """A savings account with 5,000.00 € from 2026-01-01."""
    fields = AccountFields("Test savings", "private", "savings", 500_000, date(2026, 1, 1))
    return accounts.create_account(conn, fields).id


@pytest.fixture
def giro(conn: sqlite3.Connection) -> int:
    """A giro account with 10,000.00 € from 2026-01-01."""
    fields = AccountFields("Test giro", "private", "giro", 1_000_000, date(2026, 1, 1))
    return accounts.create_account(conn, fields).id


def _pot(conn: sqlite3.Connection, account: int, name: str, balance: int = 0, **overrides: object) -> service.Pot:
    """A pot created today with ``balance`` cents deposited from the free balance."""
    today = overrides.pop("today", TODAY)
    pot = service.create_pot(conn, PotFields(name, account, **overrides), today=today)  # type: ignore[arg-type]
    if balance:
        pot = service.deposit(conn, pot.id, balance, today=today)  # type: ignore[arg-type]
    return pot


def _expense(conn: sqlite3.Connection, account: int, cents: int, on: date = TODAY) -> int:
    """A booked expense of ``cents`` on the account."""
    return transactions.create_transaction(conn, BookingFields(on, -cents, account, None, "Test shop"), today=TODAY).id


def _kinds(conn: sqlite3.Connection, pot_id: int) -> list[str]:
    return [a.kind for a in service.allocations(conn, pot_id)]


# --- pure rules --------------------------------------------------------------------------------


def test_pot_monthly_rate_ak1() -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK1: (3,000 - 500) ÷ 10 months = 250 €; reached → 0."""
    assert service.monthly_rate(300_000, 50_000, date(2027, 7, 19), TODAY) == 25_000
    assert service.monthly_rate(300_000, 300_000, date(2027, 7, 19), TODAY) == 0
    # A target in the current month leaves one payment: the whole rest.
    assert service.monthly_rate(300_000, 50_000, date(2026, 9, 30), TODAY) == 250_000


def test_pot_emergency_split_by_rank() -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK6 hand-computed: 500 → 300 from TV (1), 200 from holiday (2)."""
    candidates = [
        EmergencyCandidate(pot_id=4, rank=None, locked=True, balance_cents=100_000),  # emergency fund
        EmergencyCandidate(pot_id=3, rank=3, locked=False, balance_cents=60_000),  # PC
        EmergencyCandidate(pot_id=1, rank=1, locked=False, balance_cents=30_000),  # TV
        EmergencyCandidate(pot_id=2, rank=2, locked=False, balance_cents=90_000),  # holiday
    ]
    assert service.emergency_split(50_000, candidates) == [(1, 30_000), (2, 20_000)]
    # More than the unlocked pots hold: all three are emptied, the locked one never touched.
    assert service.emergency_split(500_000, candidates) == [(1, 30_000), (2, 90_000), (3, 60_000)]


# --- AK1 / AK3: rate and plan status -----------------------------------------------------------


def test_pot_rate_and_status(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK1 (250 €, on track) and AK3: two months later without a
    deposit the pot is behind and the new rate is (3,000 - 500) ÷ 8 months = 312.50 €."""
    start = date(2026, 1, 10)
    pot = _pot(conn, savings, "Car", 50_000, target_amount_cents=300_000, target_date=date(2026, 11, 10), today=start)
    assert (pot.balance_cents, pot.monthly_rate_cents, pot.status, pot.progress_pct) == (50_000, 25_000, "on_track", 16)

    later = service.get_pot(conn, pot.id, today=date(2026, 3, 10))
    assert (later.status, later.monthly_rate_cents) == ("behind", 31_250)

    # UC 1a/1b: without a date only "reached" is a status; without a target there is none.
    fund = _pot(conn, savings, "Emergency fund", 20_000, target_amount_cents=10_000, today=start)
    open_saving = _pot(conn, savings, "Open", 10_000, today=start)
    assert (fund.status, fund.monthly_rate_cents) == ("reached", None)
    assert (open_saving.status, open_saving.monthly_rate_cents, open_saving.progress_pct) == (None, None, None)


# --- AK2: free balance and guard ---------------------------------------------------------------


def test_pot_free_balance_guard(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK2 / 3a: 5,000 - (1,000 + 500) = 3,500 € free; 3,600 € refused."""
    _pot(conn, savings, "A", 100_000)
    b = _pot(conn, savings, "B", 50_000)
    assert service.free_balance(conn, savings, TODAY) == 350_000

    with pytest.raises(service.Conflict) as refused:
        service.deposit(conn, b.id, 360_000, today=TODAY)
    assert refused.value.params == {"amount": "3500.00"}
    assert service.get_pot(conn, b.id, today=TODAY).balance_cents == 50_000

    service.deposit(conn, b.id, 350_000, today=TODAY)
    assert service.free_balance(conn, savings, TODAY) == 0


def test_free_balance_contract(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — hand-computed day-exact free balance (contract for -019/-025/-026):
    5,000 € opening on 2026-01-01, 1,500 € into a pot on 2026-01-15, 1,000 € expense on 2026-02-01."""
    pot = _pot(conn, savings, "Holiday")
    service.deposit(conn, pot.id, 150_000, on=date(2026, 1, 15), today=TODAY)
    _expense(conn, savings, 100_000, date(2026, 2, 1))

    assert service.free_balance(conn, savings, date(2025, 12, 31)) == 0  # before the opening date
    assert service.free_balance(conn, savings, date(2026, 1, 14)) == 500_000
    assert service.free_balance(conn, savings, date(2026, 1, 20)) == 350_000
    assert service.free_balance(conn, savings, date(2026, 2, 1)) == 250_000


# --- AK4 / 5a: expense -------------------------------------------------------------------------


def test_pot_expense_lowers_pot_and_account(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK4: a 300 € expense lowers pot (800 → 500) and account
    (5,000 → 4,700) alike; 5a: a 1,000 € expense on 500 € covers 500 € and takes 500 € from the free balance."""
    pot = _pot(conn, savings, "Hardware", 80_000)

    first = _expense(conn, savings, 30_000)
    result = service.assign_expense(conn, pot.id, first, today=TODAY)
    assert (result.pot.balance_cents, result.covered_cents, result.from_free_cents) == (50_000, 30_000, 0)
    assert accounts.balance_at(conn, savings, TODAY) == 470_000
    assert service.free_balance(conn, savings, TODAY) == 420_000  # unchanged

    bigger = service.assign_expense(conn, pot.id, _expense(conn, savings, 100_000), today=TODAY)
    assert (bigger.pot.balance_cents, bigger.covered_cents, bigger.from_free_cents) == (0, 50_000, 50_000)
    assert service.free_balance(conn, savings, TODAY) == 370_000

    with pytest.raises(service.Conflict):  # the same expense twice
        service.assign_expense(conn, pot.id, first, today=TODAY)


# --- AK5: move ---------------------------------------------------------------------------------


def test_pot_move_keeps_account_balance(conn: sqlite3.Connection, savings: int, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK5: 200 € from A (500) to B (0) → A 300, B 200, account 5,000."""
    a = _pot(conn, savings, "A", 50_000)
    b = _pot(conn, savings, "B")
    bookings = conn.execute('SELECT count(*) FROM "transaction"').fetchone()[0]

    source, target = service.move(conn, a.id, b.id, 20_000, today=TODAY)
    assert (source.balance_cents, target.balance_cents) == (30_000, 20_000)
    assert accounts.balance_at(conn, savings, TODAY) == 500_000
    assert service.free_balance(conn, savings, TODAY) == 450_000
    assert conn.execute('SELECT count(*) FROM "transaction"').fetchone()[0] == bookings

    other = _pot(conn, giro, "Other")
    with pytest.raises(service.Conflict):
        service.move(conn, a.id, other.id, 10_000, today=TODAY)
    with pytest.raises(service.Conflict):  # more than A holds
        service.move(conn, a.id, b.id, 30_001, today=TODAY)


# --- concurrent requests: the precondition holds under the write lock ---------------------------


def _interpose_after(
    monkeypatch: pytest.MonkeyPatch, check: str, conn: sqlite3.Connection, interloper: Callable[[], object]
) -> list[bool]:
    """Run ``interloper`` once, right after the first call of ``service.<check>`` outside a transaction.

    The services run that check before ``BEGIN IMMEDIATE``; a second request (double click, retry)
    on its own connection can commit in that gap. The hook hits the gap deterministically.
    """
    real = getattr(service, check)
    fired: list[bool] = []

    def checked(*args: object, **kwargs: object) -> object:
        result = real(*args, **kwargs)
        if not fired and not conn.in_transaction:
            fired.append(True)
            interloper()
        return result

    monkeypatch.setattr(service, check, checked)
    return fired


def test_pot_concurrent_withdrawals_cannot_overdraw(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, savings: int
) -> None:
    """Oracle: spec-anchored — withdraw refuses more than the pot holds (_check_available docstring)
    and task T-2026-HAR-9130-005: two 600 € withdrawals from 1,000 € may not both pass; the pot keeps
    400 € and the free balance rises by 600 € only (4,000 → 4,600)."""
    pot = _pot(conn, savings, "Hardware", 100_000)
    other = connect(tmp_path / "pots.sqlite3")
    fired = _interpose_after(
        monkeypatch, "_check_available", conn, lambda: service.withdraw(other, pot.id, 60_000, today=TODAY)
    )

    with pytest.raises(service.Conflict):
        service.withdraw(conn, pot.id, 60_000, today=TODAY)
    other.close()

    assert fired == [True]
    assert service.get_pot(conn, pot.id, today=TODAY).balance_cents == 40_000
    assert service.free_balance(conn, savings, TODAY) == 460_000


def test_pot_concurrent_moves_cannot_overdraw(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, savings: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK5 (no more than A holds) and task T-2026-HAR-9130-005:
    two 300 € moves from A (500 €) to B may not both pass → A 200, B 300."""
    a = _pot(conn, savings, "A", 50_000)
    b = _pot(conn, savings, "B")
    other = connect(tmp_path / "pots.sqlite3")
    fired = _interpose_after(
        monkeypatch, "_check_available", conn, lambda: service.move(other, a.id, b.id, 30_000, today=TODAY)
    )

    with pytest.raises(service.Conflict):
        service.move(conn, a.id, b.id, 30_000, today=TODAY)
    other.close()

    assert fired == [True]
    assert service.get_pot(conn, a.id, today=TODAY).balance_cents == 20_000
    assert service.get_pot(conn, b.id, today=TODAY).balance_cents == 30_000


def test_pot_concurrent_expense_assignments_assign_once(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, savings: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK4 ("the same expense twice" is refused) and task
    T-2026-HAR-9130-005: the second of two concurrent assignments raises Conflict (not an
    IntegrityError); the 300 € expense lowers the pot once (800 → 500)."""
    pot = _pot(conn, savings, "Hardware", 80_000)
    expense = _expense(conn, savings, 30_000)
    other = connect(tmp_path / "pots.sqlite3")
    fired = _interpose_after(
        monkeypatch, "_check_unlocked", conn, lambda: service.assign_expense(other, pot.id, expense, today=TODAY)
    )

    with pytest.raises(service.Conflict):
        service.assign_expense(conn, pot.id, expense, today=TODAY)
    other.close()

    assert fired == [True]
    assert service.get_pot(conn, pot.id, today=TODAY).balance_cents == 50_000


# --- AK6 / AK7 / 5c: emergency -----------------------------------------------------------------


def _ak6_pots(conn: sqlite3.Connection, savings: int) -> dict[str, service.Pot]:
    """UC-ZF-011 AK6: TV (1, 300 €), holiday (2, 900 € of 1,500 € by 2027-03-15), PC (3, 600 €),
    emergency fund (locked, 1,000 €)."""
    return {
        "tv": _pot(conn, savings, "TV", 30_000),
        "holiday": _pot(conn, savings, "Holiday", 90_000, target_amount_cents=150_000, target_date=date(2027, 3, 15)),
        "pc": _pot(conn, savings, "PC", 60_000),
        "fund": _pot(conn, savings, "Emergency fund", 100_000, locked=True),
    }


def test_pot_emergency_proposal_and_confirm(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK6 hand-computed: 300 € from TV, 200 € from holiday; the
    holiday rate is (1,500 - 900) ÷ 6 months = 100 €, so 200 € move its target date 2 months to 2027-05-15."""
    pots = _ak6_pots(conn, savings)
    assert [p.emergency_rank for p in pots.values()] == [1, 2, 3, None]

    proposal = service.emergency_proposal(conn, savings, 50_000, today=TODAY)
    assert [(line.pot_id, line.amount_cents, line.new_balance_cents) for line in proposal.lines] == [
        (pots["tv"].id, 30_000, 0),
        (pots["holiday"].id, 20_000, 70_000),
    ]
    assert proposal.lines[1].shifted_target_date == date(2027, 5, 15)
    assert (proposal.covered_cents, proposal.shortfall_cents) == (50_000, 0)

    free_before = service.free_balance(conn, savings, TODAY)
    confirmed = service.confirm_emergency(
        conn, savings, [(line.pot_id, line.amount_cents) for line in proposal.lines], today=TODAY
    )
    assert [(p.balance_cents, p.target_date) for p in confirmed] == [(0, None), (70_000, date(2027, 5, 15))]
    assert service.get_pot(conn, pots["fund"].id, today=TODAY).balance_cents == 100_000
    assert service.free_balance(conn, savings, TODAY) == free_before + 50_000
    assert _kinds(conn, pots["tv"].id)[0] == "emergency"

    with pytest.raises(service.Conflict):  # a locked pot is never part of an emergency
        service.confirm_emergency(conn, savings, [(pots["fund"].id, 10_000)], today=TODAY)


def test_pot_rank_reorder_changes_proposal(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK7: order PC, TV, holiday → 500 € come from PC (600 €) alone."""
    pots = _ak6_pots(conn, savings)
    service.set_ranks(conn, savings, [pots["pc"].id, pots["tv"].id, pots["holiday"].id], today=TODAY)

    proposal = service.emergency_proposal(conn, savings, 50_000, today=TODAY)
    assert [(line.pot_id, line.amount_cents) for line in proposal.lines] == [(pots["pc"].id, 50_000)]

    with pytest.raises(service.PotsError):  # the locked pot has no rank; every ranked pot once
        service.set_ranks(conn, savings, [pots["pc"].id, pots["fund"].id], today=TODAY)


def test_pot_locked_withdrawal_needs_confirmation(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 5c: a locked pot gives money only after confirmation, logged "Notfall"."""
    fund = _pot(conn, savings, "Emergency fund", 100_000, locked=True)
    with pytest.raises(service.Conflict):
        service.withdraw(conn, fund.id, 20_000, today=TODAY)

    after = service.withdraw(conn, fund.id, 20_000, confirm_locked=True, today=TODAY)
    assert (after.balance_cents, after.locked) == (80_000, True)
    assert _kinds(conn, fund.id) == ["emergency", "deposit"]


# --- SPEC AK14: planned expense; UC step 2: savings plan; UC step 6: close ----------------------


def test_pot_plan_expense_in_forecast(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — SPEC AK14 / UC-ZF-006 2d: target 1,200 € on 2027-06-01 is a planned
    expense of -1,200 € on that day on the pot's account; it follows the target and goes on close."""
    pot = _pot(conn, savings, "Laptop", target_amount_cents=120_000, target_date=date(2027, 6, 1), plan_expense=True)

    def planned() -> list[tuple[str, int, int, str]]:
        rows = conn.execute(
            "SELECT date, amount_cents, account_id, text FROM \"transaction\" WHERE status = 'planned' ORDER BY id"
        )
        return [tuple(row) for row in rows]  # type: ignore[misc]

    assert planned() == [("2027-06-01", -120_000, savings, "Laptop")]

    fields = PotFields("Laptop", savings, target_amount_cents=150_000, target_date=date(2027, 7, 1), plan_expense=True)
    updated = service.update_pot(conn, pot.id, fields, today=TODAY)
    assert planned() == [("2027-07-01", -150_000, savings, "Laptop")]
    assert updated.planned_transaction_id is not None

    service.close_pot(conn, pot.id, today=TODAY)
    assert planned() == []


def test_pot_savings_plan_deposits_count(conn: sqlite3.Connection, savings: int, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 step 2 / AK1: a 250 € plan transfers monthly from
    2026-10-19; a booked instalment raises the pot by 250 € and stays after the plan is deleted."""
    pot = _pot(conn, savings, "Car", target_amount_cents=300_000, target_date=date(2027, 7, 19))
    assert pot.monthly_rate_cents == 30_000  # (3,000 - 0) ÷ 10 months

    pot = service.set_savings_plan(conn, pot.id, giro, amount_cents=25_000, today=TODAY)
    item = recurring.get_recurring(conn, pot.savings_plan_id or 0)
    assert (item.pot_id, item.amount_cents, item.start_date, item.end_date) == (
        pot.id,
        25_000,
        date(2026, 10, 19),
        date(2027, 7, 19),
    )
    assert (pot.balance_cents, pot.expected_reach) == (0, date(2027, 9, 19))  # 12 instalments from 2026-10-19

    # Stand-in for confirming the first instalment (UC-ZF-004, task -011).
    conn.execute(
        "UPDATE \"transaction\" SET status = 'booked' WHERE recurring_id = ? AND due_date = '2026-10-19'", (item.id,)
    )
    later = date(2026, 10, 20)
    assert service.pot_balance(conn, pot.id, later) == 25_000  # counts before it is collected
    assert service.free_balance(conn, savings, later) == 500_000  # the money arrived with it

    service.delete_savings_plan(conn, pot.id, today=later)
    assert service.get_pot(conn, pot.id, today=later).balance_cents == 25_000
    assert conn.execute("SELECT count(*) FROM recurring").fetchone()[0] == 0


def test_pot_close_returns_rest(conn: sqlite3.Connection, savings: int) -> None:
    """Oracle: spec-anchored — UC-ZF-011 step 6: closing a 400 € pot returns 400 € to the free balance
    and the emergency order closes up (B moves from rank 2 to 1)."""
    a = _pot(conn, savings, "A", 40_000)
    b = _pot(conn, savings, "B", 10_000)
    assert service.free_balance(conn, savings, TODAY) == 450_000

    closed = service.close_pot(conn, a.id, today=TODAY)
    assert (closed.balance_cents, closed.emergency_rank, closed.closed_at is not None) == (0, None, True)
    assert service.free_balance(conn, savings, TODAY) == 490_000
    assert service.get_pot(conn, b.id, today=TODAY).emergency_rank == 1
    with pytest.raises(service.Conflict):
        service.deposit(conn, a.id, 1_000, today=TODAY)


# --- HTTP ---------------------------------------------------------------------------------------


def test_pots_api_roundtrip(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK2 over HTTP: 5,000 € - 1,000 € in a pot = 4,000 € free;
    a 4,500 € deposit is refused (409); a float amount is refused (422)."""
    today = date.today()
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test savings",
            "area": "business",
            "type": "savings",
            "opening_balance_cents": 500_000,
            "opening_date": (today - timedelta(days=30)).isoformat(),
        },
    ).json()["id"]

    created = client.post(
        "/api/pots", json={"name": "Levy", "account_id": account, "locked": True, "is_levy_pot": True}
    )
    assert created.status_code == 201
    pot = created.json()
    assert (pot["area"], pot["is_levy_pot"], pot["locked"], pot["emergency_rank"]) == ("business", True, True, None)

    assert client.post(f"/api/pots/{pot['id']}/deposits", json={"amount_cents": 100_000}).status_code == 201
    assert client.get("/api/pots/free-balance", params={"account_id": account}).json()["free_balance_cents"] == 400_000
    listed = client.get("/api/pots").json()
    assert listed["accounts"] == [
        {
            "account_id": account,
            "balance_cents": 500_000,
            "pots_cents": 100_000,
            "free_cents": 400_000,
            "emergency_available_cents": 0,
        }
    ]
    assert client.post(f"/api/pots/{pot['id']}/deposits", json={"amount_cents": 450_000}).status_code == 409
    assert client.post(f"/api/pots/{pot['id']}/deposits", json={"amount_cents": 1.5}).status_code == 422
    assert client.post(f"/api/pots/{pot['id']}/withdrawals", json={"amount_cents": 1_000}).status_code == 409
    assert (
        client.get("/api/pots/emergency", params={"account_id": account, "amount_cents": 1_000}).json()[
            "shortfall_cents"
        ]
        == 1_000
    )
    assert client.get(f"/api/pots/{pot['id']}/allocations").json()[0]["kind"] == "deposit"
    assert client.get("/api/pots/999").status_code == 404
    assert client.delete(f"/api/pots/{pot['id']}").status_code == 204
    assert client.get("/api/pots").json()["pots"] == []
