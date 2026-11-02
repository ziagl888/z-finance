"""The pure rules of an allocation proposal: validation of a rule's targets and the lines it makes.

No database and no other feature state: a target list plus an income amount and the levy share give
the lines of a proposal. The levy share itself comes from the levy engine (-019,
``levy.reserve_target``); it is never recomputed here (core rule).

Invariants:

* The levy line is the first line and carries its own amount as ``floor_cents``: it cannot be
  lowered (UC-ZF-014 AK7), while every other line may be edited down to 0.
* Σ of all line amounts never exceeds the income; what is left stays free (AK1: 1,800 € of
  4,000 €). Lines are paid in the order of the rule, and a line the income no longer carries is
  reported ``capped`` instead of being scaled by an invented factor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal

from zfinance.domain.levy.service import half_up
from zfinance.i18n import N_

#: Where a share goes. ``levy`` exists only as a derived line, never as a stored target.
LineKind = Literal["levy", "debt_plan", "pot", "reserve"]
TargetKind = Literal["debt_plan", "pot", "reserve"]
TARGET_KINDS: tuple[TargetKind, ...] = ("debt_plan", "pot", "reserve")
FallbackKind = Literal["pot", "reserve"]
FALLBACK_KINDS: tuple[FallbackKind, ...] = ("pot", "reserve")

#: 100 % in basis points; the percentages of a rule may not add up to more.
FULL_BP = 10_000
#: Most targets a rule may carry (a rule the user can still read in one screen).
MAX_TARGETS = 10
#: Days after which an open proposal is highlighted (UC AK5 / 4b).
OVERDUE_DAYS = 7


class InvalidRule(ValueError):
    """A rule's targets do not hold; ``msgid`` is an ``N_()`` catalog id for the API layer."""

    def __init__(self, msgid: str, **params: object) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


@dataclass(frozen=True)
class Target:
    """One stored share of a rule: either a percentage of the income or a fixed amount.

    ``pot_id`` belongs to ``kind="pot"``; ``debt_plan`` pays the first open debt of the payoff
    order and ``reserve`` leaves the money on the account (it books nothing).
    """

    kind: TargetKind
    pot_id: int | None = None
    percent_bp: int | None = None
    fixed_cents: int | None = None

    def wanted(self, income_cents: int) -> int:
        """What this target asks of ``income_cents`` (percentage half up, or the fixed amount)."""
        if self.percent_bp is not None:
            return half_up(Fraction(self.percent_bp * max(income_cents, 0), FULL_BP))
        return self.fixed_cents or 0


@dataclass(frozen=True)
class Line:
    """One line of a proposal. ``floor_cents`` > 0 only for the levy line (AK7)."""

    kind: LineKind
    pot_id: int | None = None
    amount_cents: int = 0
    floor_cents: int = 0
    capped: bool = False


def check_targets(targets: list[Target]) -> None:
    """Refuse a rule whose targets cannot produce a proposal (UC precondition, Σ shares ≤ 100 %).

    A target is a percentage **or** a fixed amount, a percentage lies within 0..100 % and all of
    them add up to at most 100 %, a fixed amount is greater than 0, and a pot target names its pot.
    """
    if not targets:
        raise InvalidRule(N_("Add at least one target the income should go to."))
    if len(targets) > MAX_TARGETS:
        raise InvalidRule(N_("A rule can have at most %(count)s targets."), count=MAX_TARGETS)
    for target in targets:
        if (target.percent_bp is None) == (target.fixed_cents is None):
            raise InvalidRule(N_("Enter either a percentage or a fixed amount for every target."))
        if target.percent_bp is not None and not 0 <= target.percent_bp <= FULL_BP:
            raise InvalidRule(N_("A percentage must be between 0 and 100 %."))
        if target.fixed_cents is not None and target.fixed_cents <= 0:
            raise InvalidRule(N_("A fixed amount must be greater than 0."))
        if target.kind == "pot" and target.pot_id is None:
            raise InvalidRule(N_("Choose the pot the share goes into."))
    percent = sum(target.percent_bp or 0 for target in targets)
    if percent > FULL_BP:
        raise InvalidRule(N_("The shares add up to more than 100 % of the income."))


def proposal_lines(income_cents: int, levy_cents: int, levy_pot_id: int | None, targets: list[Target]) -> list[Line]:
    """The lines of a proposal for ``income_cents``, levy line first (AK1).

    ``levy_cents`` is what the levy engine reserves for this income (0 = no levy line: a private
    income, no rate, or no levy pot). Every following target gets what it asks for as long as the
    income carries it; the rest of a line that no longer fits is dropped and the line is ``capped``.
    The levy line is never capped: it is paid first, so a short income eats into the other lines.
    """
    lines: list[Line] = []
    left = income_cents
    if levy_cents > 0 and levy_pot_id is not None:
        amount = min(levy_cents, left)
        lines.append(Line("levy", levy_pot_id, amount, amount))
        left -= amount
    for target in targets:
        wanted = max(target.wanted(income_cents), 0)
        amount = min(wanted, left)
        left -= amount
        lines.append(Line(target.kind, target.pot_id, amount, 0, amount < wanted))
    return lines


def apply_edits(lines: list[Line], amounts: dict[int, int], income_cents: int) -> list[Line]:
    """The lines with the user's amounts (line index → cents) for a confirmation (UC step 4).

    Refuses a negative amount, a levy line below its floor (AK7) and a total above the income.
    Editing clears ``capped``: the amount is now the user's, not a reduced proposal.
    """
    edited: list[Line] = []
    for index, line in enumerate(lines):
        amount = amounts.get(index, line.amount_cents)
        if amount < 0:
            raise InvalidRule(N_("An amount cannot be negative."))
        if line.kind == "levy" and amount < line.floor_cents:
            raise InvalidRule(N_("The levy share is already reserved and cannot be lowered."))
        edited.append(replace(line, amount_cents=amount, capped=line.capped and amount == line.amount_cents))
    if sum(line.amount_cents for line in edited) > income_cents:
        raise InvalidRule(N_("The shares add up to more than the income."))
    return edited


def remaining_cents(income_cents: int, lines: list[Line]) -> int:
    """What stays free of the income after all lines (AK1: 1,800 € of 4,000 €)."""
    return income_cents - sum(line.amount_cents for line in lines)


def bookable_cents(lines: list[Line]) -> int:
    """Σ of the lines that leave the free amount when confirmed — the levy line does not.

    The levy share is already out of ``free_amount`` through ``levy_gap`` (core rule, AK6): a
    ``reserve`` line books nothing at all. So only the debt and pot lines are measured against the
    free amount for the reserve warning of UC 3a.
    """
    return sum(line.amount_cents for line in lines if line.kind in {"debt_plan", "pot"})
