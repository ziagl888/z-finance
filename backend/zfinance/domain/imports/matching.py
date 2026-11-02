"""Classifying parsed lines against the ledger: duplicate, match on a planned item, duplicate suspect, new.

Pure functions over plain values; the service loads the ledger rows (``LedgerRow``) and writes.

Order per line (UC-ZF-005 Schnittstellen, 3b, 3c):

1. ``duplicate`` — its duplicate key already exists (an earlier import). Never written.
2. ``match`` — a planned row on the account with the same sign, the amount within ``amount_pct``
   percent of the planned amount and the date within ``date_days`` days. Several candidates: the
   nearest date, then the nearest amount, then the lowest id wins and the line is flagged for
   review with all candidates (3b).
3. ``duplicate_suspect`` — a booked row on the account that did not come from an import (hand
   entry, a confirmed planned item, the leg a matched transfer booked, a receipt) with exactly the
   same amount, dated within ``SUSPECT_DAYS`` days (3c). Not written unless the user says so.
4. ``new``.

Invariants: every planned row and every suspect row is claimed by at most one line, in file order,
so a statement with two equal payments never collapses them onto one ledger row.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from zfinance.domain.imports.parser import ParsedLine

Kind = Literal["new", "duplicate", "match", "duplicate_suspect"]

#: Days a hand entry may lie away from the bank line and still count as the same payment (UC 3c).
SUSPECT_DAYS = 3
#: Defaults of the match tolerance (UC assumption; settings ``imports.match_*``).
DEFAULT_AMOUNT_PCT = 5
DEFAULT_DATE_DAYS = 5


@dataclass(frozen=True)
class Tolerance:
    """How far a bank line may differ from a planned item and still replace it."""

    amount_pct: int = DEFAULT_AMOUNT_PCT
    date_days: int = DEFAULT_DATE_DAYS


@dataclass(frozen=True)
class LedgerRow:
    """The part of an existing booking the classification needs.

    ``imported`` = the row carries an ``import_hash``. Only ``planned`` rows are match targets, only
    ``booked`` rows that were not imported are suspect targets.
    """

    id: int
    date: date
    amount_cents: int
    status: str
    imported: bool = False


@dataclass(frozen=True)
class Classified:
    """One line with its key, its kind and the ledger rows it refers to."""

    line: ParsedLine
    key: str
    kind: Kind
    planned_id: int | None = None
    candidates: tuple[int, ...] = ()
    review: bool = False
    suspect_id: int | None = None


def amount_fits(actual: int, planned: int, amount_pct: int) -> bool:
    """Same sign and ``|actual - planned| <= amount_pct % of |planned|``, in integer arithmetic."""
    return (actual > 0) == (planned > 0) and abs(actual - planned) * 100 <= amount_pct * abs(planned)


def match_planned(
    line: ParsedLine, planned: Iterable[LedgerRow], tolerance: Tolerance
) -> tuple[LedgerRow | None, list[LedgerRow]]:
    """The planned row a line replaces and all fitting candidates, best first."""
    fitting = [
        row
        for row in planned
        if row.status == "planned"
        and abs((row.date - line.date).days) <= tolerance.date_days
        and amount_fits(line.amount_cents, row.amount_cents, tolerance.amount_pct)
    ]
    fitting.sort(key=lambda row: (abs((row.date - line.date).days), abs(row.amount_cents - line.amount_cents), row.id))
    return (fitting[0] if fitting else None), fitting


def match_manual(line: ParsedLine, booked: Iterable[LedgerRow], days: int = SUSPECT_DAYS) -> LedgerRow | None:
    """A booked, not imported row with exactly the line's amount within ``days`` days: nearest date, then lowest id."""
    fitting = [
        row
        for row in booked
        if row.status == "booked"
        and not row.imported
        and row.amount_cents == line.amount_cents
        and abs((row.date - line.date).days) <= days
    ]
    return min(fitting, key=lambda row: (abs((row.date - line.date).days), row.id), default=None)


def classify(
    lines: list[ParsedLine],
    keys: list[str],
    existing_keys: set[str],
    ledger: list[LedgerRow],
    tolerance: Tolerance,
) -> list[Classified]:
    """Classify the lines of one file in file order (see the module docstring)."""
    free = {row.id: row for row in ledger}
    result: list[Classified] = []
    for line, key in zip(lines, keys, strict=True):
        if key in existing_keys:
            result.append(Classified(line, key, "duplicate"))
            continue
        best, candidates = match_planned(line, free.values(), tolerance)
        if best is not None:
            del free[best.id]
            ids = tuple(row.id for row in candidates)
            result.append(Classified(line, key, "match", best.id, ids, review=len(ids) > 1))
            continue
        suspect = match_manual(line, free.values())
        if suspect is not None:
            del free[suspect.id]
            result.append(Classified(line, key, "duplicate_suspect", suspect_id=suspect.id))
            continue
        result.append(Classified(line, key, "new"))
    return result
