"""Levy reserve (UC-ZF-015) and the free amount as plain functions over a connection.

Every business income booked today triggers income tax and SVS that the assessment of the
following summer asks for. This module turns that into figures:

* **Target** (``reserve_target``) = levy rate * base of the year; the base is the booked business
  income of the year (``income``) or the business balance (``profit``, income minus expenses), both
  read from the monthly report (-010), floored at 0.
* **Actual** = balance of the levy pot (-015) + prepayments of the year (``tax.prepayments_ytd``,
  -014); **gap** = max(0, target - actual). No levy pot → actual = prepayments (UC 2a).
* **Back-payment** (UC step 3, AK2): one planned expected item "Levy back-payment <year>" of
  -(target - prepayments) on the due date of the following year, kept in line by
  ``sync_backpayments``; ≤ 0 removes it.
* **Required revenue** (UC step 4, AK3): (fixed costs + instalments + payoff budget + pot rates +
  reserve build-up) ÷ (1 - rate), per month, with the split by block (UC 4a).
* **free_amount** — the core rule of the project, see its docstring.

The pure rules (rounding, target, per-year gaps, monthly equivalents, required revenue) are
tested without a database.

Invariants:

* The levy share of an income is reserved from the moment it is *booked*: ``levy_gap`` rises with
  the booking, not with a deposit into the levy pot, so ``free_amount`` never shows it as free.
* At most one back-payment item per year (``levy_backpayment.year`` is the primary key); an item
  removed outside the engine (confirmed, skipped, deleted) settles its year for good.

Errors are ``LevyError`` subclasses carrying an ``N_()`` msgid; ``AccountsError``,
``TransactionsError`` and ``RecurringError`` from the services called here pass through.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from math import floor
from typing import Any, Literal

from zfinance.config import Settings
from zfinance.db import connect, transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.debts import service as debts
from zfinance.domain.pots import service as pots
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.schedule import Interval, add_months, clamped
from zfinance.domain.reports import service as reports
from zfinance.domain.tax import service as tax
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.i18n import N_

logger = logging.getLogger(__name__)

Base = Literal["income", "profit"]
ChartArea = reports.Area

DEFAULT_DUE_MONTH_DAY = "07-01"
#: Highest accepted rate in basis points (90 %); the required revenue divides by (1 - rate).
MAX_RATE_BP = 9000
#: Months before and after the current month in the charts (12 back incl. the current one, 12 ahead).
CHART_MONTHS_BACK = 11
CHART_MONTHS_AHEAD = 12
#: Months over which a missing reserve floor is built up in the required revenue.
RESERVE_BUILD_MONTHS = 12
#: Text of the planned back-payment; data like the tax plan's texts, not a translated UI string.
BACKPAYMENT_TEXT = "Levy back-payment {year}"
_TAX_CATEGORY_NAMES = {"tax", "steuer"}
_DUE = re.compile(r"(\d{2})-(\d{2})")
_PER_MONTH: dict[Interval, Fraction] = {
    "weekly": Fraction(52, 12),
    "monthly": Fraction(1),
    "quarterly": Fraction(1, 3),
    "yearly": Fraction(1, 12),
}


# --- errors ------------------------------------------------------------------------------------


class LevyError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(LevyError):
    """A referenced pot or back-payment does not exist."""

    status_code = 404


class Conflict(LevyError):
    """The request contradicts the stored state."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LevyFields:
    """What the user enters (full replacement). ``levy_rate_bp`` is basis points: 3500 = 35 %."""

    levy_rate_bp: int
    levy_base: Base = "income"
    due_month_day: str = DEFAULT_DUE_MONTH_DAY
    levy_pot_id: int | None = None
    account_id: int | None = None


@dataclass(frozen=True, kw_only=True)
class LevySettings(LevyFields):
    """The stored settings; ``settings_missing`` while nothing was saved (rate 0, nothing reserved)."""

    settings_missing: bool


@dataclass(frozen=True)
class YearFigures:
    """Input of the per-year gap: target and prepayments of one open year."""

    year: int
    target_cents: int
    prepayments_cents: int


@dataclass(frozen=True)
class YearGap:
    """One open year after the levy pot was attributed (oldest year first, the newest takes the rest)."""

    year: int
    target_cents: int
    prepayments_cents: int
    pot_share_cents: int
    actual_cents: int
    gap_cents: int


@dataclass(frozen=True, kw_only=True)
class YearStatus(YearGap):
    """A year on the levy screen: base, gap and its back-payment item."""

    base_cents: int
    backpayment_cents: int
    backpayment_date: date
    backpayment_transaction_id: int | None
    backpayment_state: Literal["open", "settled", "archived"]


