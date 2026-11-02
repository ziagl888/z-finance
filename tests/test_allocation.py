"""Allocation rules (task -025, UC-ZF-014): proposals from a booked income, the reservation of the
levy share, confirming, rejecting and the reversal suggestion.

Every amount is synthetic and hand-computed from UC-ZF-014; "today" is 2026-09-19. The scenario of
AK1 is the same throughout: a business giro account with 5,000.00 €, a savings account carrying the
levy pot and the pot "Test reserve", a levy rate of 30 %, and a rule "from 500 € business income:
20 % debt payoff, 5 % Test reserve".
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
from zfinance.domain.allocation import lines, service
from zfinance.domain.allocation.lines import Line, Target
from zfinance.domain.allocation.service import RuleFields
from zfinance.domain.debts import service as debts
from zfinance.domain.debts.service import DebtFields
from zfinance.domain.levy import service as levy
from zfinance.domain.levy.service import LevyFields
from zfinance.domain.pots import service as pots
from zfinance.domain.pots.service import PotFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
MONTH_END = date(2026, 9, 30)
INCOME_CENTS = 400_000  # 4,000.00 €
LEVY_SHARE = 120_000  # 30 % of the income (AK1)
DEBT_SHARE = 80_000  # 20 %
POT_SHARE = 20_000  # 5 %


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "allocation.sqlite3")
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
def savings(conn: sqlite3.Connection) -> int:
    """The savings account the pots live on (so a deposit is a real transfer)."""
    return accounts.create_account(conn, AccountFields("Test savings", "business", "savings", 0, date(2026, 1, 1))).id


@pytest.fixture
def levy_pot(conn: sqlite3.Connection, savings: int) -> int:
    """The levy pot with a rate of 30 % (UC-ZF-015 settings)."""
    pot_id = pots.create_pot(conn, PotFields("Test levies", savings, is_levy_pot=True), today=TODAY).id
    levy.save_settings(conn, LevyFields(3000), today=TODAY)
    return pot_id


@pytest.fixture
def reserve_pot(conn: sqlite3.Connection, savings: int) -> int:
    """The pot the 5 % share goes into."""
    return pots.create_pot(conn, PotFields("Test reserve", savings), today=TODAY).id


@pytest.fixture
def debt(conn: sqlite3.Connection, giro: int) -> int:
    """An open debt of 5,000.00 € without an instalment (the plan's first open debt)."""
    return debts.create_debt(conn, DebtFields("Test lender", "business", 500_000, pay_account_id=giro), today=TODAY).id


def _rule(conn: sqlite3.Connection, reserve_pot: int, **overrides: object) -> service.Rule:
    """The AK1 rule: business income from 500 €, 20 % debt payoff, 5 % into the reserve pot."""
    fields = RuleFields(
        name="Test business income",
        area="business",
        min_amount_cents=50_000,
        targets=(Target("debt_plan", percent_bp=2000), Target("pot", reserve_pot, percent_bp=500)),
        fallback_kind="pot",
        fallback_pot_id=reserve_pot,
    )
    return service.create_rule(conn, RuleFields(**{**fields.__dict__, **overrides}))


def _income(conn: sqlite3.Connection, giro: int, cents: int = INCOME_CENTS, *, on: date = TODAY) -> int:
    return transactions.create_transaction(
        conn, BookingFields(on, cents, giro, None, "Test client fee"), today=TODAY
    ).id


def _free(conn: sqlite3.Connection) -> int:
    return levy.free_amount(conn, MONTH_END, today=TODAY)


def _only_proposal(conn: sqlite3.Connection) -> service.Proposal:
    overview = service.overview(conn, today=TODAY)
    assert len(overview.proposals) == 1
    return overview.proposals[0]


def _transaction_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute('SELECT count(*) FROM "transaction"').fetchone()[0])


def _total_balance(conn: sqlite3.Connection) -> int:
    """Sum over all accounts: unchanged by a transfer (AK3)."""
    return sum(
        accounts.balance_at(conn, account.id, TODAY) for account in accounts.list_accounts(conn, include_archived=True)
    )


# --- the lines of a proposal (pure) ------------------------------------------------------------


