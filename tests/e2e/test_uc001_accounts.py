"""UC-ZF-001 — set up accounts, areas and categories, in the browser.

The four criteria of the use case, read off the rendered ``/accounts`` screen and the first-run
wizard. The dates of AK1 (1 September, 5 September, 6 September) are taken as what they say —
an opening balance, a booking five days later, the balance a day after that — and dated relative to
the day of the run, because the tool only ever shows *today's* balance and a criterion fixed to a
calendar day would pass in September and fail in March.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

import _scenario as scenario
from _ui import body, euro, row, shown, wait_gone


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, Any]:
    """An instance whose first run is over: the default categories are adopted."""
    return {"categories": scenario.adopt_categories(app)}


def test_ak1_balance_is_opening_balance_minus_the_booking(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK1 "Ein Konto mit Startsaldo 1.000 € am 01.09. und einer
    gebuchten Ausgabe von 200 € am 05.09. zeigt am 06.09. Saldo 800 €"."""
    opening = date.today() - timedelta(days=6)
    account = scenario.account(app, "AK1 current account", opening_cents=100_000, opening=opening)
    scenario.book(
        app,
        day=opening + timedelta(days=4),
        cents=-20_000,
        account_id=account,
        category_id=ready["categories"]["Groceries"],
        text="AK1 expense",
    )

    app.open(page, "/accounts")

    assert euro(80_000) in row(page, "AK1 current account").inner_text()


def test_ak2_an_account_with_bookings_can_only_be_archived(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK2 "Ein Konto mit Buchungen lässt sich nicht löschen, nur
    archivieren; ein archiviertes Konto erscheint nicht im Erfassungsformular"."""
    used = scenario.account(app, "AK2 used account", opening_cents=50_000)
    scenario.book(
        app, day=date.today(), cents=-1_000, account_id=used, category_id=ready["categories"]["Groceries"], text="AK2"
    )

    app.open(page, "/accounts")
    used_row = row(page, "AK2 used account")
    assert used_row.get_by_role("button", name="Delete").count() == 0, "a used account must not offer Delete"
    assert used_row.get_by_role("button", name="Archive").count() == 1

    used_row.get_by_role("button", name="Archive").click()
    page.wait_for_load_state("networkidle")
    app.open(page, "/entry")

    options = page.locator("#entry-account option").all_inner_texts()
    assert "AK2 used account" not in [option.strip() for option in options]


def test_ak3_the_default_categories_are_offered_and_editable_before_adoption(
    page: Any, one_app: Any, today: date
) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK3 "Der Standard-Kategoriensatz wird beim ersten Start
    angeboten und kann vor Übernahme editiert werden"."""
    fresh = one_app()  # a tool that has never been set up: the first run sends every route to the wizard

    fresh.open(page, "/")
    page.wait_for_url("**/first-run")
    page.get_by_role("button", name="Continue").click()  # step 1: language
    page.locator("#first-run-name").fill("AK3 first account")
    page.locator("#first-run-balance").fill("1000.00")
    page.locator("#first-run-date").fill(today.isoformat())
    page.get_by_role("button", name="Continue").click()  # step 2: first account

    assert shown(page, "Categories to start with")
    offered = page.locator('input[aria-label="Name"]')
    assert offered.count() == len(scenario.CATEGORIES)
    housing = offered.nth(scenario.CATEGORIES.index("Housing"))
    assert housing.input_value() == "Housing"
    housing.fill("Living costs")  # the set is editable before it is adopted

    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url(f"{fresh.base}/")
    fresh.open(page, "/categories")

    text = body(page)
    assert "Living costs" in text
    assert "Housing" not in text


def test_ak4_a_category_with_bookings_is_removed_only_onto_a_target(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-001 AK4 "Eine Kategorie mit Buchungen kann nur nach Umbuchung
    auf eine Zielkategorie entfernt werden"."""
    account = scenario.account(app, "AK4 account", opening_cents=100_000)
    leisure = ready["categories"]["Leisure"]
    scenario.book(app, day=date.today(), cents=-3_000, account_id=account, category_id=leisure, text="AK4 booking")

    app.open(page, "/categories")
    row(page, "Leisure").get_by_role("button", name="Delete").click()

    assert shown(page, "This category has 1 booking")
    move = page.get_by_role("button", name="Move and delete")
    assert move.is_disabled(), "without a target category the removal must not be possible"

    page.locator("#category-target").select_option(label="Eating out")
    move.click()
    wait_gone(page, 'td:text-is("Leisure")')

    assert page.get_by_role("cell", name="Leisure", exact=True).count() == 0
    app.open(page, "/entry")
    assert "Eating out" in row(page, "AK4 booking").inner_text()
