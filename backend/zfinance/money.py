"""Money as integer cents.

Every amount in the database, the services and the JSON API is an ``int`` number of euro cents
(signed: income positive, expense negative). Floats never carry money. Conversion happens only
at the edges: parsing user/CSV input and formatting for display.

API field convention: amounts are named ``*_cents`` (``amount_cents``, ``opening_balance_cents``).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import Field, StrictInt

Cents = int

#: Largest amount in cents, either sign (100 billion euros). SQLite sums in int64 (9.2e18), so
#: this leaves room for a sum over 900,000 rows at the bound; past it ``SUM`` raises for good.
MAX_CENTS = 10**13

#: Largest id SQLite stores in an INTEGER column.
MAX_ID = 2**63 - 1

# API input types: strict ints inside the bound, so an oversized value is a 422 at the edge.
AmountCents = Annotated[StrictInt, Field(ge=-MAX_CENTS, le=MAX_CENTS)]
PositiveCents = Annotated[StrictInt, Field(gt=0, le=MAX_CENTS)]
NonNegativeCents = Annotated[StrictInt, Field(ge=0, le=MAX_CENTS)]

_CENT = Decimal("0.01")
_MAX_EUROS = Decimal(MAX_CENTS) / 100


class AmountTooLargeError(ValueError):
    """An amount is beyond ``MAX_CENTS`` in either direction."""


def to_cents(value: Decimal | str | int) -> Cents:
    """Convert a euro amount to cents exactly; reject anything that is not a whole number of cents.

    Accepts ``Decimal``, ``int`` (whole euros) or a plain decimal string with ``.`` as separator
    (``"-12.30"``). Locale formats (``"1.234,56"``) are the caller's job, e.g. the CSV importer
    that knows the profile's decimal separator. Floats are refused on purpose.

    Raises:
        AmountTooLargeError: more than ``MAX_CENTS`` in either direction.
        ValueError: not a finite number, a float, or more than two decimal places.
    """
    if isinstance(value, bool | float):
        raise ValueError(f"refusing {type(value).__name__} as money: {value!r}")
    try:
        amount = Decimal(value) if not isinstance(value, Decimal) else value
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal amount: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"not a finite amount: {value!r}")
    if abs(amount) > _MAX_EUROS:
        raise AmountTooLargeError(f"amount beyond {MAX_CENTS} cents: {value!r}")
    if amount != amount.quantize(_CENT):
        raise ValueError(f"more than two decimal places: {value!r}")
    return int(amount * 100)


def from_cents(cents: Cents) -> Decimal:
    """Convert cents back to a euro ``Decimal`` with exactly two decimal places."""
    return (Decimal(cents) / 100).quantize(_CENT)
