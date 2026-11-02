"""Payoff plan (UC-ZF-012) as functions over a connection.

It reads the debts (-013), the monthly report (-010) and the levy engine (-019) through their
service functions and writes bookings only through the transactions service (-008):

* **Available amount** (UC step 2) = Ø income of the last 3 complete months (or the user's
  override) - fixed costs - instalments - pot rates - levy share. The three blocks come from
  ``levy.revenue_blocks``; the levy share is ``levy.reserve_target`` of the months' levy base, so
  the amount is net of the levies (core rule): a booked income adds only (1 - rate) of itself.
* **Budget** = ``debts.payoff_budget``, the amount per month on top of the instalments (the one
  source; the levy's required revenue reads it too). Total per month = instalments + budget.
* **Special payments** (UC step 4): one planned transfer paying account → liability account per
  month and debt, for the next 12 months, created, rewritten or removed by ``sync_plan`` so they
  always follow the current plan: once a debt is paid off they move on to the next. The recurring
  engine cannot move its target account, so they are single transfers (the task's stop condition).
  Confirmation (-011) and import (-012) book them through ``replace_planned``.
* **Baseline** (UC step 6): saving stores the planned repayment per month and the debt-free
  month. Syncs move the special payments but not the baseline, so a skipped payment shows as
  "behind plan" with the catch-up amount, and the debt-free month as moved (AK4).

Errors are ``PayoffError`` carrying an ``N_()`` msgid; ``DebtsError``, ``AccountsError`` and
``TransactionsError`` from the services called here pass through.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

from zfinance.app_settings import get_setting, set_setting
from zfinance.config import Settings
from zfinance.db import connect, transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.debts import service as debts
from zfinance.domain.levy import service as levy
from zfinance.domain.payoff.plan import (
    STRATEGIES,
    MonthProgress,
    Plan,
    PlanDebt,
    Strategy,
    build_plan,
    key_index,
    month_index,
    month_key,
    progress,
)
from zfinance.domain.recurring.schedule import MONTH_STEPS
from zfinance.domain.reports import service as reports
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import TransferFields
from zfinance.i18n import N_

logger = logging.getLogger(__name__)

INCOME_OVERRIDE_KEY = "payoff.income_override_cents"
#: Complete months the average income is taken over (UC step 2).
INCOME_MONTHS = 3
#: Special payments are planned for this many whole months ahead (the 1st of each month after
#: today, so exactly 12 slices). A month-granular window, not the day window ``[today, today + 12
#: months)`` of the recurring engine; see ARCHITECTURE.md "Time windows".
HORIZON_MONTHS = 12
SPECIAL_PAYMENT_TEXT = "Special payment {creditor}"


class PayoffError(Exception):
    """A refused input; ``status_code`` is the HTTP status, ``msgid`` an ``N_()`` catalog id."""

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanFields:
    """What the user saves. ``budget_cents`` is the amount per month on top of the instalments."""

    strategy: Strategy
    budget_cents: int
    manual_order: tuple[int, ...] = ()
    income_override_cents: int | None = None


@dataclass(frozen=True)
class Availability:
    """The available amount per month and its terms (UC step 2), all in cents per month."""

    income_cents: int
    income_months: tuple[str, ...]
    income_overridden: bool
    fixed_costs_cents: int
    installments_cents: int
    pot_rates_cents: int
    levy_cents: int
    levy_rate_bp: int
    available_cents: int


@dataclass(frozen=True)
class DebtLine:
    """An open debt in plan order with the month of its last payment (None: no plan / never)."""

    id: int
    creditor: str
    remaining_cents: int
    installment_cents: int | None
    interval: str | None
    interest_rate_pct: str | None
    end_month: str | None


@dataclass(frozen=True)
class MonthLine:
    """One month of the plan: instalments, special payments (per debt) and what remains after it."""

    month: str
    installments_cents: int
    extra_cents: int
    extra_to: tuple[tuple[int, int], ...]
    remaining_cents: int


@dataclass(frozen=True)
class Status:
    """Everything the payoff screen shows (UC steps 2-6)."""

    saved: bool
    strategy: Strategy
    manual_order: tuple[int, ...]
    budget_cents: int
    total_monthly_cents: int
    income_override_cents: int | None
    availability: Availability
    remaining_total_cents: int
    has_plan: bool
    shortfall_cents: int
    debts: list[DebtLine]
    months: list[MonthLine]
    debt_free_month: str | None
    baseline_end_month: str | None
    shift_months: int | None
    progress: list[MonthProgress]
    behind: bool
    catch_up_cents: int
    over_budget: bool
    suggested_budget_cents: int | None
    free_now_cents: int
    missing_pay_account: list[int]


@dataclass(frozen=True)
class WhatIf:
    """The plan with another budget, not saved (UC step 7)."""

    budget_cents: int
    total_monthly_cents: int
    has_plan: bool
    debt_free_month: str | None
    ends: dict[int, str | None]


@dataclass(frozen=True)
class _Computed:
    plan: Plan | None
    availability: Availability
    debts: dict[int, debts.Debt]
    start: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _first_day(index: int) -> date:
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


# --- settings ----------------------------------------------------------------------------------


def _plan_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM payoff_plan WHERE id = 1").fetchone()


def _settings(conn: sqlite3.Connection) -> tuple[Strategy, tuple[int, ...]]:
    row = _plan_row(conn)
    if row is None:
        return "snowball", ()
    return row["strategy"], tuple(json.loads(row["manual_order"]))


def income_override(conn: sqlite3.Connection) -> int | None:
    """The user's monthly income instead of the 3-month average (UC assumption), or None."""
    value = get_setting(conn, INCOME_OVERRIDE_KEY)
    return None if value is None else int(value)