@dataclass(frozen=True)
class LevyStatus:
    """What UC step 2 shows, for every open year (the previous one until its back-payment is done)."""

    settings: LevySettings
    levy_pot_id: int | None
    levy_pot_missing: bool
    levy_pot_ambiguous: bool
    pot_balance_cents: int
    years: list[YearStatus]
    levy_gap_cents: int
    free_balance_cents: int
    levy_uncovered: bool
    backpayment_account_missing: bool


@dataclass(frozen=True)
class FreeAmount:
    """The terms of ``free_amount`` on ``as_of``; ``free_cents`` is the result."""

    as_of: date
    accounts_cents: int
    planned_cents: int
    reserve_cents: int
    levy_gap_cents: int
    free_cents: int


@dataclass(frozen=True)
class RevenueBlocks:
    """Monthly costs the revenue must carry, before levies (exact fractions of a cent)."""

    fixed_costs: Fraction = Fraction(0)
    installments: Fraction = Fraction(0)
    payoff_budget: Fraction = Fraction(0)
    pot_rates: Fraction = Fraction(0)
    reserve_build: Fraction = Fraction(0)

    def total(self) -> Fraction:
        """Sum of all blocks."""
        return self.fixed_costs + self.installments + self.payoff_budget + self.pot_rates + self.reserve_build


@dataclass(frozen=True)
class RequiredRevenue:
    """Revenue needed per month (UC step 4) with its split by block (UC 4a); the parts sum to the total."""

    required_cents: int
    fixed_costs_cents: int
    installments_cents: int
    payoff_budget_cents: int
    pot_rates_cents: int
    reserve_build_cents: int
    levy_cents: int
    levy_rate_bp: int


@dataclass(frozen=True)
class IncomeMonth:
    """One bar of the income chart: booked and planned business income against the need."""

    month: str
    actual_cents: int
    planned_cents: int
    required_cents: int
    below_required: bool


@dataclass(frozen=True)
class CategoryAmount:
    """Expenses of one category in a month, positive cents."""

    category_id: int | None
    category_name: str | None
    amount_cents: int


@dataclass(frozen=True)
class ExpenseMonth:
    """One month of the expense chart; ``planned`` months (after the current one) show planned rows."""

    month: str
    planned: bool
    categories: list[CategoryAmount] = field(default_factory=list)
    liabilities_cents: int = 0
    remaining_debt_cents: int = 0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- pure rules --------------------------------------------------------------------------------


def half_up(value: Fraction) -> int:
    """Round to whole cents, half away from zero."""
    return floor(value + Fraction(1, 2)) if value >= 0 else -floor(-value + Fraction(1, 2))


def reserve_target(rate_bp: int, base_cents: int) -> int:
    """Rate * base in cents, half up; a negative base (a loss) reserves nothing."""
    return half_up(Fraction(rate_bp * max(base_cents, 0), 10_000))


def year_gaps(years: Sequence[YearFigures], pot_cents: int) -> list[YearGap]:
    """Attribute the levy pot to the open years, oldest first, and compute each year's gap.

    An older year takes from the pot only what its target still needs after its prepayments; the
    newest year takes the rest, so with one open year ``actual = pot + prepayments`` and
    ``gap = max(0, target - actual)`` — the SPEC formula. A negative pot counts as 0.
    """
    remaining = max(pot_cents, 0)
    result: list[YearGap] = []
    ordered = sorted(years, key=lambda item: item.year)
    for index, item in enumerate(ordered):
        need = max(item.target_cents - item.prepayments_cents, 0)
        share = remaining if index == len(ordered) - 1 else min(remaining, need)
        remaining -= share
        actual = share + item.prepayments_cents
        result.append(
            YearGap(
                item.year, item.target_cents, item.prepayments_cents, share, actual, max(item.target_cents - actual, 0)
            )
        )
    return result


def monthly_equivalent(interval: Interval, amount_cents: int) -> Fraction:
    """The monthly share of a recurring amount (weekly * 52 ÷ 12, quarterly ÷ 3, yearly ÷ 12)."""
    return _PER_MONTH[interval] * amount_cents


