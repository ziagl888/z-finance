"""Liquidity forecast (UC-ZF-006) as plain functions over a connection.

The forecast answers "where does my balance go in the next 3, 6 or 12 months, when is it lowest,
and does it fall below the reserve?" for one account, one area or all payment accounts:

* **Curve** (``balance_cents``) = balance at the end of today + the rows that are still to come,
  day by day: planned rows on their date, planned rows dated before today on today (overdue, UC
  2b, not ignored), booked rows dated after today on their date. The daily sums come from one
  ``GROUP BY`` query, so the cost does not grow with the number of days (AK5).
* **Available after levies** (``available_cents``) = curve - the open levy gap (UC step 3,
  ``levy.levy_status``). Minimum, first day below the reserve and the shortfall refer to this line.
* **Reserve**: the account's floor, the sum of the floors of an area, or the total reserve in the
  total view (UC TBD-5, SPEC AK12); 0 means "not set" and never raises an alarm (UC 3a).
* **what_if** (UC 1a): hypothetical items join the curve in memory and are never stored.
* **items_at** (UC step 4): the rows that act on one day of the curve.
* **emergency_suggestion** (UC 3b): which pots in emergency order would cover the shortfall.

Which rows count needs no special case: a transfer leg counts on its account, so a pair inside
the view cancels (UC 2a); liability accounts are not part of any view, so an instalment is an
outflow of the paying account only (UC 2c); a pot's planned purchase is a planned row on the
target date, its savings plan a transfer and a deposit from the free balance no booking (UC 2d).

Invariants:

* ``forecast``, ``what_if`` and ``items_at`` only read. ``emergency_suggestion`` reads through
  ``pots.emergency_proposal``, which collects booked savings-plan deposits like every pot read.
* The planned back-payment item of a levy year is never subtracted on top of that year's gap:
  before its date the line is curve - gap, from its date on the curve holds the item and the
  year's gap is no longer subtracted (the rule of ``levy.free_amount``, see ``_levy_steps``).

Errors are ``ForecastError`` subclasses carrying an ``N_()`` msgid; ``AccountsError`` passes through.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import Area
from zfinance.domain.levy import service as levy
from zfinance.domain.pots import service as pots
from zfinance.domain.recurring.schedule import add_months
from zfinance.i18n import N_

Scope = Literal["account", "area", "total"]
ItemKind = Literal["planned", "booked", "what_if"]

#: The horizons the UC offers, in months.
HORIZONS = (3, 6, 12)
DEFAULT_HORIZON = 12


# --- errors ------------------------------------------------------------------------------------


class ForecastError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class Conflict(ForecastError):
    """The chosen view contradicts the stored state (archived or liability account)."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    """What the forecast looks at: one account, else one area, else all payment accounts."""

    account_id: int | None = None
    area: Area | None = None

    @property
    def scope(self) -> Scope:
        """``account`` wins over ``area``; neither is the total view."""
        if self.account_id is not None:
            return "account"
        return "area" if self.area is not None else "total"


@dataclass(frozen=True)
class WhatIfItem:
    """A hypothetical item (UC 1a): signed cents on an account and a day from today on."""

    date: date
    amount_cents: int
    account_id: int
    text: str = ""


@dataclass(frozen=True)
class ForecastDay:
    """One point of the curve: the balance and the line after levies at the end of the day."""

    date: date
    balance_cents: int
    available_cents: int


@dataclass(frozen=True)
class GapStep:
    """The open levy gap per year, valid from ``start`` until the next step."""

    start: date
    gaps: Mapping[int, int]


@dataclass(frozen=True)
class Forecast:
    """The curve with the figures of UC step 3; minimum and breach refer to ``available_cents``."""

    scope: Scope
    account_id: int | None
    area: Area | None
    horizon_months: int
    start: date
    end: date
    days: list[ForecastDay]
    minimum_date: date
    minimum_cents: int
    reserve_cents: int
    reserve_missing: bool
    first_breach_date: date | None
    shortfall_cents: int
    planned_income_cents: int
    planned_expense_cents: int
    overdue_count: int
    levy_applies: bool
    levy_gap_cents: int


@dataclass(frozen=True)
class ForecastItem:
    """A row acting on one day of the curve (UC step 4); ``date`` is the booking or due date."""

    transaction_id: int | None
    kind: ItemKind
    effective_date: date
    date: date
    account_id: int
    amount_cents: int
    text: str
    transfer_group_id: str | None
    overdue: bool


@dataclass(frozen=True)
class EmergencySuggestion:
    """Pots in emergency order that would cover the shortfall (UC 3b), per account of the view."""

    shortfall_cents: int
    proposals: list[pots.EmergencyProposal]
    covered_cents: int
    uncovered_cents: int


# --- pure rules --------------------------------------------------------------------------------


def horizon_end(today: date, horizon_months: int) -> date:
    """Last day of the forecast: the window is ``[today, today + horizon_months)``, so its last
    day is the day before the same day ``horizon_months`` later (clamped to the month's end).

    The window is the one the recurring engine generates (ARCHITECTURE.md "Time windows").
    """
    if horizon_months not in HORIZONS:
        raise ForecastError(N_("Choose a horizon of 3, 6 or 12 months."))
    return add_months(today, horizon_months) - timedelta(days=1)


def gap_on(day: date, gaps: Mapping[int, int], paid: Mapping[int, date]) -> int:
    """Σ of the year gaps on ``day``, leaving out every year whose back-payment is paid by then."""
    return sum(gap for year, gap in gaps.items() if not (year in paid and paid[year] <= day))


def curve(
    start: date,
    end: date,
    start_cents: int,
    deltas: Mapping[date, int],
    steps: Sequence[GapStep] = (),
    paid: Mapping[int, date] = {},
) -> list[ForecastDay]:
    """The day-by-day curve from ``start`` to ``end``: running sum of ``deltas`` and the line after levies.

    ``deltas[start]`` already acts on the first day (overdue items count from today). ``steps``
    are sorted by ``start``; without steps the available line equals the balance.
    """
    days: list[ForecastDay] = []
    balance = start_cents
    step = -1
    day = start
    while day <= end:
        balance += deltas.get(day, 0)
        while step + 1 < len(steps) and steps[step + 1].start <= day:
            step += 1
        gap = gap_on(day, steps[step].gaps, paid) if step >= 0 else 0
        days.append(ForecastDay(day, balance, balance - gap))
        day += timedelta(days=1)
    return days


def minimum(days: Sequence[ForecastDay]) -> ForecastDay:
    """The day with the lowest available amount; the earliest one on a tie."""
    return min(days, key=lambda point: (point.available_cents, point.date))


def first_breach(days: Iterable[ForecastDay], reserve_cents: int) -> date | None:
    """The first day whose available amount is below the reserve; none without a reserve (UC 3a)."""
    if reserve_cents <= 0:
        return None
    return next((point.date for point in days if point.available_cents < reserve_cents), None)


# --- reading -----------------------------------------------------------------------------------


def _view_accounts(conn: sqlite3.Connection, view: View) -> list[accounts.Account]:
    """The active payment accounts of the view; liability and archived accounts are refused as a view."""
    if view.account_id is not None:
        account = accounts.get_account(conn, view.account_id)
        if account.type == "liability":
            raise Conflict(N_("A liability account is not part of the liquidity forecast."))
        if account.archived_at is not None:
            raise Conflict(N_("The account %(name)s is archived."), name=account.name)
        return [account]
    return [
        account
        for account in accounts.list_accounts(conn)
        if account.type != "liability" and view.area in (None, account.area)
    ]


def _placeholders(ids: Sequence[int]) -> str:
    return ",".join("?" * len(ids))


# Rows still to come: planned rows (overdue ones on today) and booked rows dated after today.
_COMING = (
    ' FROM "transaction" t WHERE t.account_id IN ({ids}) AND t.date <= ?'
    " AND (t.status = 'planned' OR (t.status = 'booked' AND t.date > ?))"
)


def _daily_sums(conn: sqlite3.Connection, ids: Sequence[int], today: date, end: date) -> dict[date, int]:
    """Signed cents per effective day (one aggregate query): the deltas of ``curve``."""
    if not ids:
        return {}
    sql = (
        "SELECT max(t.date, ?) AS day, sum(t.amount_cents)"
        + _COMING.format(ids=_placeholders(ids))  # nosec B608 — only "?" placeholders go in
        + " GROUP BY day"
    )
    rows = conn.execute(sql, (today.isoformat(), *ids, end.isoformat(), today.isoformat()))
    return {date.fromisoformat(day): int(total) for day, total in rows}


def _planned_totals(conn: sqlite3.Connection, ids: Sequence[int], today: date, end: date) -> tuple[int, int]:
    """(income, expense) of the rows still to come in the horizon; transfer pairs inside the view are neither."""
    if not ids:
        return 0, 0
    marks = _placeholders(ids)
    sql = (
        "SELECT coalesce(sum(CASE WHEN t.amount_cents > 0 THEN t.amount_cents END), 0),"  # noqa: S608
        " coalesce(sum(CASE WHEN t.amount_cents < 0 THEN t.amount_cents END), 0)"
        + _COMING.format(ids=marks)
        + ' AND NOT EXISTS (SELECT 1 FROM "transaction" p WHERE t.transfer_group_id IS NOT NULL'  # noqa: S608
        f" AND p.transfer_group_id = t.transfer_group_id AND p.id != t.id AND p.account_id IN ({marks}))"  # nosec B608 — only "?" placeholders go in
    )
    income, expense = conn.execute(sql, (*ids, end.isoformat(), today.isoformat(), *ids)).fetchone()
    return int(income), int(expense)


def _overdue_count(conn: sqlite3.Connection, ids: Sequence[int], today: date) -> int:
    """Planned items dated before today (UC 2b); a transfer pair counts once."""
    if not ids:
        return 0
    sql = (
        'SELECT count(DISTINCT coalesce(t.transfer_group_id, t.id)) FROM "transaction" t'  # noqa: S608
        f" WHERE t.account_id IN ({_placeholders(ids)}) AND t.status = 'planned' AND t.date < ?"  # nosec B608 — only "?" placeholders go in
    )
    return int(conn.execute(sql, (*ids, today.isoformat())).fetchone()[0])


def _reserve(conn: sqlite3.Connection, view: View, in_view: Sequence[accounts.Account]) -> int:
    """Account floor, Σ floors of the area, or the total reserve (UC TBD-5, SPEC AK12)."""
    if view.scope == "total":
        return accounts.total_reserve_floor(conn)
    return sum(account.reserve_floor_cents for account in in_view)


def _levy_account(conn: sqlite3.Connection, settings: levy.LevySettings) -> int | None:
    """The account the levy is paid from: the one the back-payment sync plans its item on."""
    pot_id, _ = levy.levy_pot(conn, settings)
    # Consumed, not reimplemented: the same resolution the back-payment sync uses (-019).
    return levy._backpayment_account(conn, settings, pot_id)


def _levy_applies(conn: sqlite3.Connection, view: View, in_view: Sequence[accounts.Account]) -> bool:
    """The total view always carries the levy; an area or account view only if it holds the levy's account."""
    settings = levy.get_settings(conn)
    if settings.settings_missing:
        return False
    if view.scope == "total":
        return True
    account_id = _levy_account(conn, settings)
    return account_id is not None and any(account.id == account_id for account in in_view)


_BACKPAYMENTS_SQL = (
    'SELECT b.year, t.date FROM levy_backpayment b JOIN "transaction" t ON t.id = b.transaction_id'
    " WHERE t.status = 'planned'"
)


# Anything booked or allocated after today would move the gap within a year (no feature writes it).
_FUTURE_BOOKED_SQL = (
    "SELECT EXISTS (SELECT 1 FROM \"transaction\" WHERE status = 'booked' AND date > ?)"
    " OR EXISTS (SELECT 1 FROM pot_allocation WHERE date > ?)"
)


def _levy_steps(conn: sqlite3.Connection, today: date, end: date) -> tuple[list[GapStep], dict[int, date]]:
    """Year gaps on today, every new year, every back-payment due date and (if needed) every month.

    The gap is a function of booked rows, pot allocations and the set of open years (core rule:
    it rises only with a *booked* income). Up to today everything is known, so within a year it
    can only change on a due date (the previous year closes) — unless rows are booked or allocated
    after today, then also on every first of a month (the report counts whole months). Evaluating
    it per day, or per month without need, costs up to 24 monthly reports each time (AK5).

    Back-payment decision (UC step 3 + core rule): the planned item "Levy back-payment <year>" is
    a real outflow and counts in the curve on its (effective) date. From that date on the year's
    gap is no longer subtracted — the item has paid it. Before, the line is curve - gap. So the
    item is never subtracted on top of its gap, the rule ``levy.free_amount`` applies by leaving
    the items out of its planned rows.
    """
    settings = levy.get_settings(conn)
    points = {today}
    monthly = bool(conn.execute(_FUTURE_BOOKED_SQL, (today.isoformat(), today.isoformat())).fetchone()[0])
    first = add_months(today.replace(day=1), 1)
    while first <= end:
        if monthly or first.month == 1:
            points.add(first)
        first = add_months(first, 1)
    for year in range(today.year - 1, end.year + 1):
        due = levy.due_date(settings.due_month_day, year)
        if today < due <= end:  # ``end`` is the window's last day: a due date on the excluded day is out
            points.add(due)
    steps = [
        GapStep(point, {year.year: year.gap_cents for year in levy.levy_status(conn, today=point).years})
        for point in sorted(points)
    ]
    paid = {int(year): max(date.fromisoformat(day), today) for year, day in conn.execute(_BACKPAYMENTS_SQL)}
    return steps, paid


def _check_items(conn: sqlite3.Connection, items: Sequence[WhatIfItem], today: date) -> None:
    """A what-if item needs an amount other than 0, a day from today on and an existing account."""
    for item in items:
        if item.amount_cents == 0:
            raise ForecastError(N_("Enter an amount other than 0."))
        if item.date < today:
            raise ForecastError(N_("The date must be today or later."))
        accounts.get_account(conn, item.account_id)


def forecast(
    conn: sqlite3.Connection,
    view: View | None = None,
    horizon_months: int = DEFAULT_HORIZON,
    *,
    what_if: Sequence[WhatIfItem] = (),
    today: date | None = None,
) -> Forecast:
    """The curve of the view over the horizon with minimum, reserve check and horizon totals (UC steps 2-3).

    Pure read. ``what_if`` items (UC 1a) join the curve and the totals in memory; items on
    accounts outside the view or after the horizon leave it unchanged.
    """
    today = today or date.today()
    view = view or View()
    end = horizon_end(today, horizon_months)
    _check_items(conn, what_if, today)
    in_view = _view_accounts(conn, view)
    ids = [account.id for account in in_view]

    deltas: defaultdict[date, int] = defaultdict(int, _daily_sums(conn, ids, today, end))
    for account in in_view:
        if today < account.opening_date <= end:  # balance_at is 0 before the opening date
            deltas[account.opening_date] += account.opening_balance_cents
    income, expense = _planned_totals(conn, ids, today, end)
    for item in what_if:
        if item.account_id in ids and item.date <= end:
            deltas[item.date] += item.amount_cents
            if item.amount_cents > 0:
                income += item.amount_cents
            else:
                expense += item.amount_cents

    applies = _levy_applies(conn, view, in_view)
    steps, paid = _levy_steps(conn, today, end) if applies else ([], {})
    start_cents = sum(accounts.balance_at(conn, account.id, today) for account in in_view)
    days = curve(today, end, start_cents, deltas, steps, paid)
    low = minimum(days)
    reserve = _reserve(conn, view, in_view)
    breach = first_breach(days, reserve)
    return Forecast(
        scope=view.scope,
        account_id=view.account_id,
        area=view.area if view.scope == "area" else None,
        horizon_months=horizon_months,
        start=today,
        end=end,
        days=days,
        minimum_date=low.date,
        minimum_cents=low.available_cents,
        reserve_cents=reserve,
        reserve_missing=reserve <= 0,
        first_breach_date=breach,
        shortfall_cents=reserve - low.available_cents if breach is not None else 0,
        planned_income_cents=income,
        planned_expense_cents=expense,
        overdue_count=_overdue_count(conn, ids, today),
        levy_applies=applies,
        levy_gap_cents=days[0].balance_cents - days[0].available_cents,
    )


def what_if(
    conn: sqlite3.Connection,
    items: Sequence[WhatIfItem],
    view: View | None = None,
    horizon_months: int = DEFAULT_HORIZON,
    *,
    today: date | None = None,
) -> Forecast:
    """The forecast with hypothetical items (UC 1a); nothing is stored — ``recurring.create_expected`` does that."""
    return forecast(conn, view, horizon_months, what_if=items, today=today)


def items_at(
    conn: sqlite3.Connection,
    day: date,
    view: View | None = None,
    *,
    what_if: Sequence[WhatIfItem] = (),
    today: date | None = None,
) -> list[ForecastItem]:
    """The rows acting on ``day`` in the view (UC step 4): Σ amounts = curve(day) - curve(day - 1).

    On today that includes the overdue planned rows (UC 2b) and an item planned for today.
    """
    today = today or date.today()
    view = view or View()
    if day < today:
        raise ForecastError(N_("The date must be today or later."))
    _check_items(conn, what_if, today)
    ids = [account.id for account in _view_accounts(conn, view)]
    result: list[ForecastItem] = []
    if ids:
        sql = (
            "SELECT t.id, t.status, t.date, t.account_id, t.amount_cents, t.text, t.transfer_group_id"
            + _COMING.format(ids=_placeholders(ids))  # nosec B608 — only "?" placeholders go in
            + " AND max(t.date, ?) = ? ORDER BY t.date, t.id"
        )
        params = (*ids, day.isoformat(), today.isoformat(), today.isoformat(), day.isoformat())
        for row in conn.execute(sql, params):
            booked_on = date.fromisoformat(row["date"])
            result.append(
                ForecastItem(
                    transaction_id=row["id"],
                    kind=row["status"],
                    effective_date=day,
                    date=booked_on,
                    account_id=row["account_id"],
                    amount_cents=row["amount_cents"],
                    text=row["text"],
                    transfer_group_id=row["transfer_group_id"],
                    overdue=booked_on < today,
                )
            )
    result.extend(
        ForecastItem(None, "what_if", day, item.date, item.account_id, item.amount_cents, item.text, None, False)
        for item in what_if
        if item.date == day and item.account_id in ids
    )
    return result


def emergency_suggestion(
    conn: sqlite3.Connection, result: Forecast, *, today: date | None = None
) -> EmergencySuggestion | None:
    """Pots that would cover the shortfall of ``result`` in emergency order (UC 3b); none without a breach.

    The shortfall goes to ``pots.emergency_proposal`` account by account (the view's accounts in
    list order) until it is covered; locked pots are never proposed. Taking money out of a pot
    moves it to the free balance and moves the pot's target date (and its planned purchase) — it
    does not raise the account balance.
    """
    today = today or date.today()
    if result.shortfall_cents <= 0:
        return None
    view = View(result.account_id, result.area)
    remaining = result.shortfall_cents
    proposals: list[pots.EmergencyProposal] = []
    for account in _view_accounts(conn, view):
        if remaining <= 0:
            break
        proposal = pots.emergency_proposal(conn, account.id, remaining, today=today)
        if proposal.covered_cents > 0:
            proposals.append(proposal)
            remaining -= proposal.covered_cents
    return EmergencySuggestion(result.shortfall_cents, proposals, result.shortfall_cents - remaining, remaining)