# --- available amount --------------------------------------------------------------------------


def availability(conn: sqlite3.Connection, *, today: date, override: int | None = None) -> Availability:
    """The available amount per month, net of the levies (see the module docstring)."""
    current = month_index(today)
    keys = tuple(month_key(current - back) for back in range(INCOME_MONTHS, 0, -1))
    settings = levy.get_settings(conn)
    income_sum = sum(reports.month_report(conn, key, "all").income.amount_cents for key in keys)
    business = [reports.month_report(conn, key, "business") for key in keys]
    if settings.levy_base == "income":
        base_sum = sum(report.income.amount_cents for report in business)
    else:
        base_sum = sum(report.balance.amount_cents for report in business)
    levy_sum = levy.reserve_target(settings.levy_rate_bp, base_sum)
    if override is None:
        income = Fraction(income_sum, INCOME_MONTHS)
        levy_share = Fraction(levy_sum, INCOME_MONTHS)
    elif income_sum > 0:
        # The override carries the levy share the income of the last months carried.
        income = Fraction(override)
        levy_share = Fraction(levy_sum * override, income_sum)
    else:
        # No history: treat the whole override as levy-liable, so no levy money shows as free.
        income = Fraction(override)
        levy_share = Fraction(settings.levy_rate_bp * override, 10_000)
    blocks = levy.revenue_blocks(conn, today=today)
    available = income - blocks.fixed_costs - blocks.installments - blocks.pot_rates - levy_share
    return Availability(
        income_cents=levy.half_up(income),
        income_months=keys,
        income_overridden=override is not None,
        fixed_costs_cents=levy.half_up(blocks.fixed_costs),
        installments_cents=levy.half_up(blocks.installments),
        pot_rates_cents=levy.half_up(blocks.pot_rates),
        levy_cents=levy.half_up(levy_share),
        levy_rate_bp=settings.levy_rate_bp,
        available_cents=levy.half_up(available),
    )


# --- the plan ----------------------------------------------------------------------------------


def _open_debts(conn: sqlite3.Connection, today: date) -> dict[int, debts.Debt]:
    ids = [row[0] for row in conn.execute("SELECT id FROM debt ORDER BY id")]
    loaded = (debts.get_debt(conn, debt_id, today=today) for debt_id in ids)
    return {debt.id: debt for debt in loaded if debt.closed_at is None and debt.remaining_cents > 0}


