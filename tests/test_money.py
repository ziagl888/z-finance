"""AK6: money is integer cents; conversion is exact or refused."""

from __future__ import annotations

from decimal import Decimal

import pytest

from zfinance.money import from_cents, to_cents


@pytest.mark.parametrize(
    ("value", "cents"),
    [("12.30", 1230), ("-0.01", -1), ("1000", 100000), (Decimal("0.10"), 10), (7, 700), ("0.1", 10)],
)
def test_to_cents_is_exact(value: str | Decimal | int, cents: int) -> None:
    """Oracle: spec-anchored — SPEC "Beträge intern als Integer-Cent", hand-computed values."""
    assert to_cents(value) == cents


@pytest.mark.parametrize("value", ["1.234", "abc", "NaN", "Infinity", 1.5, True])
def test_to_cents_refuses_inexact_or_non_numeric(value: object) -> None:
    """Oracle: spec-anchored — money.py contract: floats, >2 decimals and non-numbers raise."""
    with pytest.raises(ValueError):
        to_cents(value)  # type: ignore[arg-type]


def test_ten_thousand_cent_values_sum_without_drift() -> None:
    """Oracle: spec-anchored — UC NF3 "keine Rundungsdrift bei 10.000 Buchungen" (10,000 x 0.10 = 1,000.00)."""
    assert from_cents(sum(to_cents("0.10") for _ in range(10_000))) == Decimal("1000.00")
