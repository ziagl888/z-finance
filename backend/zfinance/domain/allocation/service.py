"""Allocation rules (UC-ZF-014) as plain functions over a connection.

A fee on the account feels available, and at the end of the month nothing is left for debts or
reserves. A rule takes that decision out of the moment: every booked income that matches it gets a
**proposal** with fixed shares, and the user confirms with one click.

* **Proposal** (``sync_proposals``): the levy share first (``levy.reserve_target`` of the rate, into
  the levy pot — a business income only), then the rule's targets in their order: the first open
  debt of the payoff order (-021), a pot (-015), or the total reserve. Nothing is booked.
* **Core rule** (AK6): an open proposal is *not* a planned booking, so it never lowers
  ``free_amount``. The levy share is already reserved by ``levy_gap`` from the booking on, and
  nothing is deducted twice: an income of 4,000 € at 30 % raises the free amount by 2,800 €
  immediately, and confirming the proposal moves that same money into the levy pot.
* **Confirmation** (``confirm_proposal``, UC steps 4-5): one transfer pair per bookable line, all in
  one transaction, through the owning services (``pots.deposit``, ``debts.pay_extra``). A second
  confirmation is refused, so nothing is booked twice.
* **Rejection** (``skip_proposal``, UC 4a) logs the proposal as skipped and the month review counts
  it; a proposal is never silently dropped. An open proposal older than seven days is highlighted
  (AK5) and still not booked (UC 4b).
* **Reversal suggestion** (UC 2a): the income of a confirmed proposal was deleted → its bookings are
  listed for reversal, never reversed automatically.

The pure rules (validation of a rule, the lines of a proposal, edits) live in ``lines`` and are
tested without a database.

Invariants:

* At most one proposal per income (unique index on ``allocation_proposal.transaction_id``), and an
  income only matches rules that existed when it was written — a new rule never produces a pile of
  proposals for the past.
* A proposal never writes a planned row and never touches a balance; only ``confirm_proposal``
  books, and only through -008/-013/-015.
* The levy line is the floor of its line: it cannot be confirmed lower (AK7).

Errors are ``AllocationError`` subclasses carrying an ``N_()`` msgid; ``AccountsError``,
``PotsError``, ``DebtsError`` and ``TransactionsError`` from the services called here pass through.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from zfinance.config import Settings
from zfinance.db import connect, transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import Area
from zfinance.domain.allocation import lines as rules
from zfinance.domain.allocation.lines import FallbackKind, Line, Target
from zfinance.domain.debts import service as debts
from zfinance.domain.levy import service as levy
from zfinance.domain.payoff import service as payoff
from zfinance.domain.pots import service as pots
from zfinance.i18n import N_

logger = logging.getLogger(__name__)

#: Text of the bookings a confirmation writes; data, not a translated UI string.
BOOKING_TEXT = "Allocation"


# --- errors ------------------------------------------------------------------------------------


class AllocationError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(AllocationError):
    """A referenced rule, proposal or pot does not exist."""

    status_code = 404


class Conflict(AllocationError):
    """The request contradicts the stored state (a proposal that is no longer open)."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleFields:
    """What the user enters for a rule (create and full update).

    ``area`` and ``category_id`` are the trigger (both optional: any area, any category),
    ``min_amount_cents`` the minimum income (AK2), ``targets`` the shares in payment order and
    ``fallback`` where a debt share goes without an open debt (AK4).
    """

    name: str
    area: Area | None = None
    category_id: int | None = None
    min_amount_cents: int = 0
    targets: tuple[Target, ...] = ()
    fallback_kind: FallbackKind = "reserve"
    fallback_pot_id: int | None = None
    active: bool = True


@dataclass(frozen=True, kw_only=True)
class Rule(RuleFields):
    """A stored rule."""

    id: int
    created_at: str