def _pending_extra(conn: sqlite3.Connection, before: date) -> dict[int, int]:
    """Special payments still planned before the plan starts, per debt (assumed to be paid)."""
    rows = conn.execute(
        'SELECT pt.debt_id, sum(t.amount_cents) FROM payoff_transfer pt JOIN "transaction" t'
        " ON t.transfer_group_id = pt.transfer_group_id AND t.amount_cents > 0"
        " WHERE t.status = 'planned' AND t.date < ? AND pt.debt_id IS NOT NULL GROUP BY pt.debt_id",
        (before.isoformat(),),
    )
    return {row[0]: row[1] for row in rows}


def _plan_debt(debt: debts.Debt, start: int, pending: int) -> PlanDebt:
    """The debt from plan month 0 on: instalments and special payments due before are taken off."""
    remaining = debt.remaining_cents - pending
    installment, step, first_due = 0, 1, 0
    if debt.plan is not None and debt.installment_cents is not None and debt.interval is not None:
        installment, step = debt.installment_cents, MONTH_STEPS[debt.interval]
        next_due = month_index(debt.plan.next_due)
        before = max(0, -(-(start - next_due) // step))
        remaining -= before * installment
        first_due = next_due + before * step - start
    rate = Decimal(debt.interest_rate_pct) if debt.interest_rate_pct else Decimal(0)
    return PlanDebt(debt.id, max(remaining, 0), installment, step, first_due, rate)


def _compute(
    conn: sqlite3.Connection,
    today: date,
    *,
    budget: int,
    strategy: Strategy,
    manual_order: tuple[int, ...],
    override: int | None,
) -> _Computed:
    avail = availability(conn, today=today, override=override)
    open_debts = _open_debts(conn, today)
    start = month_index(today) + 1
    if avail.available_cents < 0:
        # UC 2a / AK3: fixed costs and instalments exceed the income, no plan is computed.
        return _Computed(None, avail, open_debts, start)
    pending = _pending_extra(conn, _first_day(start))
    plan_debts = [_plan_debt(debt, start, pending.get(debt.id, 0)) for debt in open_debts.values()]
    plan = build_plan([d for d in plan_debts if d.remaining_cents > 0], budget, strategy, manual_order)
    return _Computed(plan, avail, open_debts, start)


def _current(conn: sqlite3.Connection, today: date) -> _Computed:
    strategy, manual_order = _settings(conn)
    return _compute(
        conn,
        today,
        budget=debts.payoff_budget(conn),
        strategy=strategy,
        manual_order=manual_order,
        override=income_override(conn),
    )


def debt_order(conn: sqlite3.Connection, *, today: date | None = None) -> tuple[int, ...]:
    """The open debts in plan order; the first one is where extra money goes next.

    Contract for the allocation rules (-025): a ``debt_plan`` share pays this debt. Pure read —
    unlike ``status`` it syncs no special payment, so it is cheap enough to call per proposal. Debts
    the plan leaves out (already covered by payments planned before the plan starts) follow at the
    end, as in ``status``.
    """
    computed = _current(conn, today or date.today())
    order = computed.plan.order if computed.plan is not None else ()
    return (*order, *(debt_id for debt_id in computed.debts if debt_id not in order))


# --- special payments --------------------------------------------------------------------------


def _pay_account(conn: sqlite3.Connection, debt: debts.Debt) -> int | None:
    """The debt's paying account if it can take a booking, else None (no special payment planned)."""
    if debt.pay_account_id is None:
        return None
    account = accounts.get_account(conn, debt.pay_account_id)
    return None if account.archived_at is not None else account.id


def _wanted(
    conn: sqlite3.Connection, computed: _Computed, today: date
) -> tuple[dict[tuple[str, int], tuple[int, int]], list[int]]:
    """(month, debt) → (amount, paying account) of the special payments to keep planned, and the
    debts that cannot get one (no usable paying account)."""
    wanted: dict[tuple[str, int], tuple[int, int]] = {}
    missing: set[int] = set()
    if computed.plan is None:
        return wanted, []
    last = month_index(today) + HORIZON_MONTHS
    for month in computed.plan.months:
        index = computed.start + month.index
        if index > last:
            break
        for payment in month.payments:
            if not payment.extra_cents:
                continue
            pay_account = _pay_account(conn, computed.debts[payment.debt_id])
            if pay_account is None:
                missing.add(payment.debt_id)
                continue
            wanted[(month_key(index), payment.debt_id)] = (payment.extra_cents, pay_account)
    return wanted, sorted(missing)


def _transfer_fields(debt: debts.Debt, month: str, amount: int, pay_account: int) -> TransferFields:
    return TransferFields(
        date.fromisoformat(f"{month}-01"),
        amount,
        pay_account,
        debt.account_id,
        None,
        SPECIAL_PAYMENT_TEXT.format(creditor=debt.creditor),
    )


def _sync_transfers(conn: sqlite3.Connection, computed: _Computed, today: date) -> list[int]:
    """Make the planned special payments from the plan's first month on equal to the plan.

    Booked or skipped ones, and planned ones of the current or an earlier month, stay as they are
    (they are the user's to confirm or skip). One that was deleted by hand is forgotten here and
    created again when the plan still wants it. Returns the debts without a usable paying account.
    """
    wanted, missing = _wanted(conn, computed, today)
    start_key = month_key(computed.start)
    rows = conn.execute(
        "SELECT pt.transfer_group_id, pt.month, pt.debt_id, t.id, t.status, t.amount_cents, t.account_id"
        ' FROM payoff_transfer pt LEFT JOIN "transaction" t'
        " ON t.transfer_group_id = pt.transfer_group_id AND t.amount_cents < 0"
    ).fetchall()
    for group_id, month, debt_id, txn_id, status, amount, account_id in rows:
        if txn_id is None:
            conn.execute("DELETE FROM payoff_transfer WHERE transfer_group_id = ?", (group_id,))
            continue
        if month < start_key:
            continue
        key = (month, debt_id)
        if status != "planned":
            # Booked early or skipped by the user: that month's payment to this debt is settled,
            # creating a new one would pay it twice or undo the skip.
            wanted.pop(key, None)
            continue
        if key in wanted:
            target, pay_account = wanted.pop(key)
            if -amount != target or account_id != pay_account:
                transactions.update_transfer(
                    conn, group_id, _transfer_fields(computed.debts[debt_id], month, target, pay_account), today=today
                )
            continue
        transactions.delete_transaction(conn, txn_id)
        conn.execute("DELETE FROM payoff_transfer WHERE transfer_group_id = ?", (group_id,))
    for (month, debt_id), (amount, pay_account) in sorted(wanted.items()):
        source, _ = transactions.create_transfer(
            conn, _transfer_fields(computed.debts[debt_id], month, amount, pay_account), today=today
        )
        conn.execute(
            "INSERT INTO payoff_transfer (transfer_group_id, month, debt_id) VALUES (?, ?, ?)",
            (source.transfer_group_id, month, debt_id),
        )
    return missing


def sync_plan(conn: sqlite3.Connection, *, today: date | None = None) -> list[int]:
    """Bring the planned special payments in line with the current plan; a no-op before the first save.

    Runs on every status read, after saving and at app start. Returns the debts that get no
    special payment because they have no usable paying account.
    """
    today = today or date.today()
    if _plan_row(conn) is None:
        return []
    with transaction(conn):
        return _sync_transfers(conn, _current(conn, today), today)


def run_startup_sync(settings: Settings, *, today: date | None = None) -> bool:
    """Sync the special payments at app start, after the debts sync. Never raises."""
    try:
        conn = connect(settings.db_path)
        try:
            sync_plan(conn, today=today)
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("payoff plan sync at startup failed; the app starts without it")
        return False


# --- saving ------------------------------------------------------------------------------------


def _check(conn: sqlite3.Connection, fields: PlanFields, today: date) -> PlanFields:
    if fields.strategy not in STRATEGIES:
        raise PayoffError(N_("Choose smallest first, most expensive first or your own order."))
    if fields.budget_cents < 0:
        raise PayoffError(N_("The payoff budget cannot be negative."))
    if fields.income_override_cents is not None and fields.income_override_cents < 0:
        raise PayoffError(N_("The monthly income cannot be negative."))
    if len(set(fields.manual_order)) != len(fields.manual_order):
        raise PayoffError(N_("A debt appears twice in the order."))
    if set(fields.manual_order) - set(_open_debts(conn, today)):
        raise PayoffError(N_("The order names a debt that is not open."))
    return fields


def save_plan(conn: sqlite3.Connection, fields: PlanFields, *, today: date | None = None) -> Status:
    """Save budget, strategy, order and income override, then plan (UC steps 3-4).

    Writes the budget to ``debts.set_payoff_budget``, the new baseline for the months from the
    plan's first month on (earlier months keep theirs) and the special payments.
    """
    today = today or date.today()
    fields = _check(conn, fields, today)
    now = _now()
    with transaction(conn):
        debts.set_payoff_budget(conn, fields.budget_cents)
        set_setting(conn, INCOME_OVERRIDE_KEY, fields.income_override_cents)
        conn.execute(
            "INSERT INTO payoff_plan (id, strategy, manual_order, created_at, updated_at) VALUES (1, ?, ?, ?, ?)"
            " ON CONFLICT (id) DO UPDATE SET strategy = excluded.strategy, manual_order = excluded.manual_order,"
            " updated_at = excluded.updated_at",
            (fields.strategy, json.dumps(list(fields.manual_order)), now, now),
        )
        computed = _current(conn, today)
        start_key = month_key(computed.start)
        conn.execute("DELETE FROM payoff_month WHERE month >= ?", (start_key,))
        end = None
        if computed.plan is not None:
            conn.executemany(
                "INSERT INTO payoff_month (month, planned_cents, extra_cents) VALUES (?, ?, ?)",
                [
                    (
                        month_key(computed.start + month.index),
                        month.installments_cents + month.extra_cents,
                        month.extra_cents,
                    )
                    for month in computed.plan.months
                ],
            )
            if computed.plan.end is not None:
                end = month_key(computed.start + computed.plan.end)
        conn.execute(
            "UPDATE payoff_plan SET baseline_end_month = ?, baseline_available_cents = ? WHERE id = 1",
            (end, computed.availability.available_cents),
        )
        _sync_transfers(conn, computed, today)
    return status(conn, today=today)


# --- reading -----------------------------------------------------------------------------------


def _actual(conn: sqlite3.Connection, first: str, last: str) -> dict[str, int]:
    """Booked payments into the debts' liability accounts per month (instalments + special payments)."""
    rows = conn.execute(
        'SELECT substr(t.date, 1, 7), sum(t.amount_cents) FROM "transaction" t'
        " WHERE t.status = 'booked' AND t.amount_cents > 0 AND t.transfer_group_id IS NOT NULL"
        " AND t.account_id IN (SELECT account_id FROM debt) AND substr(t.date, 1, 7) BETWEEN ? AND ?"
        " GROUP BY 1",
        (first, last),
    )
    return {row[0]: row[1] for row in rows}


def _progress(conn: sqlite3.Connection, today: date) -> tuple[list[MonthProgress], int]:
    current = month_key(month_index(today))
    baseline = {
        row[0]: row[1]
        for row in conn.execute("SELECT month, planned_cents FROM payoff_month WHERE month <= ?", (current,))
    }
    if not baseline:
        return [], 0
    actual = _actual(conn, min(baseline), current)
    conn.executemany(
        "UPDATE payoff_month SET actual_cents = ? WHERE month = ?",
        [(actual.get(month, 0), month) for month in baseline],
    )
    return progress(baseline, actual, current)


def _ends(computed: _Computed) -> dict[int, str | None]:
    plan = computed.plan
    return {
        debt_id: (month_key(computed.start + plan.ends[debt_id]) if plan and debt_id in plan.ends else None)
        for debt_id in computed.debts
    }


def status(conn: sqlite3.Connection, *, today: date | None = None) -> Status:
    """Sync the special payments, then return the plan with its figures and progress (UC 2-6)."""
    today = today or date.today()
    row = _plan_row(conn)
    budget = debts.payoff_budget(conn)
    strategy, manual_order = _settings(conn)
    override = income_override(conn)
    with transaction(conn):
        computed = _current(conn, today)
        missing = _sync_transfers(conn, computed, today) if row is not None else []
        rows, catch_up = _progress(conn, today)
    plan, avail = computed.plan, computed.availability
    ends = _ends(computed)
    order = plan.order if plan is not None else tuple(computed.debts)
    lines = [
        DebtLine(
            debt.id,
            debt.creditor,
            debt.remaining_cents,
            debt.installment_cents,
            debt.interval,
            debt.interest_rate_pct,
            ends[debt.id],
        )
        for debt in (computed.debts[debt_id] for debt_id in order)
    ]
    # Debts paid off by pending payments before the plan starts are not in the order; list them too.
    lines += [
        DebtLine(d.id, d.creditor, d.remaining_cents, d.installment_cents, d.interval, d.interest_rate_pct, None)
        for d in computed.debts.values()
        if d.id not in order
    ]
    months = [
        MonthLine(
            month_key(computed.start + month.index),
            month.installments_cents,
            month.extra_cents,
            tuple((p.debt_id, p.extra_cents) for p in month.payments if p.extra_cents),
            month.remaining_cents,
        )
        for month in (plan.months if plan is not None else ())
    ]
    debt_free = month_key(computed.start + plan.end) if plan is not None and plan.end is not None else None
    baseline_end = row["baseline_end_month"] if row is not None else None
    shift = None
    if debt_free is not None and baseline_end is not None:
        shift = key_index(debt_free) - key_index(baseline_end)
    suggestion = None
    if row is not None and plan is not None and row["baseline_available_cents"] is not None:
        rise = avail.available_cents - row["baseline_available_cents"]
        if rise > 0:
            suggestion = budget + rise
    return Status(
        saved=row is not None,
        strategy=strategy,
        manual_order=manual_order,
        budget_cents=budget,
        total_monthly_cents=avail.installments_cents + budget,
        income_override_cents=override,
        availability=avail,
        remaining_total_cents=sum(d.remaining_cents for d in computed.debts.values()),
        has_plan=plan is not None,
        shortfall_cents=max(-avail.available_cents, 0),
        debts=lines,
        months=months,
        debt_free_month=debt_free,
        baseline_end_month=baseline_end,
        shift_months=shift,
        progress=rows,
        behind=catch_up > 0,
        catch_up_cents=catch_up,
        over_budget=plan is not None and budget > avail.available_cents,
        suggested_budget_cents=suggestion,
        free_now_cents=levy.free_amount(conn, levy.month_end(today), today=today),
        missing_pay_account=missing,
    )


def what_if(conn: sqlite3.Connection, delta_cents: int, *, today: date | None = None) -> WhatIf:
    """The plan with the budget changed by ``delta_cents`` (at least 0), without saving anything (AK5)."""
    today = today or date.today()
    strategy, manual_order = _settings(conn)
    budget = max(debts.payoff_budget(conn) + delta_cents, 0)
    computed = _compute(
        conn, today, budget=budget, strategy=strategy, manual_order=manual_order, override=income_override(conn)
    )
    plan = computed.plan
    return WhatIf(
        budget_cents=budget,
        total_monthly_cents=computed.availability.installments_cents + budget,
        has_plan=plan is not None,
        debt_free_month=month_key(computed.start + plan.end) if plan is not None and plan.end is not None else None,
        ends=_ends(computed),
    )
