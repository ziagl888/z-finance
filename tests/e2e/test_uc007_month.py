"""UC-ZF-007 — review a month and categorise what is left, in the browser.

Everything happens on ``/month``: the area switch, the two tables by category, the list of
uncategorised bookings with the rule dialog it offers, and the drill-down behind a category.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

import _scenario as scenario
from _ui import body, euro, kpi_value, read_cents, row, shown


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, Any]:
    """A private and a business account with a few bookings in the running month."""
    categories = scenario.adopt_categories(app)
    private = scenario.account(app, "Month private", opening_cents=400_000)
    business = scenario.account(app, "Month business", area="business", opening_cents=600_000)
    today = date.today()
    scenario.book(app, day=today, cents=-10_000, account_id=private, category_id=categories["Groceries"], text="Food A")
    scenario.book(app, day=today, cents=-5_000, account_id=private, category_id=categories["Groceries"], text="Food B")
    scenario.book(
        app, day=today, cents=-20_000, account_id=business, category_id=categories["Software"], text="Editor licence"
    )
    return {"categories": categories, "private": private, "business": business}


def _area(page: Any, label: str) -> None:
    page.locator("#month-area").select_option(label=label)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)


def test_ak1_the_areas_are_separate(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-007 AK1 "Die Monatsübersicht „business" enthält keine privaten
    Buchungen und umgekehrt"."""
    app.open(page, "/month")

    _area(page, "Business")
    business = body(page)
    assert "Software" in business
    assert "Groceries" not in business

    _area(page, "Private")
    private = body(page)
    assert "Groceries" in private
    assert "Software" not in private


def test_ak2_a_new_rule_runs_at_once_and_overwrites_nothing(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-007 AK2 "Eine neue Regel wird sofort auf alle unkategorisierten
    Buchungen angewendet und überschreibt keine bereits kategorisierte"."""
    today = date.today()
    categories = ready["categories"]
    scenario.book(
        app, day=today, cents=-1_200, account_id=ready["private"], category_id=categories["Health"], text="Chemist Oak"
    )
    for index in (1, 2, 3):
        scenario.book(app, day=today, cents=-1_000 * index, account_id=ready["private"], text=f"Chemist Oak {index}")

    app.open(page, "/month")
    _area(page, "Private")
    page.get_by_label("Category for Chemist Oak 1").select_option(label="Health")
    page.wait_for_selector('[role=dialog][aria-label="Make a rule"]')
    page.get_by_label("Text to look for").fill("Chemist Oak")
    # The dialog counts the matches after a short pause, so wait for the sentence, not for the box.
    page.get_by_text("2 other uncategorised bookings match").wait_for()

    page.get_by_role("button", name="Save rule").click()
    page.get_by_text("Rule saved. 2 bookings categorised").wait_for()

    app.open(page, "/entry")
    healthy = page.locator("table.entry-table tbody tr", has_text="Chemist Oak")
    assert healthy.count() == 4
    for index in range(healthy.count()):
        assert "Health" in healthy.nth(index).inner_text(), "the rule categorised the three, the fourth kept Health"


def test_ak3_an_owner_draw_is_neither_income_nor_expense_in_the_total(
    app: Any, page: Any, ready: dict[str, Any], one_app: Any
) -> None:
    """Oracle: spec-anchored — UC-ZF-007 AK3 "In „gesamt" erscheint eine Privatentnahme von 1.000 €
    weder als Einnahme noch als Ausgabe"."""
    fresh = one_app()
    categories = scenario.adopt_categories(fresh)
    private = scenario.account(fresh, "Draw private", opening_cents=100_000)
    business = scenario.account(fresh, "Draw business", area="business", opening_cents=500_000)
    scenario.book(
        fresh,
        day=date.today(),
        cents=-3_000,
        account_id=private,
        category_id=categories["Groceries"],
        text="Draw groceries",
    )
    scenario.transfer(
        fresh,
        day=date.today(),
        cents=100_000,
        from_account_id=business,
        to_account_id=private,
        category_id=categories["Owner draw/contribution"],
        text="Owner draw",
    )

    fresh.open(page, "/month")
    _area(page, "Total")

    assert read_cents(kpi_value(page, "Expenses this month")) == -3_000, "only the groceries are an expense"
    assert read_cents(kpi_value(page, "Income this month")) == 0, "the owner draw is no income"
    assert "Owner draw" not in body(page), "the transfer is in neither table of the total view"

    _area(page, "Private")  # in one area the screen says what it leaves out (UC-ZF-007 2a)
    assert shown(page, f"not counted as income or expenses: in {euro(100_000)}, out {euro(0)}")


def test_ak4_the_drilldown_sums_up_to_the_category(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-007 AK4 "Klick auf eine Kategorie zeigt Einzelbuchungen, deren
    Summe dem Kategoriebetrag entspricht"."""
    app.open(page, "/month")
    _area(page, "Private")

    groceries = row(page, "Groceries")
    amount = read_cents(groceries.inner_text())
    groceries.get_by_role("button", name="Groceries").click()
    page.wait_for_selector('section[aria-label="Bookings of the category"]')

    drilldown = page.locator('section[aria-label="Bookings of the category"]')
    assert shown(drilldown, "Expenses: Groceries")
    assert read_cents(row(page, "Sum").inner_text()) == amount
    assert drilldown.locator("tbody tr").count() == 2