def required_revenue(blocks: RevenueBlocks, rate_bp: int) -> RequiredRevenue:
    """(Σ blocks) ÷ (1 - rate), half up; the levy part is what is left after the rounded blocks.

    AK3: 2,000 + 500 + 300 + 200 = 3,000 € at 35 % → 3,000 ÷ 0.65 = 4,615.38 → 4,615 €.
    """
    required = half_up(blocks.total() / (1 - Fraction(rate_bp, 10_000)))
    fixed, installments, payoff, pot_rates, reserve = (
        half_up(blocks.fixed_costs),
        half_up(blocks.installments),
        half_up(blocks.payoff_budget),
        half_up(blocks.pot_rates),
        half_up(blocks.reserve_build),
    )
    return RequiredRevenue(
        required_cents=required,
        fixed_costs_cents=fixed,
        installments_cents=installments,
        payoff_budget_cents=payoff,
        pot_rates_cents=pot_rates,
        reserve_build_cents=reserve,
        levy_cents=required - (fixed + installments + payoff + pot_rates + reserve),
        levy_rate_bp=rate_bp,
    )


def parse_due_month_day(text: str) -> str:
    """Validate an ``MM-DD`` due date (day 31 = end of the month) and return it normalized."""
    match = _DUE.fullmatch(text.strip())
    if match is None or not (1 <= int(match[1]) <= 12 and 1 <= int(match[2]) <= 31):
        raise LevyError(N_("Enter the due date as month and day, e.g. 07-01."))
    return f"{int(match[1]):02d}-{int(match[2]):02d}"


def due_date(due_month_day: str, year: int) -> date:
    """The back-payment date for the levies of ``year``: the due date in the following year."""
    return clamped(year + 1, int(due_month_day[:2]), int(due_month_day[3:]))


def month_end(day: date) -> date:
    """The last day of the month of ``day`` (the dashboard's "free until the end of the month")."""
    return add_months(day.replace(day=1), 1) - timedelta(days=1)


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def chart_months(today: date) -> list[date]:
    """First days of the 24 chart months: 11 before the current month, the current one, 12 after."""
    first = today.replace(day=1)
    return [add_months(first, offset) for offset in range(-CHART_MONTHS_BACK, CHART_MONTHS_AHEAD + 1)]


# --- settings ----------------------------------------------------------------------------------


def get_settings(conn: sqlite3.Connection) -> LevySettings:
    """The stored settings, or rate 0 with the defaults while nothing is saved."""
    row = conn.execute("SELECT * FROM levy_settings WHERE id = 1").fetchone()
    if row is None:
        return LevySettings(levy_rate_bp=0, settings_missing=True)
    return LevySettings(
        levy_rate_bp=row["levy_rate_bp"],
        levy_base=row["levy_base"],
        due_month_day=row["due_month_day"],
        levy_pot_id=row["levy_pot_id"],
        account_id=row["account_id"],
        settings_missing=False,
    )


def _check(conn: sqlite3.Connection, fields: LevyFields) -> LevyFields:
    if not 0 <= fields.levy_rate_bp <= MAX_RATE_BP:
        raise LevyError(N_("Enter a levy rate between 0 and 90 %."))
    due = parse_due_month_day(fields.due_month_day)
    if fields.levy_pot_id is not None:
        pot = conn.execute("SELECT name, closed_at FROM pot WHERE id = ?", (fields.levy_pot_id,)).fetchone()
        if pot is None:
            raise NotFound(N_("Pot not found."))
        if pot["closed_at"] is not None:
            raise Conflict(N_("The pot %(name)s is closed."), name=pot["name"])
    if fields.account_id is not None:
        account = accounts.get_account(conn, fields.account_id)
        if account.archived_at is not None:
            raise Conflict(N_("The account %(name)s is archived."), name=account.name)
        if account.type == "liability":
            raise Conflict(N_("The back-payment cannot be planned on a liability account."))
    return LevyFields(fields.levy_rate_bp, fields.levy_base, due, fields.levy_pot_id, fields.account_id)


def save_settings(conn: sqlite3.Connection, fields: LevyFields, *, today: date | None = None) -> LevySettings:
    """Store the settings and bring the back-payment items in line at once (UC step 6, AK5)."""
    today = today or date.today()
    fields = _check(conn, fields)
    with transaction(conn):
        conn.execute(
            "INSERT INTO levy_settings (id, levy_rate_bp, levy_base, due_month_day, levy_pot_id, account_id,"
            " updated_at) VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET"
            " levy_rate_bp = excluded.levy_rate_bp, levy_base = excluded.levy_base,"
            " due_month_day = excluded.due_month_day, levy_pot_id = excluded.levy_pot_id,"
            " account_id = excluded.account_id, updated_at = excluded.updated_at",
            (
                fields.levy_rate_bp,
                fields.levy_base,
                fields.due_month_day,
                fields.levy_pot_id,
                fields.account_id,
                _now(),
            ),
        )
        sync_backpayments(conn, today=today)
    return get_settings(conn)


