"""Savings pots (UC-ZF-011) as plain functions over a connection.

A pot is a virtual subdivision of one real account (UC assumption: never across accounts). It
holds no money of its own: its **balance** is the sum of its allocations (table
``pot_allocation``), and the account's **free balance** is the account balance minus the balances
of all its pots. The rules without a database — monthly rate, linear plan status, emergency split,
shifted target date — are pure functions below, tested on their own.

* **Allocations.** A deposit from the free balance is refused above the free balance (UC 3a); a
  deposit from another account is a transfer of the transactions service (-008) whose incoming leg
  the allocation points to. Moving between two pots of one account is a pair of allocations and no
  booking (UC 3b). An expense on the account assigned to a pot lowers the pot by the part it covers
  (UC step 5, 5a); the account balance falls through the booking itself.
* **Savings plan** = a monthly recurring transfer of the recurring engine (-009) into the pot's
  account with ``recurring.pot_id`` set (UC step 2). Its booked incoming legs count towards the
  pot's balance as soon as they are booked (confirmation -011, import -012), even before
  ``collect_deposits`` turns them into allocation rows; collecting makes them survive a deletion of
  the recurring item, which unlinks booked rows.
* **Planned expense** (``plan_expense``): the target amount as a planned booking on the target
  date, kept in line by ``_sync_planned_expense`` (UC-ZF-006 2d reads it like any planned row).
* **Emergency order.** Open, unlocked pots carry a rank unique per account (1 = taken first);
  locked pots have none and are never proposed (UC 5b). ``emergency_split`` empties rank 1 before
  touching rank 2.

``free_balance`` is the contract for the levy reserve (-019), the allocation rules (-025) and the
payoff plan (-026): their "free amount net of the levy gap" starts from it.

Errors are ``PotsError`` subclasses carrying an ``N_()`` msgid; ``AccountsError``,
``TransactionsError`` and ``RecurringError`` from the services called here pass through.
"""

# rule-waiver: SIZE-01 module was over the cap before this fix; splitting it is a separate refactor
from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from zfinance.db import transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import Area
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.schedule import add_months
from zfinance.domain.recurring.service import RecurringFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransferFields
from zfinance.i18n import N_
from zfinance.money import from_cents

PotStatus = Literal["on_track", "behind", "reached"]
AllocationKind = Literal["deposit", "withdraw", "transfer_in", "transfer_out", "emergency"]


# --- errors ------------------------------------------------------------------------------------


class PotsError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(PotsError):
    """The pot does not exist."""

    status_code = 404


class Conflict(PotsError):
    """The request contradicts the state of the pot or its account."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PotFields:
    """What the user enters for a pot (create and full update).

    ``area`` ``None`` takes the account's area. ``plan_expense`` needs a target amount and a target
    date. A locked pot has no emergency rank; an unlocked one gets the next free rank.
    """

    name: str
    account_id: int
    area: Area | None = None
    target_amount_cents: int | None = None
    target_date: date | None = None
    plan_expense: bool = False
    locked: bool = False
    is_levy_pot: bool = False


@dataclass(frozen=True, kw_only=True)
class Pot(PotFields):
    """A stored pot with its figures on ``today``.

    ``monthly_rate_cents`` and ``status`` need a target amount and a target date (UC 1a, 1b; a
    reached target is ``reached`` either way). ``expected_reach`` comes from the savings plan.
    """

    id: int
    emergency_rank: int | None
    plan_start: date
    planned_transaction_id: int | None
    closed_at: str | None
    created_at: str
    balance_cents: int
    progress_pct: int | None
    monthly_rate_cents: int | None
    status: PotStatus | None
    expected_balance_cents: int | None
    savings_plan_id: int | None
    savings_plan_amount_cents: int | None
    expected_reach: date | None


@dataclass(frozen=True)
class AccountPots:
    """One account's balance split: sum of pot balances, free balance, "available in an emergency"."""

    account_id: int
    balance_cents: int
    pots_cents: int
    free_cents: int
    emergency_available_cents: int


@dataclass(frozen=True)
class Overview:
    """Pots (open first, per account in emergency order) and the split of every account with pots."""

    pots: list[Pot]
    accounts: list[AccountPots]


@dataclass(frozen=True)
class Allocation:
    """One movement of a pot's balance (history)."""

    id: int
    pot_id: int
    date: date
    amount_cents: int
    kind: AllocationKind
    transaction_id: int | None
    transfer_group_id: str | None
    memo: str | None


@dataclass(frozen=True)
class ExpenseResult:
    """An expense assigned to a pot: the pot afterwards, the part it covered, the part from the free balance."""

    pot: Pot
    covered_cents: int
    from_free_cents: int


@dataclass(frozen=True)
class EmergencyCandidate:
    """Input of ``emergency_split``: an open pot with its rank (``None`` = not in the order) and balance."""

    pot_id: int
    rank: int | None
    locked: bool
    balance_cents: int


@dataclass(frozen=True)
class EmergencyLine:
    """One pot of an emergency proposal: what is taken, the new balance and the shifted target date."""

    pot_id: int
    name: str
    rank: int
    amount_cents: int
    new_balance_cents: int
    target_date: date | None
    shifted_target_date: date | None


