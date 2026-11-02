"""Amounts are bounded at MAX_CENTS: an oversized input is refused, never stored.

Without a bound a 10**20 cent booking answered 500, and two bookings above 2**62 made every sum
over the account fail for good. Values are synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from zfinance import money
from zfinance.domain.imports import parser

HUGE = 10**20
OVERFLOWING = 5 * 10**18  # two of these exceed the signed 64-bit range of SQLite's SUM
OVER_MAX = money.MAX_CENTS + 1


def _account(client: TestClient, **overrides: object) -> int:
    body: dict[str, Any] = {
        "name": "Test giro",
        "area": "private",
        "type": "giro",
        "opening_balance_cents": 0,
        "opening_date": "2026-09-01",
    }
    body.update(overrides)
    response = client.post("/api/accounts", json=body)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _booking(account: int, cents: int) -> dict[str, object]:
    return {"date": "2026-09-10", "amount_cents": cents, "account_id": account, "text": "Test"}


def test_max_cents_leaves_room_to_sum_every_row() -> None:
    """Oracle: spec-anchored — the bound must keep a sum over 900,000 rows inside SQLite's int64."""
    assert money.MAX_CENTS == 10**13
    assert money.MAX_CENTS * 900_000 < 2**63


@pytest.mark.parametrize("euros", ["100000000000.01", "-100000000000.01", "1" + "0" * 18, "1" + "0" * 30, 10**12])
def test_to_cents_refuses_amounts_over_the_bound(euros: str | int) -> None:
    """Oracle: spec-anchored — task trigger (c): to_cents raises ValueError past MAX_CENTS (10**11 euros)."""
    with pytest.raises(money.AmountTooLargeError):
        money.to_cents(euros)


def test_to_cents_accepts_the_bound_itself() -> None:
    """Oracle: spec-anchored — the limit is inclusive on both sides."""
    assert money.to_cents(str(money.MAX_CENTS // 100)) == money.MAX_CENTS
    assert money.to_cents(str(-money.MAX_CENTS // 100)) == -money.MAX_CENTS


def test_booking_over_the_bound_is_422_not_500(client: TestClient) -> None:
    """Oracle: spec-anchored — trigger (a): 10**20 cents answered 500 (SQLite cannot hold it)."""
    account = _account(client)
    for cents in (HUGE, OVER_MAX, -OVER_MAX, OVERFLOWING):
        assert client.post("/api/transactions", json=_booking(account, cents)).status_code == 422
    assert client.get("/api/transactions").json() == []


def test_booking_at_the_bound_is_stored(client: TestClient) -> None:
    """Oracle: spec-anchored — the bound itself is a valid amount."""
    account = _account(client)
    created = client.post("/api/transactions", json=_booking(account, money.MAX_CENTS))
    assert created.status_code == 201
    assert created.json()["amount_cents"] == money.MAX_CENTS


def test_two_huge_bookings_cannot_break_the_sum_queries(client: TestClient) -> None:
    """Oracle: spec-anchored — trigger (b): two bookings above 4.6e18 made every sum query fail for good."""
    account = _account(client)
    for _ in range(2):
        client.post("/api/transactions", json=_booking(account, OVERFLOWING))
    assert client.get("/api/accounts").status_code == 200
    assert client.get("/api/dashboard").status_code == 200


@pytest.mark.parametrize("field", ["opening_balance_cents", "reserve_floor_cents"])
def test_account_amounts_are_bounded(client: TestClient, field: str) -> None:
    """Oracle: spec-anchored — account amount fields share the bound."""
    body = {"name": "Test", "area": "private", "type": "giro", "opening_balance_cents": 0, "opening_date": "2026-09-01"}
    response = client.post("/api/accounts", json={**body, field: OVER_MAX})
    assert response.status_code == 422


def test_recurring_amount_is_bounded(client: TestClient) -> None:
    """Oracle: spec-anchored — a recurring item books every period, so it is bounded like a booking."""
    account = _account(client)
    body = {
        "name": "Test rent",
        "account_id": account,
        "amount_cents": HUGE,
        "interval": "monthly",
        "start_date": "2026-09-01",
    }
    assert client.post("/api/recurring", json=body).status_code == 422


def test_receipt_form_amount_over_the_bound_is_422(client: TestClient) -> None:
    """Oracle: spec-anchored — the receipt agent submits without a person at the form."""
    response = client.post(
        "/api/receipts",
        data={"amount_cents": str(HUGE), "merchant": "Test shop", "date": "2026-09-10"},
        files={"photo": ("r.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert response.status_code == 422


def test_receipt_run_id_out_of_range_is_422(client: TestClient) -> None:
    """Oracle: spec-anchored — SQLite INTEGER holds ids below 2**63; larger ones answered 500."""
    assert client.put(f"/api/receipts/runs/{2**63}", json={"counts": {}}).status_code == 422
    response = client.post(
        "/api/receipts",
        data={"run_id": str(2**63), "merchant": "Test shop", "date": "2026-09-10"},
        files={"photo": ("r.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert response.status_code == 422


def test_import_amount_over_the_bound_is_a_line_error() -> None:
    """Oracle: spec-anchored — trigger (c): 20 digits passed the preview and failed the commit with 500."""
    profile = parser.Profile(
        "Test Simple", parser.ColumnMap(date="Datum", text="Text", amount="Betrag"), delimiter="semicolon"
    )
    good = "".join(f"02.09.2026;Test ok {n};12,50\n" for n in range(20))
    data = f"Datum;Text;Betrag\n01.09.2026;Test big;100000000000000000000,00\n{good}".encode()
    result = parser.parse_rows(profile, data)
    assert [line.amount_cents for line in result.lines] == [1250] * 20
    assert [error.line for error in result.errors] == [2]
    assert "too large" in result.errors[0].msgid


def test_import_amount_at_the_bound_is_read() -> None:
    """Oracle: spec-anchored — the bound itself is accepted by the importer."""
    assert parser.parse_amount("100.000.000.000,00", ",") == money.MAX_CENTS
    with pytest.raises(ValueError):
        parser.parse_amount("100.000.000.000,01", ",")


def test_forecast_what_if_amount_is_bounded(client: TestClient) -> None:
    """Oracle: spec-anchored — a what-if item is an amount like a booking, so it shares the bound."""
    account = _account(client)
    for cents in (HUGE, OVER_MAX, -OVER_MAX):
        item = {"date": "2099-01-01", "amount_cents": cents, "account_id": account, "text": "Test"}
        response = client.post("/api/forecast/what-if", json={"account_id": account, "items": [item]})
        assert response.status_code == 422, cents
    at_bound = {"date": "2099-01-01", "amount_cents": -money.MAX_CENTS, "account_id": account, "text": "Test"}
    ok = client.post("/api/forecast/what-if", json={"account_id": account, "items": [at_bound]})
    assert ok.status_code == 200, ok.text


@pytest.mark.parametrize("field", ["income_override_cents", "budget_cents"])
def test_payoff_plan_amounts_are_bounded(client: TestClient, field: str) -> None:
    """Oracle: spec-anchored — the income override and the monthly budget are non-negative amounts."""
    base = {"strategy": "snowball", "budget_cents": 0}
    for cents in (HUGE, OVER_MAX):
        assert client.put("/api/payoff", json={**base, field: cents}).status_code == 422, cents
    assert client.put("/api/payoff", json={**base, field: money.MAX_CENTS}).status_code == 200
