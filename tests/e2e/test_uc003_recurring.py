"""UC-ZF-003 — keep recurring items and expected items, in the browser.

The one place where the browser cannot reach the criterion is AK2: the generation of the planned
items has no control in the UI (it runs when an item is saved and at every start of the program), so
the second run is triggered through ``POST /api/recurring/generate``. What the criterion asks about —
the number of planned items — is read off the list both times.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

import _scenario as scenario
import demo_data
from _ui import date_label, row, shown, wait_row


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, Any]:
    """The default categories and one current account."""
    categories = scenario.adopt_categories(app)
    return {"categories": categories, "account": scenario.account(app, "Recurring checking", opening_cents=500_000)}


def _create(page: Any, *, description: str, amount: str, day: str, start: date, category: str = "Housing") -> None:
    """Fill and save the form of a monthly expense."""
    page.locator("#rec-description").fill(description)
    page.locator("#rec-amount").fill(amount)
    page.locator("#rec-category").select_option(label=f"{category} (Expense)")
    page.locator("#rec-interval").select_option(label="Monthly")
    page.locator("#rec-day").fill(day)
    page.locator("#rec-start").fill(start.isoformat())
    page.get_by_role("button", name="Save", exact=True).click()
    wait_row(page, description)


def _items(page: Any, description: str) -> Any:
    """Open the item list of a recurring item and return its table."""
    row(page, description).click()
    page.wait_for_selector("#rec-items-title")
    page.wait_for_load_state("networkidle")
    return page.locator("section.rec-items table")


def _first_february(start: date) -> date:
    """The last day of the first February on or after ``start`` (the day AK1 asks about)."""
    year = start.year if start.month <= 2 else start.year + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return date(year, 2, 29 if leap else 28)


def test_ak1_day_thirty_one_falls_on_the_last_day_of_february(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-003 AK1 "Eine monatliche Dauerbuchung mit Stichtag 31 erzeugt für
    Februar einen Posten am 28./29."."""
    start = date.today().replace(day=1)
    app.open(page, "/recurring")
    _create(page, description="AK1 rent", amount="950.00", day="31", start=start)

    items = _items(page, "AK1 rent")
    february = _first_february(start)

    assert date_label(page, february, "short") in items.inner_text()
    assert date_label(page, february.replace(day=1), "short") not in items.inner_text()


def test_ak2_generating_twice_gives_the_same_number_of_planned_items(
    app: Any, page: Any, ready: dict[str, Any]
) -> None:
    """Oracle: spec-anchored — UC-ZF-003 AK2 "Zweimaliges Ausführen der Erzeugung ergibt dieselbe
    Anzahl geplanter Posten"."""
    app.open(page, "/recurring")
    _create(page, description="AK2 insurance", amount="186.00", day="15", start=date.today().replace(day=1))
    before = row(page, "AK2 insurance").inner_text()
    assert "planned" in before

    app.post("/api/recurring/generate", {})  # the UI has no control for this: it runs at every start
    app.post("/api/recurring/generate", {})

    app.open(page, "/recurring")
    assert row(page, "AK2 insurance").inner_text() == before


def test_ak3_a_paused_item_keeps_its_booked_occurrences_only(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-003 AK3 "Eine deaktivierte Dauerbuchung hat keine unbestätigten
    Posten mehr, bestätigte bleiben erhalten"."""
    recurring_id = scenario.recurring(
        app,
        description="AK3 subscription",
        cents=-1_299,
        account_id=ready["account"],
        category_id=ready["categories"]["Subscriptions"],
        day_of_month=12,
    )
    first = scenario.planned_items(app, recurring_id)[0]
    demo_data.backdate_planned(app.db_path, int(first["id"]), date.today())
    app.post(f"/api/due/{first['id']}/confirm", {})

    app.open(page, "/recurring")
    page.once("dialog", lambda dialog: dialog.accept())
    row(page, "AK3 subscription").get_by_role("button", name="Pause").click()
    page.wait_for_selector('tr:has-text("AK3 subscription") button:text-is("Resume")')

    listed = row(page, "AK3 subscription")
    assert shown(listed, "paused")
    assert "0 planned" in listed.inner_text()
    items = _items(page, "AK3 subscription")
    assert "booked" in items.inner_text()
    assert "planned" not in items.inner_text()


def test_ak4_a_moved_item_stays_on_its_new_date_after_the_next_run(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-003 AK4 "Ein manuell auf ein anderes Datum verschobener Posten
    bleibt nach dem nächsten Lauf auf dem neuen Datum"."""
    recurring_id = scenario.recurring(
        app,
        description="AK4 software",
        cents=-4_900,
        account_id=ready["account"],
        category_id=ready["categories"]["Software"],
        day_of_month=9,
    )
    planned = scenario.planned_items(app, recurring_id)[0]
    moved_to = date.fromisoformat(planned["date"]).replace(day=17)

    app.open(page, "/recurring")
    items = _items(page, "AK4 software")
    items.locator("tr", has_text=date_label(page, date.fromisoformat(planned["date"]), "short")).get_by_role(
        "button", name="Move"
    ).click()
    page.locator('input[aria-label="New date"]').fill(moved_to.isoformat())
    page.locator("section.rec-items").get_by_role("button", name="Save", exact=True).click()
    page.wait_for_selector(f'section.rec-items tr:has-text("{date_label(page, moved_to, "short")}")')
    assert date_label(page, moved_to, "short") in items.inner_text()

    app.post("/api/recurring/generate", {})  # the next run must not pull it back

    app.open(page, "/recurring")
    items = _items(page, "AK4 software")
    text = items.inner_text()
    assert date_label(page, moved_to, "short") in text
    assert shown(items.locator("tr", has_text=date_label(page, moved_to, "short")), "changed")
