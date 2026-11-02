"""Debts (UC-ZF-010) as plain functions over a connection.

A debt is a liability account plus, with a fixed instalment, a recurring transfer from the paying
account to that liability account. There is no booking path of its own: the account comes from
the accounts service (-007), special payments are transfers of the transactions service (-008),
and the instalments are planned rows of the recurring engine (-009).

* **Remaining amount** = the negated balance of the liability account over all booked rows. It
  starts at total - already repaid (the opening balance) and falls with every booked instalment
  or special payment, whoever booked it (confirmation UC-ZF-004, import UC-ZF-005, this screen).
* **Projection** (``payoff_plan``): the open due dates from the first open one, count =
  remaining ÷ instalment rounded up, end = the last of them, last instalment = the remainder.
* **Recurring item from master data.** ``_installment_fields`` derives the recurring item from
  the debt row and ``_sync_one`` makes the stored item equal to it — create, update, pause or
  delete through the recurring service, never a booking of its own. The tax plan (-014) and the
  savings pots (-015) follow this shape. The debt is the source: an edit of its recurring item on
  the recurring screen is reverted by the next sync.
* **Sync** (``sync_debts``) closes a debt whose remaining amount reached 0 (``closed_at``, the
  recurring item paused, so its planned instalments are gone), reopens it when the amount rises
  again, keeps the recurring item in line and sets the planned last instalment to the remainder.
  It runs after every write here, on the overview and at app start; confirmation (-011) and
  import (-012) may call it after booking an instalment.

The payoff budget (``payoff_budget``/``set_payoff_budget``, app_setting
``debts.payoff_budget_cents``) belongs to this feature and is read by the levy reserve (-019),
the allocation rules (-025) and the payoff plan (-026).

Errors are ``DebtsError`` subclasses carrying an ``N_()`` msgid; ``AccountsError``,
``TransactionsError`` and ``RecurringError`` from the services called here pass through.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from zfinance.app_settings import get_setting, set_setting
from zfinance.config import Settings
from zfinance.db import connect, transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, AccountsError, Area
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.schedule import MONTH_STEPS, Schedule, add_months, due_dates
from zfinance.domain.recurring.service import RecurringError, RecurringFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransactionsError, TransferFields
from zfinance.i18n import N_

logger = logging.getLogger(__name__)

DebtInterval = Literal["monthly", "quarterly", "yearly"]

PAYOFF_BUDGET_KEY = "debts.payoff_budget_cents"

# The remaining amount counts every booked row, whatever its date.
_ALL_DATES = date.max


# --- errors ------------------------------------------------------------------------------------


class DebtsError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(DebtsError):
    """The debt does not exist."""

    status_code = 404


class Conflict(DebtsError):
    """The request contradicts the state of the debt."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DebtFields:
    """What the user enters for a debt (create and full update).

    ``installment_cents``, ``interval`` and ``first_due`` are set together or not at all (UC 1a:
    no fixed instalment). ``interest_rate_pct`` and ``contract_end`` are information only;
    ``target_end`` is the wished end of a debt without instalment.
    """

    creditor: str
    area: Area
    total_amount_cents: int
    installment_cents: int | None = None
    interval: DebtInterval | None = None
    first_due: date | None = None
    pay_account_id: int | None = None
    interest_rate_pct: str | None = None
    contract_end: date | None = None
    target_end: date | None = None


@dataclass(frozen=True)
class PayoffPlan:
    """The open instalments of a debt: how many, the next and last due date, the last amount."""

    count: int
    next_due: date
    end: date
    last_installment_cents: int


@dataclass(frozen=True, kw_only=True)
class Debt(DebtFields):
    """A stored debt with its figures.

    ``remaining_cents`` = -balance of the liability account; ``paid_cents`` = total - remaining.
    ``plan`` exists for an open debt with an instalment; ``required_monthly_cents`` for an open
    debt without instalment that has a ``target_end`` (UC 1a).
    """

    id: int
    account_id: int
    recurring_id: int | None
    closed_at: str | None
    created_at: str
    remaining_cents: int
    paid_cents: int
    plan: PayoffPlan | None
    required_monthly_cents: int | None