@dataclass(frozen=True)
class ProposalLine:
    """One line of a proposal as the screen shows it.

    ``target`` names the resolved destination (the debt's creditor, the pot's name; empty for the
    total reserve, which the screen names itself). ``debt_id`` and ``pot_id`` are what the
    confirmation books against. ``editable`` is false for the levy line, whose amount is a
    reservation, not a proposal (AK7); ``books`` is false where nothing is booked at all.
    """

    kind: rules.LineKind
    amount_cents: int
    floor_cents: int
    capped: bool
    target: str
    pot_id: int | None = None
    debt_id: int | None = None
    editable: bool = True
    books: bool = True


@dataclass(frozen=True)
class Proposal:
    """A proposal with its lines (UC step 3). ``days_open``/``overdue`` drive the highlight (AK5)."""

    id: int
    transaction_id: int | None
    rule_id: int | None
    rule_name: str | None
    amount_cents: int
    text: str
    account_id: int
    account_name: str
    date: date
    status: str
    lines: list[ProposalLine]
    remaining_cents: int
    bookable_cents: int
    free_cents: int
    reserve_warning: bool
    reduced_total_cents: int
    days_open: int
    overdue: bool
    created_at: str
    confirmed_at: str | None


@dataclass(frozen=True)
class Reversal:
    """A confirmed proposal whose income was deleted (UC 2a): its bookings, for the user to undo."""

    proposal_id: int
    text: str
    amount_cents: int
    date: date
    booked_cents: int
    transfer_group_ids: list[str]
    confirmed_at: str | None


@dataclass(frozen=True)
class PotOption:
    """A pot the rule editor can choose (open pots only)."""

    id: int
    name: str
    account_id: int
    account_name: str
    is_levy_pot: bool


@dataclass(frozen=True)
class Overview:
    """Everything the allocation screen shows (UC steps 1-5)."""

    rules: list[Rule]
    proposals: list[Proposal]
    reversals: list[Reversal]
    pots: list[PotOption]
    levy_rate_bp: int
    levy_pot_id: int | None
    levy_pot_missing: bool
    free_cents: int
    skipped_this_month: int


@dataclass(frozen=True)
class Confirmation:
    """Result of a confirmation: the proposal afterwards and what it booked (AK3)."""

    proposal: Proposal
    transfer_count: int
    booked_cents: int


# --- helpers -----------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _targets_json(targets: tuple[Target, ...]) -> str:
    return json.dumps(
        [
            {"kind": t.kind, "pot_id": t.pot_id, "percent_bp": t.percent_bp, "fixed_cents": t.fixed_cents}
            for t in targets
        ]
    )


def _targets_of(text: str) -> list[Target]:
    return [
        Target(item["kind"], item.get("pot_id"), item.get("percent_bp"), item.get("fixed_cents"))
        for item in json.loads(text)
    ]


def _lines_json(items: list[Line]) -> str:
    return json.dumps(
        [
            {
                "kind": line.kind,
                "pot_id": line.pot_id,
                "amount_cents": line.amount_cents,
                "floor_cents": line.floor_cents,
                "capped": line.capped,
            }
            for line in items
        ]
    )


def _lines_of(text: str) -> list[Line]:
    return [
        Line(
            item["kind"],
            item.get("pot_id"),
            item["amount_cents"],
            item.get("floor_cents", 0),
            item.get("capped", False),
        )
        for item in json.loads(text)
    ]


def _rule(row: sqlite3.Row) -> Rule:
    return Rule(
        name=row["name"],
        area=row["area"],
        category_id=row["category_id"],
        min_amount_cents=row["min_amount_cents"],
        targets=tuple(_targets_of(row["targets_json"])),
        fallback_kind=row["fallback_kind"],
        fallback_pot_id=row["fallback_pot_id"],
        active=bool(row["active"]),
        id=row["id"],
        created_at=row["created_at"],
    )


def _rule_row(conn: sqlite3.Connection, rule_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM allocation_rule WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Allocation rule not found."))
    return row


def _proposal_row(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM allocation_proposal WHERE id = ?", (proposal_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Allocation proposal not found."))
    return row


