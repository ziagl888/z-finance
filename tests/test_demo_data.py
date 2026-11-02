"""The demo data set of the manual's screenshots (tools/demo_data.py; UC NF7, NF11; task -028).

The data set is seeded through the public API of a real app on a temporary data directory, once per
language, and every test reads the result back through the API.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import demo_data
from zfinance.app import create_app
from zfinance.config import Settings
from zfinance.domain.receipts.service import detect_photo_type

_TEXT_KEYS = {"name", "text", "counterparty", "memo", "creditor", "description", "merchant", "reason"}


def _free_text(node: object) -> list[str]:
    """Every value under a key that carries text a person typed (names, purposes), at any depth."""
    if isinstance(node, dict):
        found = [v for k, v in node.items() if k in _TEXT_KEYS and isinstance(v, str)]
        return found + [s for v in node.values() for s in _free_text(v)]
    if isinstance(node, list):
        return [s for v in node for s in _free_text(v)]
    return []


@dataclass
class Demo:
    """A seeded instance: its client, the ids ``seed`` returned, and the language and day it ran in."""

    client: TestClient
    ids: demo_data.DemoIds
    lang: str
    today: date


@pytest.fixture(scope="module", params=demo_data.DEMO_LANGUAGES)
def demo(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Demo]:
    """An empty app seeded with the demo story (module scope: the seeding takes a few seconds)."""
    tmp = tmp_path_factory.mktemp("demo")
    web = tmp / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>z-finance</title>", encoding="utf-8")
    settings = Settings(data_dir=tmp / "data", web_dir=web)
    today = date.today()
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        ids = demo_data.seed(client, lang=request.param, today=today, db_path=settings.db_path)
        yield Demo(client=client, ids=ids, lang=request.param, today=today)


def test_the_story_is_what_the_manual_describes(demo: Demo) -> None:
    """Oracle: spec-anchored — module docstring of tools/demo_data.py and the chapters `index`, `due`,
    `allocation`, `receipts`: two overdue items (one over 30 days), one open allocation proposal,
    two receipts to check, one booked receipt, three debts, four pots, a 30 % levy rate."""
    client = demo.client
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["overdue_count"] == 2
    long_overdue = [item for item in dashboard["due_items"] if item["long_overdue"]]
    assert len(long_overdue) == 1
    assert long_overdue[0]["days_overdue"] == 35

    proposals = client.get("/api/allocation").json()["proposals"]
    assert [p["status"] for p in proposals] == ["open"]
    assert proposals[0]["amount_cents"] == 400_000

    assert len(client.get("/api/receipts", params={"status": "review"}).json()) == 2
    assert len(client.get("/api/receipts", params={"status": "booked"}).json()) == 1

    assert len(demo.ids.debts) == 3
    assert set(demo.ids.pots) == {"holiday", "emergency", "retirement", "levy"}
    assert client.get("/api/levy/settings").json()["levy_rate_bp"] == 3000


def test_free_amount_is_net_of_the_levy_gap(demo: Demo) -> None:
    """Oracle: spec-anchored — SPEC-z-finance.md "Kernregel": free = sum of the payment accounts - planned
    items - reserve - levy_gap, and AK22: target = rate * business income of the year (half up),
    gap = target - (levy pot + prepayments). The business income is summed here from the booked rows."""
    client = demo.client
    free = client.get("/api/dashboard").json()["free"]
    # planned_cents is the (negative) sum of the planned rows, so it is added.
    assert (
        free["free_cents"]
        == free["accounts_cents"] + free["planned_cents"] - free["reserve_cents"] - free["levy_gap_cents"]
    )

    status = client.get("/api/levy/status").json()
    assert status["levy_gap_cents"] == free["levy_gap_cents"]
    booked = [t for t in client.get("/api/transactions", params={"limit": 500}).json() if t["status"] == "booked"]
    for year in status["years"]:
        income = sum(
            t["amount_cents"]
            for t in booked
            if t["account_id"] == demo.ids.business
            and t["amount_cents"] > 0
            and t["date"].startswith(str(year["year"]))
        )
        assert year["base_cents"] == income
        assert year["target_cents"] == (income * demo_data.LEVY_RATE_BP + 5000) // 10_000
        assert year["gap_cents"] == max(0, year["target_cents"] - year["actual_cents"])
    assert status["levy_gap_cents"] > 0


def test_the_statement_has_one_line_of_each_kind(demo: Demo) -> None:
    """Oracle: spec-anchored — SPEC AK5/AK6 (duplicate key, match a planned item) and the docstring of
    `demo_statement_csv`, classified by hand: two lines imported before, the coffee resembling a hand
    booking, the streaming line matching the overdue item, three new lines."""
    csv = demo_data.demo_statement_csv(demo.today, demo.lang)
    preview = demo.client.post(
        "/api/imports/preview",
        params={"profile_id": demo.ids.profile, "account_id": demo.ids.checking},
        content=csv.encode("utf-8"),
        headers={"Content-Type": "text/csv"},
    ).json()
    counts = preview["counts"]
    assert (counts["new"], counts["matched"], counts["duplicates"], counts["suspects"], counts["errors"]) == (
        3,
        1,
        2,
        1,
        0,
    )
    assert [line["kind"] for line in preview["lines"]][:2] == ["duplicate", "duplicate"]


def test_the_data_is_synthetic(demo: Demo) -> None:
    """Oracle: spec-anchored — UC NF7 "keine persönlichen Daten … im Repo": every account is one of
    the four demo accounts or a demo creditor, and nothing that looks like an e-mail address, an IBAN
    or a long number occurs in the rows the manual shows."""
    client = demo.client
    known = {demo_data.name(demo.lang, key) for key in ("checking", "savings", "wallet", "business")}
    known |= {demo_data.name(demo.lang, key) for key in ("car", "equipment", "furniture")}
    assert {account["name"] for account in client.get("/api/accounts").json()} <= known

    rows = [
        client.get("/api/accounts").json(),
        client.get("/api/transactions", params={"limit": 500}).json(),
        client.get("/api/recurring").json(),
        client.get("/api/debts").json(),
        client.get("/api/pots").json(),
        client.get("/api/receipts").json(),
    ]
    words = " | ".join(_free_text(rows))
    assert "@" not in words
    assert not re.search(r"[A-Z]{2}\d{2}[A-Z0-9]{10,}", words)  # IBAN-like
    assert not re.search(r"\d{6,}", words)  # account, card or phone number
    assert demo_data.EXAMPLE_MARKER == {"en": "EXAMPLE DATA", "de": "BEISPIELDATEN"}


def test_the_synthetic_receipt_is_a_png_the_api_accepts() -> None:
    """Oracle: spec-anchored — receipts API (ARCHITECTURE "Receipts"): the type comes from the magic
    bytes, a photo is stored once per hash — so two variants must differ and both must be PNG."""
    first, second = demo_data.receipt_png(1), demo_data.receipt_png(2)
    assert detect_photo_type(first) == ("image/png", "png")
    assert first != second
    assert len(first) < 10_000


def test_an_unknown_language_is_refused_before_anything_is_written() -> None:
    """Oracle: spec-anchored — the demo exists in the two UI languages (UC NF6); anything else is
    refused up front with no request made (the client here would fail on any call)."""
    with pytest.raises(ValueError, match="unsupported demo language"):
        demo_data.seed(object(), lang="fr", today=date(2026, 9, 20), db_path=Path("unused"))