# --- reading -----------------------------------------------------------------------------------


def levy_pot(conn: sqlite3.Connection, settings: LevySettings | None = None) -> tuple[int | None, bool]:
    """(pot id, ambiguous): the chosen pot if open, else the open pot flagged ``is_levy_pot``.

    Several flagged pots without a choice: the lowest id, and ``ambiguous`` is true.
    """
    settings = settings or get_settings(conn)
    if settings.levy_pot_id is not None:
        row = conn.execute("SELECT id FROM pot WHERE id = ? AND closed_at IS NULL", (settings.levy_pot_id,)).fetchone()
        if row is not None:
            return row[0], False
    ids = [r[0] for r in conn.execute("SELECT id FROM pot WHERE is_levy_pot = 1 AND closed_at IS NULL ORDER BY id")]
    return (ids[0] if ids else None), len(ids) > 1


def _months_until(year: int, on: date) -> Iterable[str]:
    """``YYYY-MM`` keys of ``year`` up to the month of ``on`` (all twelve for an earlier year)."""
    if year > on.year:
        return []
    last = on.month if year == on.year else 12
    return [f"{year:04d}-{month:02d}" for month in range(1, last + 1)]


def business_income_ytd(conn: sqlite3.Connection, year: int, on: date) -> int:
    """Booked business income of ``year`` up to the month of ``on``, from the monthly report (-010).

    A booking's area is its account's area; transfers (owner draw/contribution) are not income.
    """
    return sum(reports.month_report(conn, key, "business").income.amount_cents for key in _months_until(year, on))


def levy_base_ytd(conn: sqlite3.Connection, year: int, on: date, base: Base) -> int:
    """The base of ``year``: business income, or business income + expenses (profit), from the report."""
    if base == "income":
        return business_income_ytd(conn, year, on)
    return sum(reports.month_report(conn, key, "business").balance.amount_cents for key in _months_until(year, on))