@dataclass(frozen=True)
class Overview:
    """All debts with the remaining amount summed per area and in total (UC AK4)."""

    debts: list[Debt]
    remaining_by_area: dict[Area, int]
    remaining_total_cents: int


@dataclass(frozen=True)
class Payment:
    """Result of a special payment: the debt afterwards, the booked amount, whether it was capped."""

    debt: Debt
    booked_cents: int
    capped: bool


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


# --- pure rules --------------------------------------------------------------------------------


def payoff_plan(
    schedule: Schedule, first_open: date, done: set[date], remaining_cents: int, installment_cents: int
) -> PayoffPlan:
    """The open instalments needed to pay ``remaining_cents`` (UC step 2: remaining ÷ instalment).

    Open due dates are the dates of ``schedule`` (without end date) from ``first_open`` on that
    are not in ``done`` (already booked or skipped). The count is rounded up; every instalment but
    the last is full, the last is the remainder. ``remaining_cents`` and ``installment_cents`` are
    positive.
    """
    count = -(-remaining_cents // installment_cents)
    # ``count + len(done)`` steps of the interval hold at least that many due dates.
    last = add_months(first_open, MONTH_STEPS[schedule.interval] * (count + len(done)))
    open_dates = [due for due in due_dates(schedule, first_open, last) if due not in done][:count]
    return PayoffPlan(
        count=count,
        next_due=open_dates[0],
        end=open_dates[-1],
        last_installment_cents=remaining_cents - installment_cents * (count - 1),
    )


def required_monthly_cents(remaining_cents: int, target_end: date, today: date) -> int:
    """Monthly rate that pays ``remaining_cents`` by ``target_end`` (UC 1a), in whole cents.

    One payment per month after the current one up to the target month; a target in the current
    month or earlier leaves one payment (the whole amount). Rounded up so the target is met.
    """
    months = max(1, (target_end.year * 12 + target_end.month) - (today.year * 12 + today.month))
    return -(-remaining_cents // months)


# --- reading -----------------------------------------------------------------------------------


def _row(conn: sqlite3.Connection, debt_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM debt WHERE id = ?", (debt_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Debt not found."))
    return row


def _remaining(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    return -accounts.balance_at(conn, row["account_id"], _ALL_DATES)


def _schedule(row: sqlite3.Row) -> Schedule:
    first_due = date.fromisoformat(row["first_due"])
    return Schedule(row["interval"], first_due, None, first_due.day)


def _plan(conn: sqlite3.Connection, row: sqlite3.Row, remaining: int, today: date) -> PayoffPlan:
    """The projection from the stored instalment rows: open = planned or not generated yet."""
    first_open = max(today, date.fromisoformat(row["first_due"]))
    done: set[date] = set()
    if row["recurring_id"] is not None:
        planned = conn.execute(
            "SELECT min(due_date) FROM \"transaction\" WHERE recurring_id = ? AND status = 'planned'",
            (row["recurring_id"],),
        ).fetchone()[0]
        if planned is not None:
            first_open = date.fromisoformat(planned)
        done = {
            date.fromisoformat(r[0])
            for r in conn.execute(
                "SELECT due_date FROM \"transaction\" WHERE recurring_id = ? AND status != 'planned'"
                " AND due_date IS NOT NULL",
                (row["recurring_id"],),
            )
        }
    return payoff_plan(_schedule(row), first_open, done, remaining, row["installment_cents"])


def _debt(conn: sqlite3.Connection, row: sqlite3.Row, today: date) -> Debt:
    remaining = _remaining(conn, row)
    is_open = row["closed_at"] is None and remaining > 0
    plan = _plan(conn, row, remaining, today) if is_open and row["installment_cents"] is not None else None
    target_end = _day(row["target_end"])
    required = None
    if is_open and row["installment_cents"] is None and target_end is not None:
        required = required_monthly_cents(remaining, target_end, today)
    return Debt(
        id=row["id"],
        creditor=row["creditor"],
        area=row["area"],
        total_amount_cents=row["total_amount_cents"],
        installment_cents=row["installment_cents"],
        interval=row["interval"],
        first_due=_day(row["first_due"]),
        pay_account_id=row["pay_account_id"],
        interest_rate_pct=row["interest_rate_pct"],
        contract_end=_day(row["contract_end"]),
        target_end=target_end,
        account_id=row["account_id"],
        recurring_id=row["recurring_id"],
        closed_at=row["closed_at"],
        created_at=row["created_at"],
        remaining_cents=max(remaining, 0),
        paid_cents=row["total_amount_cents"] - max(remaining, 0),
        plan=plan,
        required_monthly_cents=required,
    )


def get_debt(conn: sqlite3.Connection, debt_id: int, *, today: date | None = None) -> Debt:
    """Return the debt with its figures or raise ``NotFound``."""
    return _debt(conn, _row(conn, debt_id), today or date.today())


def overview(conn: sqlite3.Connection, *, today: date | None = None) -> Overview:
    """Sync all debts, then list them (open first, by creditor) with the sums per area (UC AK4)."""
    today = today or date.today()
    sync_debts(conn, today=today)
    rows = conn.execute("SELECT * FROM debt ORDER BY closed_at IS NOT NULL, creditor COLLATE NOCASE, id")
    debts = [_debt(conn, row, today) for row in rows]
    by_area: dict[Area, int] = dict.fromkeys(accounts.AREAS, 0)
    for debt in debts:
        by_area[debt.area] += debt.remaining_cents
    return Overview(debts=debts, remaining_by_area=by_area, remaining_total_cents=sum(by_area.values()))


def history(conn: sqlite3.Connection, debt_id: int) -> list[transactions.Transaction]:
    """Bookings on the debt's liability account (instalments, special payments, increases), newest first."""
    row = _row(conn, debt_id)
    return transactions.list_transactions(conn, account_id=row["account_id"], limit=500)


# --- writing -----------------------------------------------------------------------------------


def _check(conn: sqlite3.Connection, fields_: DebtFields, account_id: int | None = None) -> DebtFields:
    """Validate and normalize the fields (creditor trimmed, interest rate as a plain decimal)."""
    creditor = fields_.creditor.strip()
    if not creditor:
        raise DebtsError(N_("Enter the creditor."))
    if fields_.area not in accounts.AREAS:
        raise DebtsError(N_("Choose private or business."))
    if fields_.total_amount_cents <= 0:
        raise DebtsError(N_("Enter a total amount greater than 0."))
    has_installment = fields_.installment_cents is not None
    if has_installment != (fields_.interval is not None) or has_installment != (fields_.first_due is not None):
        raise DebtsError(N_("An instalment needs an amount, an interval and a first due date."))
    if fields_.installment_cents is not None:
        if fields_.installment_cents <= 0:
            raise DebtsError(N_("Enter an instalment greater than 0."))
        if fields_.interval not in MONTH_STEPS:
            raise DebtsError(N_("Choose monthly, quarterly or yearly."))
        if fields_.pay_account_id is None:
            raise DebtsError(N_("Choose the account the instalments are paid from."))
    if fields_.pay_account_id is not None:
        if fields_.pay_account_id == account_id:
            raise Conflict(N_("The debt cannot be paid from its own account."))
        pay = accounts.get_account(conn, fields_.pay_account_id)
        if pay.archived_at is not None:
            raise Conflict(N_("The account %(name)s is archived."), name=pay.name)
    interest = fields_.interest_rate_pct
    if interest is not None and interest.strip():
        try:
            rate = Decimal(interest.strip().replace(",", "."))
        except InvalidOperation:
            rate = Decimal(-1)
        if not rate.is_finite() or rate < 0:
            raise DebtsError(N_("Enter the interest rate as a number, e.g. 4.5."))
        interest = str(rate)
    else:
        interest = None
    return DebtFields(
        creditor=creditor,
        area=fields_.area,
        total_amount_cents=fields_.total_amount_cents,
        installment_cents=fields_.installment_cents,
        interval=fields_.interval,
        first_due=fields_.first_due,
        pay_account_id=fields_.pay_account_id,
        interest_rate_pct=interest,
        contract_end=fields_.contract_end,
        target_end=fields_.target_end,
    )


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _columns(f: DebtFields) -> tuple[Any, ...]:
    return (
        f.creditor,
        f.area,
        f.total_amount_cents,
        f.installment_cents,
        f.interval,
        _iso(f.first_due),
        f.pay_account_id,
        f.interest_rate_pct,
        _iso(f.contract_end),
        _iso(f.target_end),
    )


def create_debt(
    conn: sqlite3.Connection, fields_: DebtFields, *, repaid_cents: int = 0, today: date | None = None
) -> Debt:
    """Create a debt (UC steps 1-2): liability account with balance -(total - repaid), instalments.

    The liability account opens today with the remaining amount as negative opening balance and is
    named after the creditor (account names are unique, so a clash is refused by the accounts
    service). With an instalment the recurring transfer and its planned rows for the 12-month
    horizon exist on return.
    """
    today = today or date.today()
    fields_ = _check(conn, fields_)
    if not 0 <= repaid_cents < fields_.total_amount_cents:
        raise DebtsError(N_("The amount already repaid must be at least 0 and less than the total."))
    with transaction(conn):
        account = accounts.create_account(
            conn,
            AccountFields(
                fields_.creditor, fields_.area, "liability", -(fields_.total_amount_cents - repaid_cents), today
            ),
        )
        row = conn.execute(
            "INSERT INTO debt (creditor, area, total_amount_cents, installment_cents, interval, first_due,"
            " pay_account_id, interest_rate_pct, contract_end, target_end, account_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (*_columns(fields_), account.id, _now()),
        ).fetchone()
        debt_id = int(row["id"])
        _sync_one(conn, _row(conn, debt_id), today)
    return get_debt(conn, debt_id, today=today)


def update_debt(conn: sqlite3.Connection, debt_id: int, fields_: DebtFields, *, today: date | None = None) -> Debt:
    """Replace a debt's fields; the liability account and the instalments follow (UC 7a).

    A changed total is booked on the liability account as the difference, dated today, so the
    history stays and the remaining amount moves by it. The total cannot fall to or below what is
    already repaid. Creditor and area are copied to the liability account.
    """
    today = today or date.today()
    existing = get_debt(conn, debt_id, today=today)
    fields_ = _check(conn, fields_, existing.account_id)
    delta = fields_.total_amount_cents - existing.total_amount_cents
    if delta and fields_.total_amount_cents <= existing.paid_cents:
        raise DebtsError(N_("The total must be more than the amount already repaid."))
    account = accounts.get_account(conn, existing.account_id)
    with transaction(conn):
        accounts.update_account(
            conn,
            account.id,
            AccountFields(
                fields_.creditor,
                fields_.area,
                "liability",
                account.opening_balance_cents,
                account.opening_date,
                account.reserve_floor_cents,
            ),
        )
        if delta:
            transactions.create_transaction(
                conn, BookingFields(today, -delta, account.id, None, fields_.creditor), today=today
            )
        conn.execute(
            "UPDATE debt SET creditor = ?, area = ?, total_amount_cents = ?, installment_cents = ?, interval = ?,"
            " first_due = ?, pay_account_id = ?, interest_rate_pct = ?, contract_end = ?, target_end = ?"
            " WHERE id = ?",
            (*_columns(fields_), debt_id),
        )
        _sync_one(conn, _row(conn, debt_id), today)
    return get_debt(conn, debt_id, today=today)


def pay_extra(
    conn: sqlite3.Connection,
    debt_id: int,
    amount_cents: int,
    *,
    on: date | None = None,
    from_account_id: int | None = None,
    today: date | None = None,
) -> Payment:
    """Book a special payment as a transfer paying account → liability account (UC steps 5-6).

    ``on`` defaults to today and cannot lie in the future (a payment that has not happened does
    not lower the remaining amount). ``from_account_id`` defaults to the debt's paying account. An
    amount above the remaining amount is capped to it and reported with ``capped`` (UC 5a): the
    difference stays on the paying account.
    """
    today = today or date.today()
    on = on or today
    row = _row(conn, debt_id)
    if amount_cents <= 0:
        raise DebtsError(N_("Enter an amount greater than 0."))
    if on > today:
        raise DebtsError(N_("A special payment cannot be dated in the future."))
    source = from_account_id if from_account_id is not None else row["pay_account_id"]
    if source is None:
        raise DebtsError(N_("Choose the account the payment comes from."))
    remaining = _remaining(conn, row)
    if row["closed_at"] is not None or remaining <= 0:
        raise Conflict(N_("This debt is already paid off."))
    booked = min(amount_cents, remaining)
    with transaction(conn):
        transactions.create_transfer(
            conn, TransferFields(on, booked, source, row["account_id"], None, row["creditor"]), today=today
        )
        _sync_one(conn, _row(conn, debt_id), today)
    return Payment(get_debt(conn, debt_id, today=today), booked, booked < amount_cents)


def delete_debt(conn: sqlite3.Connection, debt_id: int) -> None:
    """Delete a debt: its recurring item and planned instalments go; booked rows stay.

    The liability account is deleted when it has no bookings (a debt entered by mistake), else
    archived with its history.
    """
    row = _row(conn, debt_id)
    with transaction(conn):
        if row["recurring_id"] is not None:
            recurring.delete_recurring(conn, row["recurring_id"])
        conn.execute("DELETE FROM debt WHERE id = ?", (debt_id,))
        if accounts.account_booking_counts(conn).get(row["account_id"], 0):
            accounts.set_archived(conn, row["account_id"], True)
        else:
            accounts.delete_account(conn, row["account_id"])


# --- recurring item from master data -----------------------------------------------------------


def _installment_fields(row: sqlite3.Row, end: date) -> RecurringFields:
    """The recurring transfer a debt with instalment stands for, ending on the projected end."""
    first_due = date.fromisoformat(row["first_due"])
    return RecurringFields(
        description=row["creditor"],
        amount_cents=row["installment_cents"],
        account_id=row["pay_account_id"],
        interval=row["interval"],
        start_date=first_due,
        transfer_target_account_id=row["account_id"],
        day_of_month=first_due.day,
        end_date=end,
    )


def _stored_fields(item: recurring.Recurring) -> RecurringFields:
    return RecurringFields(**{f.name: getattr(item, f.name) for f in fields(RecurringFields)})


def _set_last_installment(conn: sqlite3.Connection, recurring_id: int, plan: PayoffPlan, today: date) -> None:
    """Give the planned last instalment the remainder instead of the full amount (if generated yet)."""
    last = conn.execute(
        "SELECT * FROM \"transaction\" WHERE recurring_id = ? AND due_date = ? AND status = 'planned'"
        " AND amount_cents < 0",
        (recurring_id, plan.end.isoformat()),
    ).fetchone()
    if last is None or -last["amount_cents"] == plan.last_installment_cents:
        return
    # A transfer leg: update_transaction rewrites both legs and keeps the planned status.
    transactions.update_transaction(
        conn,
        last["id"],
        BookingFields(
            date.fromisoformat(last["date"]),
            -plan.last_installment_cents,
            last["account_id"],
            last["category_id"],
            last["text"],
            last["counterparty"],
            last["memo"],
        ),
        today=today,
    )


def _set_recurring_id(conn: sqlite3.Connection, debt_id: int, recurring_id: int | None) -> None:
    conn.execute("UPDATE debt SET recurring_id = ? WHERE id = ?", (recurring_id, debt_id))


def _sync_one(conn: sqlite3.Connection, row: sqlite3.Row, today: date) -> None:
    """Bring one debt's state and recurring item in line with its remaining amount and fields."""
    remaining = _remaining(conn, row)
    recurring_id = row["recurring_id"]
    if remaining <= 0:
        # UC step 7 / AK3: paid off — mark it and end the instalments (pausing removes planned rows).
        if row["closed_at"] is None:
            conn.execute("UPDATE debt SET closed_at = ? WHERE id = ?", (_now(), row["id"]))
        if recurring_id is not None and recurring.get_recurring(conn, recurring_id).active:
            recurring.set_active(conn, recurring_id, False, today=today)
        return
    if row["closed_at"] is not None:
        conn.execute("UPDATE debt SET closed_at = NULL WHERE id = ?", (row["id"],))
    if row["installment_cents"] is None or row["pay_account_id"] is None:
        if recurring_id is not None:
            recurring.delete_recurring(conn, recurring_id)
            _set_recurring_id(conn, row["id"], None)
        return
    plan = _plan(conn, row, remaining, today)
    wanted = _installment_fields(row, plan.end)
    if recurring_id is None:
        recurring_id = recurring.create_recurring(conn, wanted, today=today).id
        _set_recurring_id(conn, row["id"], recurring_id)
    else:
        item = recurring.get_recurring(conn, recurring_id)
        if _stored_fields(item) != wanted:
            recurring.update_recurring(conn, recurring_id, wanted, today=today)
        if not item.active:
            recurring.set_active(conn, recurring_id, True, today=today)
    _set_last_installment(conn, recurring_id, plan, today)


def sync_debts(conn: sqlite3.Connection, *, today: date | None = None) -> list[int]:
    """Sync every debt (close, reopen, instalments); return the ids that could not be synced.

    Contract for confirmation (-011) and import (-012): call it after booking an instalment so a
    paid-off debt loses its planned instalments at once. A debt whose sync fails (an account
    archived since, a booking rule refusing a date) is logged and skipped; the others go on.
    """
    today = today or date.today()
    failed: list[int] = []
    for row in conn.execute("SELECT * FROM debt ORDER BY id").fetchall():
        try:
            with transaction(conn):
                _sync_one(conn, row, today)
        except (DebtsError, AccountsError, TransactionsError, RecurringError) as exc:
            logger.warning("debt %s not synced: %s", row["id"], exc)
            failed.append(row["id"])
    return failed


def run_startup_sync(settings: Settings, *, today: date | None = None) -> list[int] | None:
    """Sync all debts at app start, after the recurring generation. Never raises."""
    try:
        conn = connect(settings.db_path)
        try:
            return sync_debts(conn, today=today)
        finally:
            conn.close()
    except Exception:
        logger.exception("debt sync at startup failed; the app starts without it")
        return None


# --- settings ----------------------------------------------------------------------------------


def payoff_budget(conn: sqlite3.Connection) -> int:
    """The monthly payoff budget in cents; 0 if never set.

    Contract for the levy reserve (-019), the allocation rules (-025) and the payoff plan (-026):
    the amount per month the user can put towards debts beyond the fixed instalments, stored as
    app_setting ``debts.payoff_budget_cents`` (an int, never negative). Read it through this
    function, never through the key.
    """
    return int(get_setting(conn, PAYOFF_BUDGET_KEY, 0))


def set_payoff_budget(conn: sqlite3.Connection, cents: int) -> int:
    """Store the monthly payoff budget in cents; negative values are refused."""
    if cents < 0:
        raise DebtsError(N_("The payoff budget cannot be negative."))
    set_setting(conn, PAYOFF_BUDGET_KEY, cents)
    return cents