# --- rules -------------------------------------------------------------------------------------


def list_rules(conn: sqlite3.Connection) -> list[Rule]:
    """All rules, newest last (the order of the editor)."""
    return [_rule(row) for row in conn.execute("SELECT * FROM allocation_rule ORDER BY id")]


def _check(conn: sqlite3.Connection, fields: RuleFields) -> RuleFields:
    """Validate a rule against the pure rules and the referenced category and pots."""
    if not fields.name.strip():
        raise AllocationError(N_("Enter a name for the rule."))
    if fields.min_amount_cents < 0:
        raise AllocationError(N_("The minimum amount cannot be negative."))
    targets = list(fields.targets)
    try:
        rules.check_targets(targets)
    except rules.InvalidRule as exc:
        raise AllocationError(exc.msgid, **exc.params) from exc
    if fields.category_id is not None:
        accounts.get_category(conn, fields.category_id)
    for pot_id in [t.pot_id for t in targets if t.kind == "pot"] + [fields.fallback_pot_id]:
        if pot_id is not None:
            _open_pot_name(conn, pot_id)
    return fields


def _open_pot_name(conn: sqlite3.Connection, pot_id: int) -> str:
    row = conn.execute("SELECT name, closed_at FROM pot WHERE id = ?", (pot_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Pot not found."))
    if row["closed_at"] is not None:
        raise Conflict(N_("The pot %(name)s is closed."), name=row["name"])
    return str(row["name"])


def _columns(fields: RuleFields) -> tuple[Any, ...]:
    return (
        fields.name.strip(),
        fields.area,
        fields.category_id,
        fields.min_amount_cents,
        _targets_json(tuple(fields.targets)),
        fields.fallback_kind,
        fields.fallback_pot_id,
        int(fields.active),
    )


def create_rule(conn: sqlite3.Connection, fields: RuleFields) -> Rule:
    """Store a rule (UC step 1). It acts on incomes booked from now on, not on the past."""
    fields = _check(conn, fields)
    now = _now()
    row = conn.execute(
        "INSERT INTO allocation_rule (name, area, category_id, min_amount_cents, targets_json, fallback_kind,"
        " fallback_pot_id, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (*_columns(fields), now, now),
    ).fetchone()
    return _rule(_rule_row(conn, int(row["id"])))


def update_rule(conn: sqlite3.Connection, rule_id: int, fields: RuleFields) -> Rule:
    """Replace a rule's fields; proposals already made keep their lines."""
    _rule_row(conn, rule_id)
    fields = _check(conn, fields)
    conn.execute(
        "UPDATE allocation_rule SET name = ?, area = ?, category_id = ?, min_amount_cents = ?, targets_json = ?,"
        " fallback_kind = ?, fallback_pot_id = ?, active = ?, updated_at = ? WHERE id = ?",
        (*_columns(fields), _now(), rule_id),
    )
    return _rule(_rule_row(conn, rule_id))


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    """Delete a rule. Its proposals stay (``rule_id`` becomes NULL): nothing is dropped silently."""
    _rule_row(conn, rule_id)
    conn.execute("DELETE FROM allocation_rule WHERE id = ?", (rule_id,))


# --- proposals ---------------------------------------------------------------------------------

#: Booked income rows that no proposal covers yet, with the area of their account. A transfer leg is
#: no income (its partner leg pays for it), and only rows written when a rule already existed can
#: match one (``created_at`` has second granularity, so the same second still counts).
_UNPROPOSED_SQL = """
SELECT t.id, t.date, t.amount_cents, t.text, t.account_id, t.category_id, t.created_at, a.area
  FROM "transaction" t
  JOIN account a ON a.id = t.account_id
 WHERE t.amount_cents > 0
   AND t.status = 'booked'
   AND t.transfer_group_id IS NULL
   AND t.created_at >= ?
   AND NOT EXISTS (SELECT 1 FROM allocation_proposal p WHERE p.transaction_id = t.id)
 ORDER BY t.id
"""


def _match(candidates: list[Rule], row: sqlite3.Row) -> Rule | None:
    """The rule an income falls under: a rule with a category beats one without, then the lowest id.

    A rule only counts for an income written once the rule existed (``created_at``, second
    granularity: the same second counts), so switching a rule on does not retro-propose the ledger.
    """
    matching = [
        rule
        for rule in candidates
        if rule.active
        and (rule.area is None or rule.area == row["area"])
        and (rule.category_id is None or rule.category_id == row["category_id"])
        and row["amount_cents"] >= rule.min_amount_cents
        and rule.created_at <= row["created_at"]
    ]
    return min(matching, key=lambda rule: (rule.category_id is None, rule.id), default=None)


def _levy_line_cents(conn: sqlite3.Connection, area: str, amount_cents: int) -> tuple[int, int | None]:
    """(levy share, levy pot) for an income: the rate of -019 on a business income, else (0, None).

    The share is ``levy.reserve_target`` — this feature never computes the levy itself (core rule).
    """
    if area != "business":
        return 0, None
    settings = levy.get_settings(conn)
    pot_id, _ = levy.levy_pot(conn, settings)
    if settings.levy_rate_bp <= 0 or pot_id is None:
        return 0, None
    return levy.reserve_target(settings.levy_rate_bp, amount_cents), pot_id


def sync_proposals(conn: sqlite3.Connection, *, today: date | None = None) -> int:
    """Create the missing proposals for booked incomes; return how many were written. Idempotent.

    Runs before every allocation read and at start, so the three writers of an income (quick entry,
    CSV import, receipt agent) need no hook of their own — the idiom of ``pots.collect_deposits``
    and ``debts.sync_debts``.
    """
    today = today or date.today()
    known = list_rules(conn)
    if not known:
        return 0
    earliest = min(rule.created_at for rule in known)
    written = 0
    with transaction(conn):
        for row in conn.execute(_UNPROPOSED_SQL, (earliest,)).fetchall():
            rule = _match(known, row)
            if rule is None:
                continue
            levy_cents, levy_pot_id = _levy_line_cents(conn, row["area"], row["amount_cents"])
            items = rules.proposal_lines(row["amount_cents"], levy_cents, levy_pot_id, list(rule.targets))
            conn.execute(
                "INSERT INTO allocation_proposal (transaction_id, rule_id, amount_cents, text, account_id, date,"
                " lines_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (
                    row["id"],
                    rule.id,
                    row["amount_cents"],
                    row["text"],
                    row["account_id"],
                    row["date"],
                    _lines_json(items),
                    _now(),
                ),
            )
            written += 1
    if written:
        logger.info("allocation: %s proposal(s) created", written)
    return written


def run_startup_proposal_sync(settings: Settings, *, today: date | None = None) -> int | None:
    """Create the missing proposals at start; log and never raise (called from the app lifespan)."""
    conn = connect(settings.db_path)
    try:
        return sync_proposals(conn, today=today)
    except Exception:
        logger.exception("allocation: proposal sync at start failed")
        return None
    finally:
        conn.close()


# --- resolving a line's target -----------------------------------------------------------------


def _first_open_debt(conn: sqlite3.Connection, today: date) -> int | None:
    """The debt the payoff plan pays next (-021), or None without an open debt (AK4)."""
    order = payoff.debt_order(conn, today=today)
    return order[0] if order else None


def _fallback_pot(conn: sqlite3.Connection, rule: Rule | None, account_id: int) -> int | None:
    """The pot a debt share falls back to: the rule's, else the highest-ranked open pot (UC 3b)."""
    if rule is not None and rule.fallback_kind == "reserve":
        return None
    if rule is not None and rule.fallback_pot_id is not None:
        row = conn.execute("SELECT id FROM pot WHERE id = ? AND closed_at IS NULL", (rule.fallback_pot_id,)).fetchone()
        if row is not None:
            return int(row["id"])
    row = conn.execute(
        "SELECT id FROM pot WHERE account_id = ? AND closed_at IS NULL AND emergency_rank IS NOT NULL"
        " ORDER BY emergency_rank LIMIT 1",
        (account_id,),
    ).fetchone()
    return None if row is None else int(row["id"])


def _resolved(conn: sqlite3.Connection, line: Line, rule: Rule | None, account_id: int, today: date) -> ProposalLine:
    """One line with its destination resolved for display and for booking (AK4: the fallback)."""
    if line.kind == "levy":
        name = _pot_name(conn, line.pot_id)
        return ProposalLine("levy", line.amount_cents, line.floor_cents, line.capped, name, line.pot_id, None, False)
    if line.kind == "pot":
        name = _pot_name(conn, line.pot_id)
        return ProposalLine("pot", line.amount_cents, 0, line.capped, name, line.pot_id)
    if line.kind == "reserve":
        return _reserve_line(line)
    debt_id = _first_open_debt(conn, today)
    if debt_id is not None:
        creditor = conn.execute("SELECT creditor FROM debt WHERE id = ?", (debt_id,)).fetchone()["creditor"]
        return ProposalLine("debt_plan", line.amount_cents, 0, line.capped, str(creditor), None, debt_id)
    pot_id = _fallback_pot(conn, rule, account_id)
    if pot_id is None:
        return _reserve_line(line)
    return ProposalLine("pot", line.amount_cents, 0, line.capped, _pot_name(conn, pot_id), pot_id)


def _reserve_line(line: Line) -> ProposalLine:
    """A share that stays on the account as part of the total reserve: nothing is booked (UC 3b)."""
    return ProposalLine("reserve", line.amount_cents, 0, line.capped, "", None, None, True, False)


def _pot_name(conn: sqlite3.Connection, pot_id: int | None) -> str:
    if pot_id is None:
        return ""
    row = conn.execute("SELECT name FROM pot WHERE id = ?", (pot_id,)).fetchone()
    return "" if row is None else str(row["name"])


# --- reading -----------------------------------------------------------------------------------


def _proposal(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    today: date,
    free_cents: int,
    rules_by_id: dict[int, Rule],
) -> Proposal:
    rule = rules_by_id.get(row["rule_id"]) if row["rule_id"] is not None else None
    items = _lines_of(row["lines_json"])
    resolved = [_resolved(conn, line, rule, row["account_id"], today) for line in items]
    bookable = rules.bookable_cents(items)
    created = date.fromisoformat(row["created_at"][:10])
    days_open = max((today - created).days, 0)
    account = conn.execute("SELECT name FROM account WHERE id = ?", (row["account_id"],)).fetchone()
    return Proposal(
        id=row["id"],
        transaction_id=row["transaction_id"],
        rule_id=row["rule_id"],
        rule_name=rule.name if rule is not None else None,
        amount_cents=row["amount_cents"],
        text=row["text"],
        account_id=row["account_id"],
        account_name=str(account["name"]) if account is not None else "",
        date=date.fromisoformat(row["date"]),
        status=row["status"],
        lines=resolved,
        remaining_cents=rules.remaining_cents(row["amount_cents"], items),
        bookable_cents=bookable,
        free_cents=free_cents,
        reserve_warning=row["status"] == "open" and bookable > free_cents,
        reduced_total_cents=max(min(bookable, free_cents), 0),
        days_open=days_open,
        overdue=row["status"] == "open" and days_open >= rules.OVERDUE_DAYS,
        created_at=row["created_at"],
        confirmed_at=row["confirmed_at"],
    )


def _group_cents(conn: sqlite3.Connection, group_id: str) -> int | None:
    """The incoming amount of a transfer pair, or None once the pair is gone (the reversal is done)."""
    row = conn.execute(
        'SELECT sum(amount_cents) FROM "transaction" WHERE transfer_group_id = ? AND amount_cents > 0',
        (group_id,),
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def _reversals(conn: sqlite3.Connection) -> list[Reversal]:
    """Confirmed proposals whose income is gone and whose bookings are still there (UC 2a)."""
    result: list[Reversal] = []
    for row in conn.execute(
        "SELECT * FROM allocation_proposal WHERE status = 'confirmed' AND transaction_id IS NULL ORDER BY id DESC"
    ):
        groups = [str(group) for group in json.loads(row["booked_json"])]
        if not groups:
            continue
        found = [(group, amount) for group in groups if (amount := _group_cents(conn, group)) is not None]
        if not found:
            continue
        result.append(
            Reversal(
                proposal_id=row["id"],
                text=row["text"],
                amount_cents=row["amount_cents"],
                date=date.fromisoformat(row["date"]),
                booked_cents=sum(int(item[1]) for item in found),
                transfer_group_ids=[str(item[0]) for item in found],
                confirmed_at=row["confirmed_at"],
            )
        )
    return result


def _pot_options(conn: sqlite3.Connection) -> list[PotOption]:
    rows = conn.execute(
        "SELECT p.id, p.name, p.account_id, p.is_levy_pot, a.name AS account_name FROM pot p"
        " JOIN account a ON a.id = p.account_id WHERE p.closed_at IS NULL ORDER BY a.name, p.name"
    )
    return [
        PotOption(row["id"], row["name"], row["account_id"], row["account_name"], bool(row["is_levy_pot"]))
        for row in rows
    ]


def overview(conn: sqlite3.Connection, *, today: date | None = None) -> Overview:
    """Rules, open proposals, reversal suggestions and the free amount (UC steps 1-3, AK5).

    Creates the missing proposals first (``sync_proposals``), like every other engine of the project
    syncs on read.
    """
    today = today or date.today()
    sync_proposals(conn, today=today)
    free_cents = levy.free_amount(conn, levy.month_end(today), today=today)
    known = list_rules(conn)
    by_id = {rule.id: rule for rule in known}
    proposals = [
        _proposal(conn, row, today=today, free_cents=free_cents, rules_by_id=by_id)
        for row in conn.execute("SELECT * FROM allocation_proposal WHERE status = 'open' ORDER BY date, id")
    ]
    pot_id, _ = levy.levy_pot(conn)
    settings = levy.get_settings(conn)
    skipped = skipped_in_month(conn, today.isoformat()[:7])
    return Overview(
        rules=known,
        proposals=proposals,
        reversals=_reversals(conn),
        pots=_pot_options(conn),
        levy_rate_bp=settings.levy_rate_bp,
        levy_pot_id=pot_id,
        levy_pot_missing=settings.levy_rate_bp > 0 and pot_id is None,
        free_cents=free_cents,
        skipped_this_month=skipped,
    )


def get_proposal(conn: sqlite3.Connection, proposal_id: int, *, today: date | None = None) -> Proposal:
    """One proposal with its resolved lines."""
    today = today or date.today()
    row = _proposal_row(conn, proposal_id)
    known = {rule.id: rule for rule in list_rules(conn)}
    free_cents = levy.free_amount(conn, levy.month_end(today), today=today)
    return _proposal(conn, row, today=today, free_cents=free_cents, rules_by_id=known)


# --- confirming and skipping --------------------------------------------------------------------


def _open_proposal(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row:
    row = _proposal_row(conn, proposal_id)
    if row["status"] != "open":
        raise Conflict(N_("This proposal was already decided."))
    return row


def _decide(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> None:
    """Run the ``... WHERE id = ? AND status = 'open'`` update that decides a proposal, or raise ``Conflict``.

    The open check before the write lock can be stale: a second request may have decided the
    proposal meanwhile. Exactly one decided row proves this request still held an open one.
    """
    if conn.execute(sql, params).rowcount != 1:
        raise Conflict(N_("This proposal was already decided."))


def _book(conn: sqlite3.Connection, line: ProposalLine, account_id: int, on: date, today: date) -> None:
    """Book one line through the service that owns its target; a ``reserve`` line books nothing."""
    if line.amount_cents <= 0:
        return
    if line.kind in {"levy", "pot"} and line.pot_id is not None:
        pot_account = conn.execute("SELECT account_id FROM pot WHERE id = ?", (line.pot_id,)).fetchone()
        same = pot_account is not None and int(pot_account["account_id"]) == account_id
        pots.deposit(
            conn,
            line.pot_id,
            line.amount_cents,
            on=on,
            from_account_id=None if same else account_id,
            memo=BOOKING_TEXT,
            today=today,
        )
    elif line.kind == "debt_plan" and line.debt_id is not None:
        debts.pay_extra(conn, line.debt_id, line.amount_cents, on=on, from_account_id=account_id, today=today)


def confirm_proposal(
    conn: sqlite3.Connection,
    proposal_id: int,
    amounts: dict[int, int] | None = None,
    *,
    today: date | None = None,
) -> Confirmation:
    """Book the proposal's lines as transfers, in one transaction (UC steps 4-5, AK3).

    ``amounts`` (line index → cents) are the user's edits; the levy line cannot go below its floor
    (AK7) and the total cannot exceed the income. The booking date is the income's date, or today
    when that lies in the future. A proposal that is not open raises ``Conflict``, so confirming
    twice books once.
    """
    today = today or date.today()
    row = _open_proposal(conn, proposal_id)
    rule_row = None if row["rule_id"] is None else _rule_row(conn, row["rule_id"])
    rule = None if rule_row is None else _rule(rule_row)
    try:
        items = rules.apply_edits(_lines_of(row["lines_json"]), amounts or {}, row["amount_cents"])
    except rules.InvalidRule as exc:
        raise AllocationError(exc.msgid, **exc.params) from exc
    on = min(date.fromisoformat(row["date"]), today)
    resolved = [_resolved(conn, line, rule, row["account_id"], today) for line in items]
    with transaction(conn):
        _open_proposal(conn, proposal_id)  # again under the write lock: the check above may be stale
        before = conn.execute('SELECT coalesce(max(id), 0) FROM "transaction"').fetchone()[0]
        for line in resolved:
            _book(conn, line, row["account_id"], on, today)
        groups = [
            str(item[0])
            for item in conn.execute(
                'SELECT DISTINCT transfer_group_id FROM "transaction"'
                " WHERE id > ? AND transfer_group_id IS NOT NULL ORDER BY id",
                (before,),
            )
        ]
        # The services write `manual` rows (`transactions._insert`); the writer re-tags them, like
        # the CSV import does.
        conn.execute("UPDATE \"transaction\" SET source = 'allocation' WHERE id > ?", (before,))
        _decide(
            conn,
            "UPDATE allocation_proposal SET status = 'confirmed', lines_json = ?, booked_json = ?, confirmed_at = ?"
            " WHERE id = ? AND status = 'open'",
            (_lines_json(items), json.dumps(groups), _now(), proposal_id),
        )
    booked = sum(line.amount_cents for line in resolved if line.books)
    return Confirmation(get_proposal(conn, proposal_id, today=today), len(groups), booked)


def skip_proposal(conn: sqlite3.Connection, proposal_id: int, *, today: date | None = None) -> Proposal:
    """Reject a proposal (UC 4a): logged as skipped and counted by the month review, never booked."""
    today = today or date.today()
    _open_proposal(conn, proposal_id)
    _decide(conn, "UPDATE allocation_proposal SET status = 'skipped' WHERE id = ? AND status = 'open'", (proposal_id,))
    return get_proposal(conn, proposal_id, today=today)


def skipped_in_month(conn: sqlite3.Connection, month: str) -> int:
    """How many proposals of ``month`` (YYYY-MM) were rejected — the month review's count (UC 4a)."""
    row = conn.execute(
        "SELECT count(*) FROM allocation_proposal WHERE status = 'skipped' AND date LIKE ?", (f"{month}-%",)
    ).fetchone()
    return int(row[0])
