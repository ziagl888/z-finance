"""The core rule of z-finance: every "free" number is net of the open levy gap (SPEC "Kernregel").

``free_amount = Σ payment accounts - planned items up to the date - reserve - levy_gap``; the gap
arises with the *booking* of an income, not with the transfer into the levy pot. Every value is
synthetic and hand-computed; "today" is 2026-09-19, the month ends on 2026-09-30.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields
from zfinance.domain.debts import service as debts
from zfinance.domain.debts.service import DebtFields
from zfinance.domain.levy import service
from zfinance.domain.levy.service import LevyFields
from zfinance.domain.pots import service as pots
from zfinance.domain.pots.service import PotFields
from zfinance.domain.recurring import service as recurring
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransferFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
MONTH_END = date(2026, 9, 30)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "free.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def business(conn: sqlite3.Connection) -> int:
    """The business account: 5,000.00 € from 2026-01-01."""
    return accounts.create_account(
        conn, AccountFields("Test business", "business", "giro", 500_000, date(2026, 1, 1))
    ).id


def _free(conn: sqlite3.Connection, as_of: date = MONTH_END) -> int:
    return service.free_amount(conn, as_of, today=TODAY)


def test_free_amount_is_net_of_levy_gap(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — SPEC-z-finance.md "Kernregel" + UC-ZF-015 AK7 / NF5: an income of
    10,000 € at 30 % raises ``free_amount`` by exactly 7,000 € right after the booking; moving the
    3,000 € into the levy pot afterwards changes nothing (the gap arises with the booking)."""
    levy_pot = pots.create_pot(conn, PotFields("Levies", business, is_levy_pot=True), today=TODAY).id
    service.save_settings(conn, LevyFields(3000), today=TODAY)
    before = _free(conn)
    assert before == 500_000

    transactions.create_transaction(conn, BookingFields(TODAY, 1_000_000, business, None, "Test client"), today=TODAY)
    after = _free(conn)
    assert after - before == 700_000  # never 10,000 €

    pots.deposit(conn, levy_pot, 300_000, today=TODAY)
    assert _free(conn) == after
    breakdown = service.free_amount_breakdown(conn, MONTH_END, today=TODAY)
    assert (breakdown.accounts_cents, breakdown.levy_gap_cents) == (1_200_000, 0)


def test_free_amount_formula_terms(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — SPEC "Kernregel" hand-computed: 5,000 € on the account, reserve floor
    1,000 €, a planned expense of 800 € on 2026-09-25 (in) and one on 2026-10-05 (not up to the
    month end), 30 % of a 2,000 € income booked = 600 € gap → 5,000 + 2,000 - 800 - 1,000 - 600."""
    accounts.update_account(
        conn, business, AccountFields("Test business", "business", "giro", 500_000, date(2026, 1, 1), 100_000)
    )
    service.save_settings(conn, LevyFields(3000), today=TODAY)
    transactions.create_transaction(conn, BookingFields(TODAY, 200_000, business, None, "Test client"), today=TODAY)
    recurring.create_expected(conn, BookingFields(date(2026, 9, 25), -80_000, business, None, "Test rent"), today=TODAY)
    recurring.create_expected(conn, BookingFields(date(2026, 10, 5), -80_000, business, None, "Test rent"), today=TODAY)
    recurring.create_expected(
        conn, BookingFields(date(2026, 9, 28), 900_000, business, None, "Test client"), today=TODAY
    )

    terms = service.free_amount_breakdown(conn, MONTH_END, today=TODAY)
    assert (terms.accounts_cents, terms.planned_cents, terms.reserve_cents, terms.levy_gap_cents) == (
        700_000,
        -80_000,
        100_000,
        60_000,
    )
    assert terms.free_cents == 460_000 == _free(conn)
    assert _free(conn, date(2026, 10, 31)) == 380_000


def test_free_amount_transfers_cancel(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — orchestrator rule for the planned items, hand-computed: a planned
    transfer between two payment accounts leaves ``free_amount`` unchanged; a planned instalment of
    500 € into a liability account lowers it by 500 €; the levy back-payment item is not counted."""
    private = accounts.create_account(
        conn, AccountFields("Test private", "private", "giro", 100_000, date(2026, 1, 1))
    ).id
    base = _free(conn)
    transactions.create_transfer(
        conn, TransferFields(date(2026, 9, 25), 50_000, business, private, None, "Test owner draw"), today=TODAY
    )
    assert _free(conn) == base == 600_000

    debts.create_debt(
        conn, DebtFields("Test lender", "private", 100_000, 50_000, "monthly", date(2026, 9, 28), private), today=TODAY
    )
    assert _free(conn) == base - 50_000

    transactions.create_transaction(conn, BookingFields(TODAY, 1_000_000, business, None, "Test client"), today=TODAY)
    service.save_settings(conn, LevyFields(3000, account_id=business, due_month_day="09-30"), today=TODAY)
    assert conn.execute("SELECT count(*) FROM levy_backpayment WHERE transaction_id IS NOT NULL").fetchone()[0] == 1
    # The 2026 back-payment falls on 2027-09-30: within this horizon, but only the gap counts.
    assert _free(conn, date(2027, 9, 30)) == _free(conn, date(2027, 9, 29))


def test_free_amount_refuses_past(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — contract sketch "Pre: as_of >= today": a past date is refused."""
    with pytest.raises(service.LevyError):
        service.free_amount(conn, date(2026, 9, 18), today=TODAY)