@dataclass(frozen=True)
class EmergencyProposal:
    """The proposal for an emergency amount on one account; ``shortfall_cents`` = not covered by pots."""

    account_id: int
    amount_cents: int
    lines: list[EmergencyLine]
    covered_cents: int
    shortfall_cents: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


# --- pure rules --------------------------------------------------------------------------------


def months_until(today: date, target: date) -> int:
    """Monthly payments from the month after ``today`` up to the target month; at least 1."""
    return max(1, (target.year * 12 + target.month) - (today.year * 12 + today.month))


def monthly_rate(target_cents: int, balance_cents: int, target_date: date, today: date) -> int:
    """Rate per month that reaches the target by the target date (UC step 2: (target - balance) ÷ months).

    Whole cents rounded up, so the target is met; 0 once the balance reaches the target. A target
    date in the current month or earlier leaves one payment (the whole rest). AK1: 3,000 € target
    in 10 months with 500 € → 250 €.
    """
    rest = target_cents - balance_cents
    if rest <= 0:
        return 0
    return -(-rest // months_until(today, target_date))


def expected_balance(base_cents: int, target_cents: int, plan_start: date, target_date: date, on: date) -> int:
    """Balance the linear plan expects on ``on``: from ``base_cents`` at ``plan_start`` to the target.

    Linear in days, floored to cents, clamped to the plan's days; a plan without days (target
    date on or before the start) expects the target.
    """
    total = (target_date - plan_start).days
    if total <= 0:
        return target_cents
    elapsed = min(max((on - plan_start).days, 0), total)
    return base_cents + (target_cents - base_cents) * elapsed // total


def plan_status(
    balance_cents: int,
    target_cents: int | None,
    target_date: date | None,
    base_cents: int,
    plan_start: date,
    today: date,
) -> PotStatus | None:
    """``reached`` at or above the target, else ``behind`` below the linear expectation, else ``on_track``.

    Without a target amount there is no status; without a target date only ``reached`` (UC 1a).
    """
    if target_cents is None:
        return None
    if balance_cents >= target_cents:
        return "reached"
    if target_date is None:
        return None
    expected = expected_balance(base_cents, target_cents, plan_start, target_date, today)
    return "behind" if balance_cents < expected else "on_track"


def emergency_split(amount_cents: int, candidates: Sequence[EmergencyCandidate]) -> list[tuple[int, int]]:
    """Distribute an emergency amount over the pots in rank order: (pot id, amount taken).

    Rank 1 is emptied before rank 2 is touched (UC assumption: an order, not a key); locked pots
    and pots without a rank are never taken, nor are empty ones. Pots stop once the amount is
    covered; what the pots cannot cover is ``amount - sum(taken)``. AK6: TV 300 (1), holiday 900
    (2), PC 600 (3), emergency fund 1,000 (locked), 500 → [(TV, 300), (holiday, 200)].
    """
    ranked = sorted((c for c in candidates if not c.locked and c.rank is not None), key=lambda c: c.rank or 0)
    rest = amount_cents
    lines: list[tuple[int, int]] = []
    for candidate in ranked:
        if rest <= 0:
            break
        take = min(rest, candidate.balance_cents)
        if take > 0:
            lines.append((candidate.pot_id, take))
            rest -= take
    return lines


def shifted_target_date(target_date: date, taken_cents: int, rate_cents: int) -> date:
    """Target date after taking ``taken_cents`` at an unchanged monthly rate: one month per rate, rounded up."""
    return add_months(target_date, -(-taken_cents // rate_cents))


def projected_reach(rest_cents: int, rate_cents: int, next_due: date) -> date:
    """Due date of the monthly instalment that closes ``rest_cents`` (first instalment on ``next_due``)."""
    return add_months(next_due, -(-rest_cents // rate_cents) - 1)


# --- balances ----------------------------------------------------------------------------------

# Booked incoming legs of a pot's savings plan that are not an allocation yet (see collect_deposits).
_UNCOLLECTED = (
    'SELECT t.id, t.amount_cents, t.date, r.pot_id FROM "transaction" t'
    " JOIN recurring r ON r.id = t.recurring_id"
    " JOIN pot p ON p.id = r.pot_id AND p.account_id = t.account_id"
    " WHERE t.status = 'booked' AND t.amount_cents > 0"
    " AND NOT EXISTS (SELECT 1 FROM pot_allocation a WHERE a.transaction_id = t.id)"
)


def pot_balance(conn: sqlite3.Connection, pot_id: int, on_date: date) -> int:
    """A pot's balance at the end of ``on_date``: its allocations plus uncollected savings-plan deposits."""
    on = on_date.isoformat()
    row = conn.execute(
        "SELECT (SELECT coalesce(sum(amount_cents), 0) FROM pot_allocation WHERE pot_id = ? AND date <= ?)"  # noqa: S608
        f" + (SELECT coalesce(sum(u.amount_cents), 0) FROM ({_UNCOLLECTED}) u WHERE u.pot_id = ? AND u.date <= ?)",  # nosec B608 — constant
        (pot_id, on, pot_id, on),
    ).fetchone()
    return int(row[0])


def pots_total(conn: sqlite3.Connection, account_id: int, on_date: date) -> int:
    """Sum of the balances of all pots on the account at the end of ``on_date``."""
    on = on_date.isoformat()
    row = conn.execute(
        "SELECT (SELECT coalesce(sum(a.amount_cents), 0) FROM pot_allocation a JOIN pot p ON p.id = a.pot_id"  # noqa: S608
        "        WHERE p.account_id = ? AND a.date <= ?)"
        f" + (SELECT coalesce(sum(u.amount_cents), 0) FROM ({_UNCOLLECTED}) u"  # nosec B608 — constant
        "     JOIN pot p ON p.id = u.pot_id WHERE p.account_id = ? AND u.date <= ?)",
        (account_id, on, account_id, on),
    ).fetchone()
    return int(row[0])


def free_balance(conn: sqlite3.Connection, account_id: int, on_date: date) -> int:
    """The account's free balance at the end of ``on_date``: ``accounts.balance_at`` - Σ pot balances.

    Contract for the levy reserve (-019), the allocation rules (-025) and the payoff plan (-026):
    money not set aside in any pot of the account. Pure read (uncollected savings-plan deposits
    already count, see ``pot_balance``); it can be negative when the account balance falls below
    what its pots hold (an expense not assigned to a pot).
    """
    return accounts.balance_at(conn, account_id, on_date) - pots_total(conn, account_id, on_date)


def collect_deposits(conn: sqlite3.Connection) -> int:
    """Turn booked savings-plan deposits into allocation rows; return how many were written.

    Idempotent. Runs before every pot read and write here; confirmation (-011) and import (-012)
    may call it after booking. Once collected, a deposit stays with the pot even if its recurring
    item is deleted later (which unlinks booked rows).
    """
    rows = conn.execute(_UNCOLLECTED).fetchall()
    if not rows:
        return 0
    with transaction(conn):
        for row in rows:
            conn.execute(
                "INSERT INTO pot_allocation (pot_id, transaction_id, amount_cents, date, kind, created_at)"
                " VALUES (?, ?, ?, ?, 'deposit', ?)",
                (row["pot_id"], row["id"], row["amount_cents"], row["date"], _now()),
            )
    return len(rows)


# --- reading -----------------------------------------------------------------------------------


def _row(conn: sqlite3.Connection, pot_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM pot WHERE id = ?", (pot_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Pot not found."))
    return row


def _savings_plan(conn: sqlite3.Connection, pot_id: int) -> recurring.Recurring | None:
    row = conn.execute("SELECT id FROM recurring WHERE pot_id = ? ORDER BY id LIMIT 1", (pot_id,)).fetchone()
    return None if row is None else recurring.get_recurring(conn, row["id"])


def _pot(conn: sqlite3.Connection, row: sqlite3.Row, today: date) -> Pot:
    balance = pot_balance(conn, row["id"], today)
    target = row["target_amount_cents"]
    target_date = _day(row["target_date"])
    plan_start = date.fromisoformat(row["plan_start"])
    is_open = row["closed_at"] is None
    plan = _savings_plan(conn, row["id"])
    rate = expected = reach = status = None
    if is_open:
        base = pot_balance(conn, row["id"], plan_start)
        status = plan_status(balance, target, target_date, base, plan_start, today)
        if target is not None and target_date is not None:
            rate = monthly_rate(target, balance, target_date, today)
            expected = expected_balance(base, target, plan_start, target_date, today)
        if target is not None and balance < target and plan is not None and plan.active and plan.next_due_date:
            reach = projected_reach(target - balance, plan.amount_cents, plan.next_due_date)
    return Pot(
        id=row["id"],
        name=row["name"],
        account_id=row["account_id"],
        area=row["area"],
        target_amount_cents=target,
        target_date=target_date,
        plan_expense=bool(row["plan_expense"]),
        locked=bool(row["locked"]),
        is_levy_pot=bool(row["is_levy_pot"]),
        emergency_rank=row["emergency_rank"],
        plan_start=plan_start,
        planned_transaction_id=row["planned_transaction_id"],
        closed_at=row["closed_at"],
        created_at=row["created_at"],
        balance_cents=balance,
        progress_pct=None if target is None else max(balance, 0) * 100 // target,
        monthly_rate_cents=rate,
        status=status,
        expected_balance_cents=expected,
        savings_plan_id=None if plan is None else plan.id,
        savings_plan_amount_cents=None if plan is None else plan.amount_cents,
        expected_reach=reach,
    )


def get_pot(conn: sqlite3.Connection, pot_id: int, *, today: date | None = None) -> Pot:
    """Return the pot with its figures or raise ``NotFound``."""
    collect_deposits(conn)
    return _pot(conn, _row(conn, pot_id), today or date.today())


def _account_split(conn: sqlite3.Connection, account_id: int, today: date) -> AccountPots:
    balance = accounts.balance_at(conn, account_id, today)
    in_pots = pots_total(conn, account_id, today)
    available = sum(
        max(pot_balance(conn, row["id"], today), 0)
        for row in conn.execute(
            "SELECT id FROM pot WHERE account_id = ? AND closed_at IS NULL AND locked = 0", (account_id,)
        ).fetchall()
    )
    return AccountPots(account_id, balance, in_pots, balance - in_pots, available)


def overview(conn: sqlite3.Connection, *, account_id: int | None = None, today: date | None = None) -> Overview:
    """All pots (open first, by account and rank, locked after ranked) and each account's split (UC step 4)."""
    today = today or date.today()
    collect_deposits(conn)
    where, params = ("WHERE account_id = ?", (account_id,)) if account_id is not None else ("", ())
    rows = conn.execute(
        f"SELECT * FROM pot {where} ORDER BY closed_at IS NOT NULL, account_id,"  # noqa: S608  # nosec B608 — constant
        " emergency_rank IS NULL, emergency_rank, name COLLATE NOCASE, id",
        params,
    ).fetchall()
    pots = [_pot(conn, row, today) for row in rows]
    account_ids = sorted({pot.account_id for pot in pots})
    return Overview(pots=pots, accounts=[_account_split(conn, a, today) for a in account_ids])


def allocations(conn: sqlite3.Connection, pot_id: int) -> list[Allocation]:
    """The pot's allocations, newest first (deposits, withdrawals, moves, expenses, emergencies)."""
    _row(conn, pot_id)
    collect_deposits(conn)
    rows = conn.execute("SELECT * FROM pot_allocation WHERE pot_id = ? ORDER BY date DESC, id DESC", (pot_id,))
    return [
        Allocation(
            id=row["id"],
            pot_id=row["pot_id"],
            date=date.fromisoformat(row["date"]),
            amount_cents=row["amount_cents"],
            kind=row["kind"],
            transaction_id=row["transaction_id"],
            transfer_group_id=row["transfer_group_id"],
            memo=row["memo"],
        )
        for row in rows
    ]


# --- pots --------------------------------------------------------------------------------------


def _check(conn: sqlite3.Connection, fields: PotFields) -> PotFields:
    """Validate and normalize the fields (name trimmed, area from the account when not given)."""
    name = fields.name.strip()
    if not name:
        raise PotsError(N_("Enter a name for the pot."))
    account = accounts.get_account(conn, fields.account_id)
    if account.archived_at is not None:
        raise Conflict(N_("The account %(name)s is archived."), name=account.name)
    if account.type == "liability":
        raise Conflict(N_("A pot cannot be on a liability account."))
    area = fields.area or account.area
    if area not in accounts.AREAS:
        raise PotsError(N_("Choose private or business."))
    if fields.target_amount_cents is not None and fields.target_amount_cents <= 0:
        raise PotsError(N_("Enter a target amount greater than 0."))
    if fields.plan_expense and (fields.target_amount_cents is None or fields.target_date is None):
        raise PotsError(N_("A planned expense needs a target amount and a target date."))
    return PotFields(
        name=name,
        account_id=fields.account_id,
        area=area,
        target_amount_cents=fields.target_amount_cents,
        target_date=fields.target_date,
        plan_expense=fields.plan_expense,
        locked=fields.locked,
        is_levy_pot=fields.is_levy_pot,
    )


def _open_row(conn: sqlite3.Connection, pot_id: int) -> sqlite3.Row:
    row = _row(conn, pot_id)
    if row["closed_at"] is not None:
        raise Conflict(N_("The pot %(name)s is closed."), name=row["name"])
    return row


def _next_rank(conn: sqlite3.Connection, account_id: int) -> int:
    row = conn.execute("SELECT coalesce(max(emergency_rank), 0) FROM pot WHERE account_id = ?", (account_id,))
    return int(row.fetchone()[0]) + 1


def _renumber(conn: sqlite3.Connection, account_id: int, order: Sequence[int]) -> None:
    """Give the pots in ``order`` the ranks 1..n (cleared first: the index is unique per account)."""
    conn.execute(
        "UPDATE pot SET emergency_rank = NULL WHERE account_id = ? AND emergency_rank IS NOT NULL", (account_id,)
    )
    for rank, pot_id in enumerate(order, start=1):
        conn.execute("UPDATE pot SET emergency_rank = ? WHERE id = ?", (rank, pot_id))


def _ranked_ids(conn: sqlite3.Connection, account_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM pot WHERE account_id = ? AND emergency_rank IS NOT NULL ORDER BY emergency_rank",
        (account_id,),
    )
    return [row["id"] for row in rows]


def _sync_planned_expense(conn: sqlite3.Connection, row: sqlite3.Row, today: date) -> None:
    """Keep the planned expense of a pot equal to its target (UC step 1 "plan the expense", UC-ZF-006 2d).

    Wanted: an open pot with ``plan_expense``, a target date after today and a target not reached
    yet. The planned row is created, rewritten or deleted through the transactions service; a row
    that became booked meanwhile (confirmed as the real purchase) is left alone and unlinked.
    """
    wanted = (
        row["closed_at"] is None
        and row["plan_expense"]
        and row["target_date"] is not None
        and date.fromisoformat(row["target_date"]) > today
    )
    planned_id = row["planned_transaction_id"]
    existing = None
    if planned_id is not None:
        existing = conn.execute('SELECT * FROM "transaction" WHERE id = ?', (planned_id,)).fetchone()
        if existing is not None and existing["status"] != "planned":
            existing = None
            conn.execute("UPDATE pot SET planned_transaction_id = NULL WHERE id = ?", (row["id"],))
    if not wanted:
        if existing is not None:
            transactions.delete_transaction(conn, existing["id"])
        return
    fields = BookingFields(
        date.fromisoformat(row["target_date"]), -row["target_amount_cents"], row["account_id"], None, row["name"]
    )
    if existing is None:
        created = transactions.create_transaction(conn, fields, today=today)
        conn.execute("UPDATE pot SET planned_transaction_id = ? WHERE id = ?", (created.id, row["id"]))
    elif (existing["date"], existing["amount_cents"], existing["text"]) != (
        fields.date.isoformat(),
        fields.amount_cents,
        fields.text,
    ):
        transactions.update_transaction(conn, existing["id"], fields, today=today)


def create_pot(conn: sqlite3.Connection, fields: PotFields, *, today: date | None = None) -> Pot:
    """Create a pot (UC step 1); unlocked pots join the end of the account's emergency order."""
    today = today or date.today()
    fields = _check(conn, fields)
    with transaction(conn):
        rank = None if fields.locked else _next_rank(conn, fields.account_id)
        row = conn.execute(
            "INSERT INTO pot (name, account_id, area, target_amount_cents, target_date, plan_start, plan_expense,"
            " emergency_rank, locked, is_levy_pot, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                fields.name,
                fields.account_id,
                fields.area,
                fields.target_amount_cents,
                _iso(fields.target_date),
                today.isoformat(),
                int(fields.plan_expense),
                rank,
                int(fields.locked),
                int(fields.is_levy_pot),
                _now(),
            ),
        ).fetchone()
        pot_id = int(row["id"])
        _sync_planned_expense(conn, _row(conn, pot_id), today)
    return get_pot(conn, pot_id, today=today)


def update_pot(conn: sqlite3.Connection, pot_id: int, fields: PotFields, *, today: date | None = None) -> Pot:
    """Replace a pot's fields. The account cannot change.

    A changed target amount or date restarts the linear plan today. Locking takes the pot out of
    the emergency order (the others close up), unlocking appends it at the end. The planned
    expense and the savings plan's end date follow.
    """
    today = today or date.today()
    row = _open_row(conn, pot_id)
    if fields.account_id != row["account_id"]:
        raise Conflict(N_("A pot cannot move to another account."))
    fields = _check(conn, fields)
    plan_start = row["plan_start"]
    if (fields.target_amount_cents, _iso(fields.target_date)) != (row["target_amount_cents"], row["target_date"]):
        plan_start = today.isoformat()
    with transaction(conn):
        conn.execute(
            "UPDATE pot SET name = ?, area = ?, target_amount_cents = ?, target_date = ?, plan_start = ?,"
            " plan_expense = ?, is_levy_pot = ? WHERE id = ?",
            (
                fields.name,
                fields.area,
                fields.target_amount_cents,
                _iso(fields.target_date),
                plan_start,
                int(fields.plan_expense),
                int(fields.is_levy_pot),
                pot_id,
            ),
        )
        if fields.locked != bool(row["locked"]):
            order = [i for i in _ranked_ids(conn, row["account_id"]) if i != pot_id]
            if not fields.locked:
                order.append(pot_id)
            conn.execute("UPDATE pot SET locked = ?, emergency_rank = NULL WHERE id = ?", (int(fields.locked), pot_id))
            _renumber(conn, row["account_id"], order)
        _sync_planned_expense(conn, _row(conn, pot_id), today)
        plan = _savings_plan(conn, pot_id)
        if plan is not None and (plan.end_date, plan.description) != (fields.target_date, fields.name):
            _write_plan(conn, pot_id, plan.account_id, plan.amount_cents, plan.start_date, fields.target_date, today)
    return get_pot(conn, pot_id, today=today)


def set_ranks(
    conn: sqlite3.Connection, account_id: int, pot_ids: Sequence[int], *, today: date | None = None
) -> Overview:
    """Set the emergency order of an account (UC step 4 "drag to change", AK7).

    ``pot_ids`` must be exactly the account's open, unlocked pots, first = rank 1.
    """
    current = _ranked_ids(conn, account_id)
    if sorted(pot_ids) != sorted(current) or len(set(pot_ids)) != len(pot_ids):
        raise PotsError(N_("List every open, unlocked pot of the account exactly once."))
    with transaction(conn):
        _renumber(conn, account_id, pot_ids)
    return overview(conn, account_id=account_id, today=today)


def _end_plan(conn: sqlite3.Connection, pot_id: int) -> None:
    """Collect the plan's booked deposits, then delete its recurring item (planned rows go)."""
    collect_deposits(conn)
    plan = _savings_plan(conn, pot_id)
    if plan is not None:
        recurring.delete_recurring(conn, plan.id)


def close_pot(conn: sqlite3.Connection, pot_id: int, *, today: date | None = None) -> Pot:
    """Close a pot (UC step 6): the rest flows back to the free balance, plan and planned expense go."""
    today = today or date.today()
    row = _open_row(conn, pot_id)
    with transaction(conn):
        _end_plan(conn, pot_id)
        balance = pot_balance(conn, pot_id, date.max)
        if balance > 0:
            _allocate(conn, pot_id, -balance, today, "withdraw")
        conn.execute("UPDATE pot SET closed_at = ?, emergency_rank = NULL WHERE id = ?", (_now(), pot_id))
        _renumber(conn, row["account_id"], [i for i in _ranked_ids(conn, row["account_id"]) if i != pot_id])
        _sync_planned_expense(conn, _row(conn, pot_id), today)
    return get_pot(conn, pot_id, today=today)


def delete_pot(conn: sqlite3.Connection, pot_id: int, *, today: date | None = None) -> None:
    """Delete a pot with its allocations (its money returns to the free balance); plan and planned expense go."""
    today = today or date.today()
    row = _row(conn, pot_id)
    with transaction(conn):
        _end_plan(conn, pot_id)
        conn.execute(
            "UPDATE pot SET closed_at = coalesce(closed_at, ?), emergency_rank = NULL WHERE id = ?", (_now(), pot_id)
        )
        _sync_planned_expense(conn, _row(conn, pot_id), today)
        conn.execute("DELETE FROM pot WHERE id = ?", (pot_id,))
        _renumber(conn, row["account_id"], _ranked_ids(conn, row["account_id"]))


# --- allocations -------------------------------------------------------------------------------


def _allocate(
    conn: sqlite3.Connection,
    pot_id: int,
    amount_cents: int,
    on: date,
    kind: AllocationKind,
    *,
    transaction_id: int | None = None,
    group_id: str | None = None,
    memo: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO pot_allocation (pot_id, transaction_id, amount_cents, date, kind, transfer_group_id, memo,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pot_id, transaction_id, amount_cents, on.isoformat(), kind, group_id, memo, _now()),
    )


def _check_amount_and_date(
    conn: sqlite3.Connection, row: sqlite3.Row, amount_cents: int, on: date, today: date
) -> None:
    if amount_cents <= 0:
        raise PotsError(N_("Enter an amount greater than 0."))
    if on > today:
        raise PotsError(N_("A pot movement cannot be dated in the future."))
    accounts.ensure_bookable(conn, row["account_id"], on)


def _check_available(conn: sqlite3.Connection, row: sqlite3.Row, amount_cents: int, on: date) -> None:
    """Refuse taking more than the pot holds on ``on`` (and from today on, so no later day goes negative)."""
    balance = min(pot_balance(conn, row["id"], on), pot_balance(conn, row["id"], date.max))
    if amount_cents > balance:
        raise Conflict(
            N_("The pot %(name)s holds only %(amount)s €."), name=row["name"], amount=str(from_cents(max(balance, 0)))
        )


def _check_not_assigned(conn: sqlite3.Connection, transaction_id: int) -> None:
    if conn.execute("SELECT 1 FROM pot_allocation WHERE transaction_id = ?", (transaction_id,)).fetchone():
        raise Conflict(N_("This expense is already assigned to a pot."))


def _check_unlocked(row: sqlite3.Row, confirm_locked: bool) -> None:
    if row["locked"] and not confirm_locked:
        raise Conflict(N_("The pot %(name)s is locked. Confirm to take money from it."), name=row["name"])


def deposit(
    conn: sqlite3.Connection,
    pot_id: int,
    amount_cents: int,
    *,
    on: date | None = None,
    from_account_id: int | None = None,
    memo: str | None = None,
    today: date | None = None,
) -> Pot:
    """Put money into a pot (UC step 3).

    Without ``from_account_id`` the amount moves from the account's free balance and is refused
    above it, naming the free balance (UC 3a, AK2). With it, a transfer from that account into the
    pot's account is booked and the pot gets its incoming leg (no free-balance check: the money
    arrives with it).
    """
    today = today or date.today()
    on = on or today
    collect_deposits(conn)
    row = _open_row(conn, pot_id)
    _check_amount_and_date(conn, row, amount_cents, on, today)
    with transaction(conn):
        if from_account_id is None:
            free = min(free_balance(conn, row["account_id"], on), free_balance(conn, row["account_id"], date.max))
            if amount_cents > free:
                raise Conflict(
                    N_("Only %(amount)s € of the account are free; the pot cannot get more."),
                    amount=str(from_cents(max(free, 0))),
                )
            _allocate(conn, pot_id, amount_cents, on, "deposit", memo=memo)
        else:
            if from_account_id == row["account_id"]:
                raise Conflict(N_("Choose another account than the pot's account to pay in from."))
            _, incoming = transactions.create_transfer(
                conn,
                TransferFields(on, amount_cents, from_account_id, row["account_id"], None, row["name"], memo),
                today=today,
            )
            _allocate(conn, pot_id, amount_cents, on, "deposit", transaction_id=incoming.id, memo=memo)
    return get_pot(conn, pot_id, today=today)


def withdraw(
    conn: sqlite3.Connection,
    pot_id: int,
    amount_cents: int,
    *,
    on: date | None = None,
    confirm_locked: bool = False,
    memo: str | None = None,
    today: date | None = None,
) -> Pot:
    """Release money from a pot back to the account's free balance (no booking).

    A locked pot gives money only with ``confirm_locked`` (UC 5c: after an explicit question); such a
    withdrawal is logged as ``emergency``. The pot stays locked.
    """
    today = today or date.today()
    on = on or today
    collect_deposits(conn)
    row = _open_row(conn, pot_id)
    _check_amount_and_date(conn, row, amount_cents, on, today)
    _check_unlocked(row, confirm_locked)
    _check_available(conn, row, amount_cents, on)
    with transaction(conn):
        _check_available(conn, row, amount_cents, on)  # again under the write lock: the check above may be stale
        _allocate(conn, pot_id, -amount_cents, on, "emergency" if row["locked"] else "withdraw", memo=memo)
    return get_pot(conn, pot_id, today=today)


def move(
    conn: sqlite3.Connection,
    from_pot_id: int,
    to_pot_id: int,
    amount_cents: int,
    *,
    on: date | None = None,
    confirm_locked: bool = False,
    today: date | None = None,
) -> tuple[Pot, Pot]:
    """Move money between two pots of one account (UC 3b, AK5): no booking, the account balance stays."""
    today = today or date.today()
    on = on or today
    collect_deposits(conn)
    source = _open_row(conn, from_pot_id)
    target = _open_row(conn, to_pot_id)
    if from_pot_id == to_pot_id:
        raise Conflict(N_("Choose two different pots."))
    if source["account_id"] != target["account_id"]:
        raise Conflict(N_("Money can only move between pots of the same account."))
    _check_amount_and_date(conn, source, amount_cents, on, today)
    _check_unlocked(source, confirm_locked)
    _check_available(conn, source, amount_cents, on)
    group = uuid.uuid4().hex
    with transaction(conn):
        _check_available(conn, source, amount_cents, on)  # again under the write lock: the check above may be stale
        _allocate(conn, from_pot_id, -amount_cents, on, "transfer_out", group_id=group)
        _allocate(conn, to_pot_id, amount_cents, on, "transfer_in", group_id=group)
    return get_pot(conn, from_pot_id, today=today), get_pot(conn, to_pot_id, today=today)


def assign_expense(
    conn: sqlite3.Connection,
    pot_id: int,
    transaction_id: int,
    *,
    confirm_locked: bool = False,
    today: date | None = None,
) -> ExpenseResult:
    """Assign an expense on the pot's account to the pot (UC step 5, AK4).

    The pot covers the expense up to its balance; the rest comes from the free balance and is
    reported (UC 5a). The booking itself lowers the account balance, so pot and account fall by the
    covered amount alike. The pot's planned expense goes: the purchase happened.
    """
    today = today or date.today()
    collect_deposits(conn)
    row = _open_row(conn, pot_id)
    booking = transactions.get_transaction(conn, transaction_id)
    if booking.account_id != row["account_id"] or booking.amount_cents >= 0 or booking.transfer_group_id:
        raise Conflict(N_("Only an expense on the pot's account can be assigned to the pot."))
    if booking.status != "booked":
        raise Conflict(N_("Only a booked expense can be assigned to a pot."))
    _check_not_assigned(conn, transaction_id)
    _check_unlocked(row, confirm_locked)
    expense = -booking.amount_cents
    with transaction(conn):
        _check_not_assigned(conn, transaction_id)  # again under the write lock: the check above may be stale
        covered = min(
            expense, max(0, min(pot_balance(conn, pot_id, booking.date), pot_balance(conn, pot_id, date.max)))
        )
        if covered > 0:
            kind: AllocationKind = "emergency" if row["locked"] else "withdraw"
            _allocate(conn, pot_id, -covered, booking.date, kind, transaction_id=transaction_id, memo=booking.text)
        if row["plan_expense"]:
            conn.execute("UPDATE pot SET plan_expense = 0 WHERE id = ?", (pot_id,))
            _sync_planned_expense(conn, _row(conn, pot_id), today)
    return ExpenseResult(get_pot(conn, pot_id, today=today), covered, expense - covered)


# --- emergency ---------------------------------------------------------------------------------


def _shift(conn: sqlite3.Connection, row: sqlite3.Row, taken: int, today: date) -> date | None:
    """Target date after taking ``taken``: at the savings plan's rate, else at today's required rate."""
    target = row["target_amount_cents"]
    target_date = _day(row["target_date"])
    if target_date is None or target is None:
        return None
    plan = _savings_plan(conn, row["id"])
    if plan is not None and plan.active:
        rate = plan.amount_cents
    else:
        rate = monthly_rate(target, pot_balance(conn, row["id"], today), target_date, today)
    return shifted_target_date(target_date, taken, rate) if rate > 0 else None


def emergency_proposal(
    conn: sqlite3.Connection, account_id: int, amount_cents: int, *, today: date | None = None
) -> EmergencyProposal:
    """Propose taking an emergency amount from the account's pots in rank order (UC 5b, AK6, AK7)."""
    today = today or date.today()
    if amount_cents <= 0:
        raise PotsError(N_("Enter an amount greater than 0."))
    accounts.get_account(conn, account_id)
    collect_deposits(conn)
    rows = {
        row["id"]: row
        for row in conn.execute("SELECT * FROM pot WHERE account_id = ? AND closed_at IS NULL", (account_id,))
    }
    balances = {pot_id: pot_balance(conn, pot_id, today) for pot_id in rows}
    split = emergency_split(
        amount_cents,
        [EmergencyCandidate(i, r["emergency_rank"], bool(r["locked"]), balances[i]) for i, r in rows.items()],
    )
    lines = [
        EmergencyLine(
            pot_id=pot_id,
            name=rows[pot_id]["name"],
            rank=rows[pot_id]["emergency_rank"],
            amount_cents=taken,
            new_balance_cents=balances[pot_id] - taken,
            target_date=_day(rows[pot_id]["target_date"]),
            shifted_target_date=_shift(conn, rows[pot_id], taken, today),
        )
        for pot_id, taken in split
    ]
    covered = sum(line.amount_cents for line in lines)
    return EmergencyProposal(account_id, amount_cents, lines, covered, amount_cents - covered)


def confirm_emergency(
    conn: sqlite3.Connection, account_id: int, lines: Sequence[tuple[int, int]], *, today: date | None = None
) -> list[Pot]:
    """Take the confirmed (possibly hand-edited) emergency amounts, logged as ``emergency`` (UC 5b).

    Every line is (pot id, amount) of an open, unlocked pot on the account, at most its balance;
    locked pots are refused here (their way is ``withdraw`` with confirmation, UC 5c). Each pot's
    target date moves as the proposal showed.
    """
    today = today or date.today()
    if not lines:
        raise PotsError(N_("Choose at least one pot."))
    collect_deposits(conn)
    with transaction(conn):
        for pot_id, amount in lines:
            row = _open_row(conn, pot_id)
            if row["account_id"] != account_id:
                raise Conflict(N_("The pot %(name)s is not on this account."), name=row["name"])
            if row["locked"]:
                raise Conflict(N_("The pot %(name)s is locked and never part of an emergency."), name=row["name"])
            _check_amount_and_date(conn, row, amount, today, today)
            _check_available(conn, row, amount, today)
            shifted = _shift(conn, row, amount, today)
            _allocate(conn, pot_id, -amount, today, "emergency")
            if shifted is not None:
                conn.execute("UPDATE pot SET target_date = ? WHERE id = ?", (shifted.isoformat(), pot_id))
                _sync_planned_expense(conn, _row(conn, pot_id), today)
                plan = _savings_plan(conn, pot_id)
                if plan is not None:
                    _write_plan(conn, pot_id, plan.account_id, plan.amount_cents, plan.start_date, shifted, today)
    return [get_pot(conn, pot_id, today=today) for pot_id, _ in lines]


# --- savings plan ------------------------------------------------------------------------------


def _write_plan(
    conn: sqlite3.Connection,
    pot_id: int,
    from_account_id: int,
    amount_cents: int,
    start: date,
    end: date | None,
    today: date,
) -> recurring.Recurring:
    """Create or update the pot's monthly recurring transfer and tag it with the pot."""
    row = _row(conn, pot_id)
    wanted = RecurringFields(
        description=row["name"],
        amount_cents=amount_cents,
        account_id=from_account_id,
        interval="monthly",
        start_date=start,
        transfer_target_account_id=row["account_id"],
        day_of_month=start.day,
        end_date=end if end is None or end >= start else start,
    )
    plan = _savings_plan(conn, pot_id)
    if plan is None:
        item = recurring.create_recurring(conn, wanted, today=today)
        # recurring.pot_id is the -009 column meant for this link; RecurringFields does not carry it.
        conn.execute("UPDATE recurring SET pot_id = ? WHERE id = ?", (pot_id, item.id))
        return item
    return recurring.update_recurring(conn, plan.id, wanted, today=today)


def set_savings_plan(
    conn: sqlite3.Connection,
    pot_id: int,
    from_account_id: int,
    *,
    amount_cents: int | None = None,
    start_date: date | None = None,
    today: date | None = None,
) -> Pot:
    """Create or replace the pot's savings plan (UC step 2): a monthly transfer into the pot's account.

    ``amount_cents`` defaults to the monthly rate, ``start_date`` to one month from today; the plan
    ends on the target date. Its planned rows come from the recurring engine at once; booked ones
    count towards the pot.
    """
    today = today or date.today()
    row = _open_row(conn, pot_id)
    if from_account_id == row["account_id"]:
        raise Conflict(N_("Choose another account than the pot's account to pay in from."))
    if amount_cents is None:
        pot = get_pot(conn, pot_id, today=today)
        amount_cents = pot.monthly_rate_cents
    if not amount_cents or amount_cents <= 0:
        raise PotsError(N_("Enter a monthly amount greater than 0."))
    start = start_date or add_months(today, 1)
    with transaction(conn):
        _write_plan(conn, pot_id, from_account_id, amount_cents, start, _day(row["target_date"]), today)
    return get_pot(conn, pot_id, today=today)


def delete_savings_plan(conn: sqlite3.Connection, pot_id: int, *, today: date | None = None) -> Pot:
    """End the pot's savings plan: booked deposits stay with the pot, planned ones go."""
    row = _row(conn, pot_id)
    with transaction(conn):
        _end_plan(conn, row["id"])
    return get_pot(conn, pot_id, today=today)
