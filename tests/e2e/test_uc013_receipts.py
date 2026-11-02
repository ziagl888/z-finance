"""UC-ZF-013 — a receipt photo, booked by itself, in the browser.

Only half of this use case lives in the browser. Everything before ``POST /api/receipts`` — the
mailbox, the allow-list of senders, the reading of the photo by the headless agent, the answer per
run and the retry after an outage — belongs to the receipt agent (``agent/``) and an IMAP mailbox, and
no browser can drive it. AK4, AK5 and AK6 are therefore not covered here; they are recorded as not
observable in ``docs/qa/acceptance-1.0.md``, with the agent's own tests as their evidence.

What the browser does cover is the tool's side: a submitted receipt is booked exactly once and
carries its photo, a second submission of the same photo books nothing more, and a receipt the agent
was unsure about waits in the review list until it is corrected and booked by hand.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

import _scenario as scenario
import demo_data
from _ui import body, euro, row, shown


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, Any]:
    """Categories and the private account a card payment lands on."""
    categories = scenario.adopt_categories(app)
    return {
        "categories": categories,
        "checking": scenario.account(app, "Receipt checking", opening_cents=300_000),
    }


def _submit(app: Any, *, variant: int, **fields: Any) -> dict[str, Any]:
    """Hand a receipt to the tool the way the agent does: a photo plus what it read."""
    response = app.client.post(
        "/api/receipts",
        files={"photo": ("receipt.png", demo_data.receipt_png(variant), "image/png")},
        data={key: str(value) for key, value in fields.items()},
    )
    assert response.status_code < 300, response.text
    return response.json()


def test_ak1_a_submitted_receipt_is_booked_once_and_keeps_its_photo(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-013 AK1 "nach dem nächsten Lauf existiert genau eine Buchung mit
    diesem Betrag und Datum und einem Beleg-Link zum Foto" (the tool's side of the criterion; the
    channel and the agent run are outside the browser, see the module docstring)."""
    day = date.today() - timedelta(days=3)
    answer = _submit(
        app,
        variant=1,
        date=day.isoformat(),
        amount_cents=2_340,
        merchant="Sample Supermarket",
        payment_method="card",
        confidence=0.95,
    )
    assert answer["status"] == "booked"

    app.open(page, "/receipts")

    booked = row(page, "Sample Supermarket")
    assert euro(-2_340) in booked.inner_text()
    assert booked.get_by_role("link", name="Open").count() == 1, "the booking carries the photo"

    app.open(page, "/entry")
    assert page.locator("table.entry-table tbody tr", has_text="Sample Supermarket").count() == 1


def test_ak2_the_same_photo_twice_books_nothing_more(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-013 AK2 "Dasselbe Foto ein zweites Mal geschickt → keine zweite
    Buchung, Rückmeldung „schon erfasst"". The browser half is the one asserted here: nothing is booked
    a second time and no second receipt is stored. The answer itself (``status: duplicate`` naming the
    receipt it is a duplicate of) goes to the agent, which replies on the mail channel — not in the UI."""
    day = date.today() - timedelta(days=2)
    photo: dict[str, Any] = {
        "variant": 2,
        "date": day.isoformat(),
        "amount_cents": 4_560,
        "merchant": "Demo Fuel Station",
        "payment_method": "card",
        "confidence": 0.95,
    }
    first = _submit(app, **photo)
    assert first["status"] == "booked"
    again = _submit(app, **photo)  # the same photo a second time
    assert again["status"] == "duplicate", again
    assert again["receipt_id"] is None, "nothing is stored a second time"

    app.open(page, "/entry")
    assert page.locator("table.entry-table tbody tr", has_text="Demo Fuel Station").count() == 1, (
        "the second submission books nothing"
    )
    app.open(page, "/receipts")
    assert page.locator("table", has_text="Photo").first.inner_text().count("Demo Fuel Station") == 1
    assert shown(page, "Nothing to check"), "and nothing is left waiting"


def test_ak3_an_unreadable_receipt_waits_and_is_booked_after_the_correction(
    app: Any, page: Any, ready: dict[str, Any]
) -> None:
    """Oracle: spec-anchored — UC-ZF-013 AK3 "Ein Foto ohne erkennbaren Betrag → keine Buchung, ein
    Prüfposten mit Foto und Grund; Korrektur im Tool erzeugt die Buchung"."""
    answer = _submit(app, variant=3, unreadable="true", confidence=0.1)
    receipt_id = answer["receipt_id"]
    assert answer["status"] != "booked"

    app.open(page, "/receipts")
    card = page.locator(f'article[aria-labelledby="receipt-{receipt_id}-title"]')
    assert shown(card, "not readable"), body(page)
    assert card.locator("img").count() == 1, "the photo is there"

    page.locator(f"#receipt-{receipt_id}-date").fill((date.today() - timedelta(days=1)).isoformat())
    page.locator(f"#receipt-{receipt_id}-amount").fill("18.90")
    page.locator(f"#receipt-{receipt_id}-merchant").fill("Corrected Merchant")
    page.locator(f"#receipt-{receipt_id}-account").select_option(label="Receipt checking")
    card.get_by_role("button", name="Book", exact=True).click()
    page.wait_for_selector("p.ui-notice, span.ui-notice")

    assert shown(page, "Booked: Corrected Merchant")
    app.open(page, "/entry")
    booking = page.locator("table.entry-table tbody tr", has_text="Corrected Merchant")
    assert booking.count() == 1
    assert euro(-1_890) in booking.first.inner_text()
