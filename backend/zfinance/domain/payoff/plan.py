"""Pure rules of the payoff plan (UC-ZF-012): the order of the debts, the month-by-month plan and
the progress against the saved plan. No database; the service feeds plain values in.

Months are counted as *plan months*: 0 is the first month of the plan (the month after today).

* **Order** (``order_debts``): snowball = smallest remaining amount first, avalanche = highest
  interest rate first, manual = the user's order (open debts missing from it follow snowball).
* **Plan** (``simulate``): every month each open debt gets its instalment when one is due; the
  extra budget goes to the first open debt of the order, and what it does not need to the next.
  An instalment of a debt that is paid off keeps going into the plan on its old due dates, and a
  capped last instalment passes its rest on in the same month, so the money per month stays the
  same (UC AK1: after A, 600 € go to B).
* **Progress** (``progress``): planned against booked per month; the catch-up amount is what the
  completed months are short of.

Invariants:

* Per plan month, Σ payments = extra budget + every instalment due that month (paid or freed),
  unless all debts are paid off within the month (then less).
* No debt is paid more than its remaining amount; a finished plan ends every debt at 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

Strategy = Literal["snowball", "avalanche", "manual"]
STRATEGIES: tuple[Strategy, ...] = ("snowball", "avalanche", "manual")

#: The plan gives up after 50 years (a debt without instalment and no extra budget never ends).
MAX_MONTHS = 600


def month_index(day: date) -> int:
    """A calendar month as one integer (months since year 0), for month arithmetic."""
    return day.year * 12 + day.month - 1


def month_key(index: int) -> str:
    """``YYYY-MM`` of a ``month_index``."""
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def key_index(key: str) -> int:
    """The ``month_index`` of a ``YYYY-MM`` key."""
    return int(key[:4]) * 12 + int(key[5:7]) - 1


@dataclass(frozen=True)
class PlanDebt:
    """One open debt as the plan sees it.

    ``first_due`` is the plan month of the next instalment (≥ 0), then every ``step_months``;
    ``installment_cents`` 0 = no fixed instalment (the debt gets extra money only).
    """

    id: int
    remaining_cents: int
    installment_cents: int = 0
    step_months: int = 1
    first_due: int = 0
    interest_rate: Decimal = Decimal(0)

    def due(self, month: int) -> bool:
        """Whether an instalment of this debt is due in plan month ``month``."""
        return (
            self.installment_cents > 0 and month >= self.first_due and (month - self.first_due) % self.step_months == 0
        )


@dataclass(frozen=True)
class Payment:
    """What one debt gets in one month: its instalment and the extra (special payment) part."""

    debt_id: int
    installment_cents: int
    extra_cents: int


@dataclass(frozen=True)
class PlanMonth:
    """One plan month: the payments in the order of the plan and the total remaining after it."""

    index: int
    payments: tuple[Payment, ...]
    remaining_cents: int

    @property
    def installments_cents(self) -> int:
        """Instalments paid this month."""
        return sum(p.installment_cents for p in self.payments)

    @property
    def extra_cents(self) -> int:
        """Special payments this month."""
        return sum(p.extra_cents for p in self.payments)


@dataclass(frozen=True)
class Plan:
    """The computed plan. ``ends`` maps a debt to the plan month of its last payment.

    ``complete`` is false when some debt is not paid off within ``MAX_MONTHS``.
    """

    order: tuple[int, ...]
    months: tuple[PlanMonth, ...]
    ends: dict[int, int] = field(default_factory=dict)
    complete: bool = True

    @property
    def end(self) -> int | None:
        """Plan month in which the last debt is paid off; None if never (or no debt at all)."""
        return max(self.ends.values()) if self.complete and self.ends else None


def order_debts(debts: Sequence[PlanDebt], strategy: Strategy, manual_order: Sequence[int] = ()) -> tuple[int, ...]:
    """The ids of ``debts`` in payoff order (ties: smaller remaining amount, then lower id)."""
    snowball = sorted(debts, key=lambda d: (d.remaining_cents, d.id))
    if strategy == "avalanche":
        return tuple(d.id for d in sorted(snowball, key=lambda d: -d.interest_rate))
    if strategy == "manual":
        known = {d.id for d in debts}
        first = [debt_id for debt_id in dict.fromkeys(manual_order) if debt_id in known]
        return (*first, *(d.id for d in snowball if d.id not in first))
    return tuple(d.id for d in snowball)


def simulate(debts: Sequence[PlanDebt], extra_cents: int, order: Sequence[int]) -> Plan:
    """Pay the debts month by month (see the module docstring) until all are at 0.

    ``order`` must list every debt of ``debts``; ``extra_cents`` ≥ 0 is the budget per month on
    top of the instalments.
    """
    remaining = {d.id: d.remaining_cents for d in debts}
    ends: dict[int, int] = {}
    months: list[PlanMonth] = []
    for index in range(MAX_MONTHS):
        if not any(remaining.values()):
            break
        pool = extra_cents
        installments: dict[int, int] = {}
        for debt in debts:
            if not debt.due(index):
                continue
            paid = min(debt.installment_cents, remaining[debt.id])
            if paid:
                installments[debt.id] = paid
                remaining[debt.id] -= paid
            # A capped or freed instalment goes on to the next debt of the order.
            pool += debt.installment_cents - paid
        extras: dict[int, int] = {}
        for debt_id in order:
            if pool == 0:
                break
            paid = min(pool, remaining[debt_id])
            if paid:
                extras[debt_id] = paid
                remaining[debt_id] -= paid
                pool -= paid
        payments = tuple(
            Payment(debt_id, installments.get(debt_id, 0), extras.get(debt_id, 0))
            for debt_id in order
            if debt_id in installments or debt_id in extras
        )
        for payment in payments:
            if remaining[payment.debt_id] == 0:
                ends.setdefault(payment.debt_id, index)
        months.append(PlanMonth(index, payments, sum(remaining.values())))
    return Plan(tuple(order), tuple(months), ends, not any(remaining.values()))


def build_plan(
    debts: Sequence[PlanDebt], extra_cents: int, strategy: Strategy, manual_order: Sequence[int] = ()
) -> Plan:
    """Order the debts by ``strategy`` and simulate the plan."""
    return simulate(debts, extra_cents, order_debts(debts, strategy, manual_order))


@dataclass(frozen=True)
class MonthProgress:
    """Planned against booked repayment of one month; ``completed`` = the month is over."""

    month: str
    planned_cents: int
    actual_cents: int
    completed: bool

    @property
    def difference_cents(self) -> int:
        """Booked minus planned: negative = behind in this month."""
        return self.actual_cents - self.planned_cents


def progress(
    baseline: Mapping[str, int], actual: Mapping[str, int], current_month: str
) -> tuple[list[MonthProgress], int]:
    """Months of the saved plan up to ``current_month`` and the catch-up amount (UC 6a).

    Catch-up = Σ (planned - booked) over the completed months, at least 0: what the next month
    needs on top to hold the debt-free date. The running month is listed but not counted.
    """
    rows = [
        MonthProgress(month, planned, actual.get(month, 0), month < current_month)
        for month, planned in sorted(baseline.items())
        if month <= current_month
    ]
    short = sum(-row.difference_cents for row in rows if row.completed)
    return rows, max(short, 0)