def test_allocation_proposal_amounts(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK1 hand-computed: rate 30 %, rule 20 % debt / 5 % pot from
    500 € business income, income 4,000 € → 1,200 € levy pot, 800 € on the first open debt of the
    payoff order, 200 € into the pot, 1,800 € stay free."""
    _rule(conn, reserve_pot)
    _income(conn, giro)

    proposal = _only_proposal(conn)
    assert [(line.kind, line.amount_cents, line.target) for line in proposal.lines] == [
        ("levy", LEVY_SHARE, "Test levies"),
        ("debt_plan", DEBT_SHARE, "Test lender"),
        ("pot", POT_SHARE, "Test reserve"),
    ]
    assert proposal.remaining_cents == 180_000
    assert proposal.lines[1].debt_id == debt
    assert proposal.lines[2].pot_id == reserve_pot
    assert proposal.status == "open"
    assert not proposal.lines[0].editable  # the levy share is a reservation, not a proposal (AK7)


def test_allocation_below_min_no_proposal(conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK2: an income of 400 € under the minimum of 500 € creates
    no proposal, while one of 500 € does."""
    _rule(conn, reserve_pot)
    _income(conn, giro, 40_000)
    assert service.overview(conn, today=TODAY).proposals == []

    _income(conn, giro, 50_000)
    assert len(service.overview(conn, today=TODAY).proposals) == 1


def test_allocation_lines_cap_in_order(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC "Capping in target order": the levy line is paid first and is
    never capped; 1,000 € income with a 900 € levy share and two fixed targets of 200 € leaves
    100 € for the first target and 0 € for the second, which is reported capped."""
    targets = [Target("pot", 7, fixed_cents=20_000), Target("reserve", fixed_cents=20_000)]
    result = lines.proposal_lines(100_000, 90_000, 3, targets)
    assert result == [
        Line("levy", 3, 90_000, 90_000, False),
        Line("pot", 7, 10_000, 0, True),
        Line("reserve", None, 0, 0, True),
    ]
    assert lines.remaining_cents(100_000, result) == 0
    assert lines.bookable_cents(result) == 10_000  # the levy line is already reserved, reserve books nothing


def test_allocation_rule_validation(conn: sqlite3.Connection, reserve_pot: int) -> None:
    """Oracle: spec-anchored — UC-ZF-014 precondition "Σ of the shares ≤ 100 %" and the contract
    sketch: a percentage and a fixed amount together, shares above 100 %, a pot target without a pot
    and a negative minimum are all refused."""
    with pytest.raises(service.AllocationError):
        _rule(conn, reserve_pot, targets=(Target("pot", reserve_pot, percent_bp=500, fixed_cents=100),))
    with pytest.raises(service.AllocationError):
        _rule(conn, reserve_pot, targets=(Target("debt_plan", percent_bp=6000), Target("reserve", percent_bp=5000)))
    with pytest.raises(service.AllocationError):
        _rule(conn, reserve_pot, targets=(Target("pot", percent_bp=500),))
    with pytest.raises(service.AllocationError):
        _rule(conn, reserve_pot, min_amount_cents=-1)
    assert service.list_rules(conn) == []


# --- the core rule ------------------------------------------------------------------------------


def test_allocation_open_proposal_does_not_reserve_twice(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK6 and SPEC "Kernregel": right after the 4,000 € income is
    booked the free amount has risen by 2,800 € (not 4,000 €), and creating the open proposal leaves
    it at 2,800 € — the levy share is reserved once, by ``levy_gap``, not a second time by the open
    levy line."""
    _rule(conn, reserve_pot)
    before = _free(conn)
    assert before == 500_000

    _income(conn, giro)
    after_booking = _free(conn)
    assert after_booking - before == 280_000

    assert service.sync_proposals(conn, today=TODAY) == 1
    assert _free(conn) == after_booking
    assert _transaction_count(conn) == 1  # the income; a proposal books nothing


def test_allocation_confirm_keeps_free_amount_consistent(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK1 ("1,800 € stay free") hand-computed: after confirming,
    the free amount is 5,000 € + 1,800 €. Moving the levy share into the pot lowers the gap by
    exactly what it takes out of the free balance, so the levy is never counted twice."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)

    service.confirm_proposal(conn, proposal.id, today=TODAY)
    assert _free(conn) == 500_000 + 180_000
    assert levy.levy_gap(conn, MONTH_END) == 0


# --- confirming ---------------------------------------------------------------------------------


def test_allocation_confirm_books_three_transfers(
    conn: sqlite3.Connection, giro: int, savings: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK3 hand-computed: confirming the AK1 proposal books exactly
    three transfers, the sum over all accounts is unchanged, the remaining debt falls by 800 € and
    the two pots hold 1,200 € and 200 €."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)
    total_before = _total_balance(conn)
    remaining_before = debts.get_debt(conn, debt, today=TODAY).remaining_cents

    result = service.confirm_proposal(conn, proposal.id, today=TODAY)

    assert result.transfer_count == 3
    assert result.booked_cents == LEVY_SHARE + DEBT_SHARE + POT_SHARE
    assert _total_balance(conn) == total_before
    assert debts.get_debt(conn, debt, today=TODAY).remaining_cents == remaining_before - DEBT_SHARE
    assert pots.pot_balance(conn, levy_pot, TODAY) == LEVY_SHARE
    assert pots.pot_balance(conn, reserve_pot, TODAY) == POT_SHARE
    assert accounts.balance_at(conn, giro, TODAY) == 500_000 + INCOME_CENTS - 220_000
    assert accounts.balance_at(conn, savings, TODAY) == LEVY_SHARE + POT_SHARE
    assert result.proposal.status == "confirmed"
    sources = {row[0] for row in conn.execute('SELECT DISTINCT source FROM "transaction" WHERE amount_cents < 0')}
    assert sources == {"allocation"}


def test_allocation_confirm_twice_books_once(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — contract sketch "a second call raises Conflict": confirming twice
    books the three transfers once and refuses the second call."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)

    service.confirm_proposal(conn, proposal.id, today=TODAY)
    written = _transaction_count(conn)
    with pytest.raises(service.Conflict):
        service.confirm_proposal(conn, proposal.id, today=TODAY)
    assert _transaction_count(conn) == written


def test_allocation_levy_share_floor(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK7: the levy line cannot be confirmed below the rate; the
    error names the reason, nothing is booked, and lowering another line is accepted."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)

    with pytest.raises(service.AllocationError, match="levy share"):
        service.confirm_proposal(conn, proposal.id, {0: LEVY_SHARE - 1}, today=TODAY)
    assert _transaction_count(conn) == 1

    result = service.confirm_proposal(conn, proposal.id, {1: 50_000}, today=TODAY)
    assert [line.amount_cents for line in result.proposal.lines] == [LEVY_SHARE, 50_000, POT_SHARE]
    assert debts.get_debt(conn, debt, today=TODAY).remaining_cents == 500_000 - 50_000


def test_allocation_fallback_without_debt(conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK4 / 3b: without an open debt the 800 € debt share goes to
    the rule's fallback pot, so confirming puts 800 € + 200 € into "Test reserve"; with the fallback
    "total reserve" the share is reported and books nothing."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)
    assert [(line.kind, line.target, line.amount_cents) for line in proposal.lines[1:]] == [
        ("pot", "Test reserve", DEBT_SHARE),
        ("pot", "Test reserve", POT_SHARE),
    ]

    service.confirm_proposal(conn, proposal.id, today=TODAY)
    assert pots.pot_balance(conn, reserve_pot, TODAY) == DEBT_SHARE + POT_SHARE

    service.update_rule(
        conn,
        proposal.rule_id or 0,
        RuleFields(
            name="Test business income",
            area="business",
            min_amount_cents=50_000,
            targets=(Target("debt_plan", percent_bp=2000),),
            fallback_kind="reserve",
        ),
    )
    _income(conn, giro)
    second = _only_proposal(conn)
    assert [(line.kind, line.books, line.amount_cents) for line in second.lines[1:]] == [("reserve", False, DEBT_SHARE)]
    booked_before = _transaction_count(conn)
    result = service.confirm_proposal(conn, second.id, today=TODAY)
    assert result.transfer_count == 1  # only the levy line books
    assert _transaction_count(conn) == booked_before + 2


# --- open proposals, rejecting, reversal --------------------------------------------------------


def test_allocation_open_seven_days_is_highlighted(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK5 / 4b: a proposal open for seven days is flagged
    ``overdue`` and is still not booked; on day six it is not flagged."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)

    # The age counts from the day the proposal was written, which is the real date of this run,
    # not the fixed TODAY of the scenario — otherwise the test would only pass on 2026-09-19.
    created = date.fromisoformat(proposal.created_at[:10])
    day_six = service.get_proposal(conn, proposal.id, today=created + timedelta(days=6))
    day_seven = service.get_proposal(conn, proposal.id, today=created + timedelta(days=7))
    assert (day_six.days_open, day_six.overdue) == (6, False)
    assert (day_seven.days_open, day_seven.overdue) == (7, True)
    assert day_seven.status == "open"
    assert _transaction_count(conn) == 1


def test_allocation_skip_is_logged_and_counted(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 4a: rejecting logs the proposal as skipped, counts it for
    the month review and books nothing; it is not offered again."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)

    skipped = service.skip_proposal(conn, proposal.id, today=TODAY)
    assert skipped.status == "skipped"
    assert _transaction_count(conn) == 1

    overview = service.overview(conn, today=TODAY)
    assert overview.proposals == []
    assert overview.skipped_this_month == 1
    assert service.skipped_in_month(conn, "2026-08") == 0


def _interpose_after_open_check(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, interloper: Callable[[], object]
) -> list[bool]:
    """Run ``interloper`` once, right after ``conn`` passed the open check outside a transaction.

    That is the gap between the check and ``BEGIN IMMEDIATE`` that a second request (double click,
    retry) on its own connection hits; the hook makes it deterministic instead of timing-based.
    """
    real = service._open_proposal
    fired: list[bool] = []

    def checked(connection: sqlite3.Connection, proposal_id: int) -> sqlite3.Row:
        row = real(connection, proposal_id)
        if connection is conn and not fired and not conn.in_transaction:
            fired.append(True)
            interloper()
        return row

    monkeypatch.setattr(service, "_open_proposal", checked)
    return fired


def test_allocation_confirm_race_books_once(
    conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    giro: int,
    levy_pot: int,
    reserve_pot: int,
    debt: int,
) -> None:
    """Oracle: spec-anchored — confirm_proposal docstring "confirming twice books once" and task
    T-2026-HAR-9130-005: a second confirm that passed the open check before the first one committed
    must raise Conflict and book nothing; one confirm writes the income plus three transfers."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)
    other = connect(tmp_path / "allocation.sqlite3")
    fired = _interpose_after_open_check(
        monkeypatch, conn, lambda: service.confirm_proposal(other, proposal.id, today=TODAY)
    )

    with pytest.raises(service.Conflict):
        service.confirm_proposal(conn, proposal.id, today=TODAY)
    other.close()

    assert fired == [True]
    assert _transaction_count(conn) == 1 + 3 * 2
    assert accounts.balance_at(conn, giro, TODAY) == 500_000 + INCOME_CENTS - LEVY_SHARE - POT_SHARE - DEBT_SHARE
    assert service.get_proposal(conn, proposal.id, today=TODAY).status == "confirmed"


def test_allocation_skip_after_concurrent_confirm_is_refused(
    conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    giro: int,
    levy_pot: int,
    reserve_pot: int,
    debt: int,
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 4a (only an open proposal can be rejected) and task
    T-2026-HAR-9130-005: a skip whose open check ran before a concurrent confirm committed must not
    overwrite the booked proposal with ``skipped``."""
    _rule(conn, reserve_pot)
    _income(conn, giro)
    proposal = _only_proposal(conn)
    other = connect(tmp_path / "allocation.sqlite3")
    fired = _interpose_after_open_check(
        monkeypatch, conn, lambda: service.confirm_proposal(other, proposal.id, today=TODAY)
    )

    with pytest.raises(service.Conflict):
        service.skip_proposal(conn, proposal.id, today=TODAY)
    other.close()

    assert fired == [True]
    assert service.get_proposal(conn, proposal.id, today=TODAY).status == "confirmed"
    assert service.skipped_in_month(conn, "2026-09") == 0


def test_allocation_reserve_warning_reduces_proposal(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 3a hand-computed: with a reserve floor of 7,300 € the free
    amount is 9,000 - 7,300 - 1,200 = 500 EUR, less than the 1,000 € the bookable lines want, so the
    proposal warns and suggests 500 €."""
    accounts.update_account(
        conn, giro, AccountFields("Test business", "business", "giro", 500_000, date(2026, 1, 1), 730_000)
    )
    _rule(conn, reserve_pot)
    _income(conn, giro)

    proposal = _only_proposal(conn)
    assert proposal.free_cents == 50_000
    assert proposal.bookable_cents == DEBT_SHARE + POT_SHARE
    assert proposal.reserve_warning
    assert proposal.reduced_total_cents == 50_000


def test_allocation_cancelled_income_suggests_reversal(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — UC-ZF-014 2a: deleting the income of a confirmed proposal suggests its
    three bookings for reversal and reverses nothing; once the bookings are gone, so is the
    suggestion."""
    _rule(conn, reserve_pot)
    income_id = _income(conn, giro)
    proposal = _only_proposal(conn)
    service.confirm_proposal(conn, proposal.id, today=TODAY)
    booked = _transaction_count(conn)

    transactions.delete_transaction(conn, income_id)
    overview = service.overview(conn, today=TODAY)
    assert len(overview.reversals) == 1
    reversal = overview.reversals[0]
    assert reversal.proposal_id == proposal.id
    assert reversal.booked_cents == LEVY_SHARE + DEBT_SHARE + POT_SHARE
    assert len(reversal.transfer_group_ids) == 3
    assert _transaction_count(conn) == booked - 1  # only the income is gone

    for group in reversal.transfer_group_ids:
        transactions.delete_transaction(conn, transactions.get_transfer(conn, group)[0].id)
    assert service.overview(conn, today=TODAY).reversals == []


def test_allocation_proposal_sources_and_idempotency(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — task -025 "proposal hook on a booked matching income from entry,
    import or receipts" and the contract sketch: an income written by the import and one by the
    receipt agent both get a proposal, a second sweep writes none, and a private income or a
    transfer leg gets none."""
    private = accounts.create_account(
        conn, AccountFields("Test private", "private", "giro", 100_000, date(2026, 1, 1))
    ).id
    _rule(conn, reserve_pot)
    for source in ("import", "receipt"):
        booking_id = _income(conn, giro)
        conn.execute('UPDATE "transaction" SET source = ? WHERE id = ?', (source, booking_id))
    _income(conn, private)
    transactions.create_transfer(
        conn, transactions.TransferFields(TODAY, 100_000, private, giro, None, "Test owner draw"), today=TODAY
    )

    assert service.sync_proposals(conn, today=TODAY) == 2
    assert service.sync_proposals(conn, today=TODAY) == 0
    assert len(service.overview(conn, today=TODAY).proposals) == 2


def test_allocation_rule_only_acts_on_later_income(
    conn: sqlite3.Connection, giro: int, levy_pot: int, reserve_pot: int, debt: int
) -> None:
    """Oracle: spec-anchored — SPEC "Proposals are created by a sweep": an income booked before the
    rule existed gets no proposal, so switching a rule on does not propose the whole past."""
    old_income = _income(conn, giro, on=date(2026, 8, 1))
    conn.execute('UPDATE "transaction" SET created_at = ? WHERE id = ?', ("2026-08-01T10:00:00+00:00", old_income))
    _rule(conn, reserve_pot)

    assert service.sync_proposals(conn, today=TODAY) == 0
    _income(conn, giro)
    assert service.sync_proposals(conn, today=TODAY) == 1


# --- HTTP --------------------------------------------------------------------------------------


def test_allocation_api_roundtrip(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC AK "HTTP round trip": rules can be created, changed and deleted,
    the overview carries the proposal of a booked income, confirming books and skipping does not,
    and a float amount is refused with 422."""
    giro = client.post(
        "/api/accounts",
        json={
            "name": "Test business",
            "area": "business",
            "type": "giro",
            "opening_balance_cents": 500_000,
            "opening_date": "2026-01-01",
        },
    ).json()["id"]
    pot = client.post("/api/pots", json={"name": "Test reserve", "account_id": giro}).json()["id"]
    rule = client.post(
        "/api/allocation/rules",
        json={
            "name": "Test business income",
            "area": "business",
            "min_amount_cents": 50_000,
            "targets": [{"kind": "pot", "pot_id": pot, "percent_bp": 500}],
            "fallback_kind": "reserve",
        },
    )
    assert rule.status_code == 201, rule.text
    rule_id = rule.json()["id"]

    assert client.post("/api/allocation/rules", json={"name": "x", "targets": []}).status_code == 422
    assert (
        client.post(
            "/api/allocation/rules",
            json={"name": "Test", "targets": [{"kind": "reserve", "fixed_cents": 10.5}]},
        ).status_code
        == 422
    )
    changed = client.put(f"/api/allocation/rules/{rule_id}", json={**rule.json(), "min_amount_cents": 100_000})
    assert changed.status_code == 200
    assert changed.json()["min_amount_cents"] == 100_000

    today = date.today().isoformat()
    booking = client.post(
        "/api/transactions",
        json={"date": today, "amount_cents": INCOME_CENTS, "account_id": giro, "text": "Test client fee"},
    )
    assert booking.status_code == 201, booking.text

    overview = client.get("/api/allocation").json()
    assert len(overview["proposals"]) == 1
    proposal = overview["proposals"][0]
    assert proposal["lines"] == [
        {
            "kind": "pot",
            "amount_cents": POT_SHARE,
            "floor_cents": 0,
            "capped": False,
            "target": "Test reserve",
            "pot_id": pot,
            "debt_id": None,
            "editable": True,
            "books": True,
        }
    ]

    confirmed = client.post(f"/api/allocation/proposals/{proposal['id']}/confirm", json={"amounts": {}})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["transfer_count"] == 0  # the pot sits on the income's account
    assert client.post(f"/api/allocation/proposals/{proposal['id']}/skip").status_code == 409
    assert client.get("/api/pots").json()["pots"][0]["balance_cents"] == POT_SHARE
    assert client.delete(f"/api/allocation/rules/{rule_id}").status_code == 204
    assert client.get("/api/allocation/rules").json() == []