def _backpayment_rows(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {row["year"]: row for row in conn.execute("SELECT * FROM levy_backpayment")}


def _open_years(settings: LevySettings, rows: dict[int, sqlite3.Row], on: date) -> list[int]:
    """The current year of ``on`` and the previous one while its back-payment is not done."""
    years = [on.year]
    previous = rows.get(on.year - 1)
    done = previous is not None and (previous["settled_at"] is not None or previous["archived_at"] is not None)
    still_planned = previous is not None and previous["transaction_id"] is not None
    if not done and (due_date(settings.due_month_day, on.year - 1) > on or still_planned):
        years.insert(0, on.year - 1)
    return years


def _year_gaps(
    conn: sqlite3.Connection, settings: LevySettings, on: date, pot_cents: int
) -> tuple[list[YearGap], dict[int, int], dict[int, sqlite3.Row]]:
    rows = _backpayment_rows(conn)
    bases: dict[int, int] = {}
    figures: list[YearFigures] = []
    for year in _open_years(settings, rows, on):
        bases[year] = levy_base_ytd(conn, year, on, settings.levy_base)
        target = reserve_target(settings.levy_rate_bp, bases[year])
        figures.append(YearFigures(year, target, tax.prepayments_ytd(conn, year)))
    return year_gaps(figures, pot_cents), bases, rows


def levy_gap(conn: sqlite3.Connection, as_of: date) -> int:
    """The open levy gap on ``as_of``: Σ over the open years of max(0, target - (pot + prepayments))."""
    settings = get_settings(conn)
    pot_id, _ = levy_pot(conn, settings)
    pot_cents = pots.pot_balance(conn, pot_id, as_of) if pot_id is not None else 0
    gaps, _, _ = _year_gaps(conn, settings, as_of, pot_cents)
    return sum(item.gap_cents for item in gaps)


def _payment_accounts(conn: sqlite3.Connection) -> list[accounts.Account]:
    """Active accounts except liabilities: where money to spend is."""
    return [account for account in accounts.list_accounts(conn) if account.type != "liability"]


def _backpayment_account(conn: sqlite3.Connection, settings: LevySettings, pot_id: int | None) -> int | None:
    """The account of the back-payment: settings, else the tax plan's business account, else the pot's."""
    candidates = [settings.account_id, tax.get_settings(conn).business_account_id]
    if pot_id is not None:
        candidates.append(conn.execute("SELECT account_id FROM pot WHERE id = ?", (pot_id,)).fetchone()[0])
    for account_id in candidates:
        if account_id is None:
            continue
        account = accounts.get_account(conn, account_id)
        if account.archived_at is None and account.type != "liability":
            return account_id
    return None


def levy_status(conn: sqlite3.Connection, *, today: date | None = None) -> LevyStatus:
    """Target, actual and gap per open year, the back-payments and the warnings (UC step 2, 2a, 2b).

    Pure read; call ``sync_backpayments`` first to have the back-payment items up to date.
    """
    today = today or date.today()
    settings = get_settings(conn)
    pot_id, ambiguous = levy_pot(conn, settings)
    pot_cents = pots.pot_balance(conn, pot_id, today) if pot_id is not None else 0
    gaps, bases, rows = _year_gaps(conn, settings, today, pot_cents)
    years: list[YearStatus] = []
    for item in gaps:
        row = rows.get(item.year)
        state: Literal["open", "settled", "archived"] = "open"
        if row is not None and row["archived_at"] is not None:
            state = "archived"
        elif row is not None and row["settled_at"] is not None:
            state = "settled"
        years.append(
            YearStatus(
                **vars(item),
                base_cents=bases[item.year],
                backpayment_cents=max(item.target_cents - item.prepayments_cents, 0),
                backpayment_date=due_date(settings.due_month_day, item.year),
                backpayment_transaction_id=row["transaction_id"] if row is not None else None,
                backpayment_state=state,
            )
        )
    gap = sum(item.gap_cents for item in gaps)
    free = sum(pots.free_balance(conn, account.id, today) for account in _payment_accounts(conn))
    return LevyStatus(
        settings=settings,
        levy_pot_id=pot_id,
        levy_pot_missing=pot_id is None,
        levy_pot_ambiguous=ambiguous,
        pot_balance_cents=pot_cents,
        years=years,
        levy_gap_cents=gap,
        free_balance_cents=free,
        levy_uncovered=gap > max(free, 0),
        backpayment_account_missing=not settings.settings_missing
        and any(y.backpayment_cents > 0 and y.backpayment_state == "open" for y in years)
        and _backpayment_account(conn, settings, pot_id) is None,
    )


# --- the core rule -----------------------------------------------------------------------------

_PLANNED_SQL = (
    'SELECT coalesce(sum(t.amount_cents), 0) FROM "transaction" t JOIN account a ON a.id = t.account_id'
    " WHERE t.status = 'planned' AND t.date <= ? AND a.type != 'liability' AND a.archived_at IS NULL"
    " AND (t.amount_cents < 0 OR t.transfer_group_id IS NOT NULL)"
    " AND t.id NOT IN (SELECT transaction_id FROM levy_backpayment WHERE transaction_id IS NOT NULL)"
    " AND t.id NOT IN (SELECT planned_transaction_id FROM pot WHERE planned_transaction_id IS NOT NULL)"
)
_POT_EXPENSES_SQL = (
    'SELECT p.id, t.amount_cents FROM pot p JOIN "transaction" t ON t.id = p.planned_transaction_id'
    " JOIN account a ON a.id = t.account_id"
    " WHERE t.status = 'planned' AND t.date <= ? AND a.type != 'liability' AND a.archived_at IS NULL"
)


def free_amount_breakdown(conn: sqlite3.Connection, as_of: date, *, today: date | None = None) -> FreeAmount:
    """The terms of ``free_amount`` (see there); the dashboard shows ``levy_gap_cents`` beside it (NF5)."""
    today = today or date.today()
    if as_of < today:
        raise LevyError(N_("The date must be today or later."))
    payment = _payment_accounts(conn)
    balances = sum(pots.free_balance(conn, account.id, today) for account in payment)
    planned = int(conn.execute(_PLANNED_SQL, (as_of.isoformat(),)).fetchone()[0])
    for pot_id, amount in conn.execute(_POT_EXPENSES_SQL, (as_of.isoformat(),)).fetchall():
        # A pot's planned purchase is paid from the pot first; only the part it does not hold is due.
        planned -= max(-amount - max(pots.pot_balance(conn, pot_id, today), 0), 0)
    reserve = sum(account.reserve_floor_cents for account in payment)
    gap = levy_gap(conn, as_of)
    return FreeAmount(as_of, balances, planned, reserve, gap, balances + planned - reserve - gap)


def free_amount(conn: sqlite3.Connection, as_of: date, *, today: date | None = None) -> int:
    """The one "free" number of z-finance in cents: what can be spent up to ``as_of`` (≥ today).

    Invariant (core rule, ``SPEC-z-finance.md`` "Kernregel", UC NF5): the levy share of an income
    is reserved from its booking on and is never free, also before it is moved into the levy pot::

        free_amount = Σ payment accounts - planned items up to as_of - reserve - levy_gap

    * **Σ payment accounts**: ``pots.free_balance`` on today of every active account except
      liabilities — so the money in *every* pot (the levy pot included) is already out.
    * **Planned items**: planned rows of those accounts dated up to ``as_of``, signed. Transfer
      pairs between two payment accounts cancel; an instalment into a liability account counts.
      Not counted: planned income (it counts once booked, and then net of the levy), the levy
      back-payment items (covered by pot + gap; counting them would take the levy twice), and
      the part of a pot's planned purchase that the pot holds.
    * **Reserve**: the reserve floors of those accounts (UC-ZF-001).
    * **levy_gap**: ``levy_gap(conn, as_of)``.

    Example: 5,000 € on the giro, no plan, rate 30 %, then an income of 10,000 € is booked:
    target and gap rise by 3,000 €, ``free_amount`` goes from 5,000 € to 12,000 € (+7,000 €, AK7).

    Dashboard, forecast, allocation (-025) and payoff plan (-021/-026) all read this function.
    """
    return free_amount_breakdown(conn, as_of, today=today).free_cents


# --- back-payment ------------------------------------------------------------------------------


def _tax_category(conn: sqlite3.Connection) -> int | None:
    """An existing top-level expense category "Tax"/"Steuer" (the tax plan's), else none."""
    for category in accounts.list_categories(conn):
        if (
            category.kind == "expense"
            and category.parent_id is None
            and category.name.casefold() in _TAX_CATEGORY_NAMES
        ):
            return category.id
    return None


def _store(
    conn: sqlite3.Connection, year: int, transaction_id: int | None, estimate: int, *, settled: bool = False
) -> None:
    conn.execute(
        "INSERT INTO levy_backpayment (year, transaction_id, estimate_cents, settled_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?) ON CONFLICT(year) DO UPDATE SET transaction_id = excluded.transaction_id,"
        " estimate_cents = excluded.estimate_cents, settled_at = coalesce(levy_backpayment.settled_at,"
        " excluded.settled_at), updated_at = excluded.updated_at",
        (year, transaction_id, estimate, _now() if settled else None, _now()),
    )


def _sync_year(conn: sqlite3.Connection, settings: LevySettings, year: int, today: date) -> None:
    row = conn.execute("SELECT * FROM levy_backpayment WHERE year = ?", (year,)).fetchone()
    if row is not None and (row["settled_at"] is not None or row["archived_at"] is not None):
        return
    existing = None
    if row is not None and row["transaction_id"] is not None:
        existing = transactions.get_transaction(conn, row["transaction_id"])
        if existing.status != "planned":  # skipped (or otherwise no longer planned): done
            _store(conn, year, existing.id, row["estimate_cents"], settled=True)
            return
    elif row is not None and row["estimate_cents"] > 0:  # the planned row went outside the engine
        _store(conn, year, None, row["estimate_cents"], settled=True)
        return
    due = due_date(settings.due_month_day, year)
    if due <= today and existing is None:
        return  # the date has passed without an item; nothing is planned in the past
    base = levy_base_ytd(conn, year, today, settings.levy_base)
    amount = reserve_target(settings.levy_rate_bp, base) - tax.prepayments_ytd(conn, year)
    if amount <= 0:
        if existing is not None:
            transactions.delete_transaction(conn, existing.id)
        _store(conn, year, None, 0)
        return
    pot_id, _ = levy_pot(conn, settings)
    account_id = _backpayment_account(conn, settings, pot_id)
    if account_id is None:
        return
    fields = BookingFields(
        max(due, today + timedelta(days=1)),
        -amount,
        account_id,
        _tax_category(conn),
        BACKPAYMENT_TEXT.format(year=year),
    )
    if existing is None:
        created = recurring.create_expected(conn, fields, today=today)
        _store(conn, year, created.id, amount)
    else:
        if (existing.date, existing.amount_cents, existing.account_id, existing.text) != (
            fields.date,
            fields.amount_cents,
            fields.account_id,
            fields.text,
        ):
            transactions.update_transaction(
                conn,
                existing.id,
                BookingFields(
                    fields.date,
                    fields.amount_cents,
                    fields.account_id,
                    existing.category_id,
                    fields.text,
                    existing.counterparty,
                    existing.memo,
                ),
                today=today,
            )
        _store(conn, year, existing.id, amount)


def sync_backpayments(conn: sqlite3.Connection, *, today: date | None = None) -> None:
    """Keep one planned back-payment per open year equal to target - prepayments (UC step 3, AK2).

    Runs for the previous and the current year: creates the expected item on the due date of the
    following year (``recurring.create_expected``), rewrites it when target, prepayments, date or
    account changed (``transactions.update_transaction``), removes it at an amount ≤ 0
    (``transactions.delete_transaction``). The levy settings are the source: a hand edit of the item
    is put back. An item confirmed, skipped or deleted outside the engine settles its year; it is
    never created again. Idempotent; runs in one database transaction. Without saved settings
    nothing happens.
    """
    today = today or date.today()
    settings = get_settings(conn)
    if settings.settings_missing:
        return
    with transaction(conn):
        for year in (today.year - 1, today.year):
            _sync_year(conn, settings, year, today)


def archive_backpayment(conn: sqlite3.Connection, year: int) -> None:
    """The assessment is in (UC 3a): keep the estimate, remove the planned item, settle the year.

    The real amount is entered as the tax plan's back-payment (UC-ZF-008) or an expected item.
    """
    row = conn.execute("SELECT * FROM levy_backpayment WHERE year = ?", (year,)).fetchone()
    if row is None:
        raise NotFound(N_("No back-payment is planned for %(year)s."), year=year)
    if row["archived_at"] is not None or row["settled_at"] is not None:
        raise Conflict(N_("The back-payment for %(year)s is already done."), year=year)
    with transaction(conn):
        if row["transaction_id"] is not None:
            transactions.delete_transaction(conn, row["transaction_id"])
        conn.execute(
            "UPDATE levy_backpayment SET transaction_id = NULL, archived_at = ?, updated_at = ? WHERE year = ?",
            (_now(), _now(), year),
        )


def run_startup_backpayment_sync(settings: Settings, *, today: date | None = None) -> bool:
    """Sync the back-payment items at app start, after the recurring generation. Never raises."""
    try:
        conn = connect(settings.db_path)
        try:
            sync_backpayments(conn, today=today)
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("levy back-payment sync at startup failed; the app starts without it")
        return False


# --- required revenue --------------------------------------------------------------------------


def revenue_blocks(conn: sqlite3.Connection, *, today: date | None = None) -> RevenueBlocks:
    """The monthly blocks the revenue must carry (UC step 4), from the recurring items and settings.

    * fixed costs: active recurring expenses (no transfer), except the tax plan's items — the levy
      is what the division by (1 - rate) covers;
    * instalments: recurring transfers into a liability account (the debts, -013);
    * payoff budget: ``debts.payoff_budget``;
    * pot rates: savings plans (``recurring.pot_id``) except the levy pot's;
    * reserve build-up: Σ max(0, reserve floor - free balance) of the payment accounts ÷ 12.
    """
    today = today or date.today()
    tax_items = {term.recurring_id for term in tax.get_settings(conn).terms}
    liabilities = {a.id for a in accounts.list_accounts(conn, include_archived=True) if a.type == "liability"}
    levy_pot_id, _ = levy_pot(conn)
    fixed = installments = pot_rates = Fraction(0)
    for item in recurring.list_recurring(conn):
        if not item.active or (item.end_date is not None and item.end_date < today):
            continue
        monthly = monthly_equivalent(item.interval, abs(item.amount_cents))
        if item.transfer_target_account_id in liabilities:
            installments += monthly
        elif item.pot_id is not None:
            if item.pot_id != levy_pot_id:
                pot_rates += monthly
        elif item.transfer_target_account_id is None and item.amount_cents < 0 and item.id not in tax_items:
            fixed += monthly
    missing = sum(
        max(account.reserve_floor_cents - pots.free_balance(conn, account.id, today), 0)
        for account in _payment_accounts(conn)
    )
    return RevenueBlocks(
        fixed_costs=fixed,
        installments=installments,
        payoff_budget=Fraction(debts.payoff_budget(conn)),
        pot_rates=pot_rates,
        reserve_build=Fraction(missing, RESERVE_BUILD_MONTHS),
    )


def required_revenue_monthly(conn: sqlite3.Connection, *, today: date | None = None) -> RequiredRevenue:
    """Revenue needed per month at the current levy rate (UC step 4, AK3, AK5)."""
    return required_revenue(revenue_blocks(conn, today=today), get_settings(conn).levy_rate_bp)


# --- chart data --------------------------------------------------------------------------------

_PLANNED_INCOME_SQL = (
    'SELECT substr(t.date, 1, 7) AS ym, sum(t.amount_cents) AS total FROM "transaction" t'
    " JOIN account a ON a.id = t.account_id WHERE t.status = 'planned' AND t.amount_cents > 0"
    " AND t.transfer_group_id IS NULL AND a.area = 'business' AND t.date >= ? AND t.date < ? GROUP BY ym"
)
_PLANNED_EXPENSES_SQL = (
    'SELECT substr(t.date, 1, 7) AS ym, t.category_id, sum(t.amount_cents) AS total FROM "transaction" t'
    " JOIN account a ON a.id = t.account_id WHERE t.status = 'planned' AND t.amount_cents < 0"
    " AND t.transfer_group_id IS NULL AND (? = 'all' OR a.area = ?) AND t.date >= ? AND t.date < ?"
    " GROUP BY ym, t.category_id"
)
_LIABILITY_IN_SQL = (
    'SELECT substr(t.date, 1, 7) AS ym, sum(t.amount_cents) AS total FROM "transaction" t'
    " JOIN account a ON a.id = t.account_id WHERE a.type = 'liability' AND t.amount_cents > 0"
    " AND t.transfer_group_id IS NOT NULL AND t.status IN ('booked', 'planned') AND (? = 'all' OR a.area = ?)"
    " AND t.date >= ? AND t.date < ? GROUP BY ym"
)
_LIABILITY_PLANNED_SQL = (
    'SELECT coalesce(sum(t.amount_cents), 0) FROM "transaction" t'
    " WHERE t.account_id = ? AND t.status = 'planned' AND t.date <= ?"
)


def income_chart(conn: sqlite3.Connection, *, today: date | None = None) -> list[IncomeMonth]:
    """24 months of business income (booked, and planned expected items) against the required revenue.

    A month is ``below_required`` when booked + planned income is under the need (AK4); with no
    need (0) no month is marked. The need is today's figure for every month.
    """
    today = today or date.today()
    months = chart_months(today)
    required = required_revenue_monthly(conn, today=today).required_cents
    window = (months[0].isoformat(), add_months(months[-1], 1).isoformat())
    planned = {row["ym"]: row["total"] for row in conn.execute(_PLANNED_INCOME_SQL, window)}
    current = today.replace(day=1)
    result = []
    for first in months:
        key = _month_key(first)
        actual = reports.month_report(conn, key, "business").income.amount_cents if first <= current else 0
        plan = planned.get(key, 0)
        result.append(IncomeMonth(key, actual, plan, required, required > 0 and actual + plan < required))
    return result


def _category_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {category.id: category.name for category in accounts.list_categories(conn)}


def expense_chart(
    conn: sqlite3.Connection, area: ChartArea = "all", *, today: date | None = None
) -> list[ExpenseMonth]:
    """24 months of expenses by category beside the liabilities line and the remaining debt (UC step 5).

    Up to the current month the expenses are the monthly report's booked expense lines (-010);
    later months show planned expenses. ``liabilities_cents`` = payments into liability accounts
    (instalments and special payments, booked or planned); ``remaining_debt_cents`` = what the
    liability accounts still owe at the end of the month, with planned payments for future months.
    """
    today = today or date.today()
    months = chart_months(today)
    current = today.replace(day=1)
    window = (months[0].isoformat(), add_months(months[-1], 1).isoformat())
    names = _category_names(conn)
    planned: dict[str, list[CategoryAmount]] = {}
    for row in conn.execute(_PLANNED_EXPENSES_SQL, (area, area, *window)):
        planned.setdefault(row["ym"], []).append(
            CategoryAmount(row["category_id"], names.get(row["category_id"]), -row["total"])
        )
    paid = {row["ym"]: row["total"] for row in conn.execute(_LIABILITY_IN_SQL, (area, area, *window))}
    liabilities = [
        a
        for a in accounts.list_accounts(conn, include_archived=True)
        if a.type == "liability" and area in ("all", a.area)
    ]
    result = []
    for first in months:
        key = _month_key(first)
        end = month_end(first)
        if first <= current:
            report = reports.month_report(conn, key, area)
            categories = [
                CategoryAmount(line.category_id, line.category_name, -line.comparison.amount_cents)
                for line in report.lines
                if line.flow == "expense" and line.comparison.amount_cents
            ]
        else:
            categories = sorted(planned.get(key, []), key=lambda item: -item.amount_cents)
        remaining = 0
        for account in liabilities:
            owed = -accounts.balance_at(conn, account.id, end)
            if end > today:
                owed -= int(conn.execute(_LIABILITY_PLANNED_SQL, (account.id, end.isoformat())).fetchone()[0])
            remaining += max(owed, 0)
        result.append(ExpenseMonth(key, first > current, categories, paid.get(key, 0), remaining))
    return result
