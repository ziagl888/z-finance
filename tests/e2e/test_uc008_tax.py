"""UC-ZF-008 — plan the income-tax prepayment, the SVS contribution and back-payments, in the browser.

AK3 is a criterion about something that must *not* exist. It is checked where a field would have to
be: the labels of every control, the headings of every table and the labels of every figure on all
screens of the tool. The manual is left out on purpose — it is prose, and it says in words that there
is no VAT in z-finance, which would match a plain text search for the word.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest

import _scenario as scenario
import demo_data
from _ui import euro, row, shown

#: The due dates the tool proposes (``tax.service.DEFAULT_EST_DUE_DATES``) and AK1 asks for.
DUE_LABELS = ("February 15", "May 15", "August 15", "November 15")

#: Every screen of the tool, without the manual (see the module docstring).
SCREENS = (
    "/",
    "/entry",
    "/due",
    "/import",
    "/receipts",
    "/recurring",
    "/pots",
    "/debts",
    "/payoff",
    "/allocation",
    "/tax",
    "/levy",
    "/forecast",
    "/month",
    "/accounts",
    "/settings",
    "/backup",
)
VAT_WORDS = re.compile(r"\b(VAT|USt|UVA|Umsatzsteuer|Vorsteuer|value added tax)\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, Any]:
    """Categories and a business account, as UC-ZF-008 asks for."""
    categories = scenario.adopt_categories(app)
    return {
        "categories": categories,
        "business": scenario.account(app, "Tax business", area="business", opening_cents=800_000),
    }


def _save_prepayment(app: Any, page: Any, amount: str) -> None:
    """Enter the amount per due date on ``/tax`` and save."""
    app.open(page, "/tax")
    page.locator("#tax-account").select_option(label="Tax business")
    page.locator("#tax-est").fill(amount)
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_selector("span[role=status]")


def test_ak1_four_planned_prepayments_on_the_legal_dates(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-008 AK1 "Nach Eingabe ESt-VZ 1.500 € je Quartal existieren für die
    nächsten 12 Monate genau vier geplante Posten -1.500 € an 15.02./15.05./15.08./15.11."."""
    _save_prepayment(app, page, "1500")

    plan = page.locator("table", has_text="Due date").first.inner_text()
    for label in DUE_LABELS:
        assert label in plan, f"{label} is missing from the plan"
    assert plan.count(euro(150_000)) == 4, "the plan lists the amount per due date"

    app.open(page, "/recurring")
    items = page.locator("tr", has_text="Income tax prepayment")
    assert items.count() == 4, "one item per due date"
    planned = 0
    for index in range(items.count()):
        text = items.nth(index).inner_text()
        assert euro(-150_000) in text
        counted = re.search(r"(\d+) planned", text)
        assert counted, text
        planned += int(counted.group(1))
    assert planned == 4, "exactly four planned prepayments in the twelve months ahead"


def test_ak2_a_new_amount_leaves_a_confirmed_quarter_alone(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-008 AK2 "Eine Änderung des Quartalsbetrags ändert nur zukünftige,
    unbestätigte Posten; ein bereits bestätigtes Quartal behält seinen Betrag"."""
    plans = [item for item in app.get("/api/recurring") if item["description"] == "Income tax prepayment"]
    assert plans, "the plan of AK1 is still there"
    first = min(
        (item for plan in plans for item in scenario.planned_items(app, plan["id"])),
        key=lambda item: item["date"],
    )
    demo_data.backdate_planned(app.db_path, int(first["id"]), date.today())

    app.open(page, "/due")
    row(page, "Income tax prepayment").get_by_role("button", name="Confirm").click()
    page.wait_for_selector("p.due-notice")

    _save_prepayment(app, page, "1800")

    app.open(page, "/entry")
    listed = page.locator("table.entry-table tbody tr", has_text="Income tax prepayment")
    booked = [
        listed.nth(index).inner_text()
        for index in range(listed.count())
        if "planned" not in listed.nth(index).inner_text()
    ]
    assert len(booked) == 1, "one quarter is paid"
    assert euro(-150_000) in booked[0], "the quarter that is already paid keeps its amount"

    app.open(page, "/recurring")
    rows = page.locator("tr", has_text="Income tax prepayment")
    open_amounts = set()
    for index in range(rows.count()):
        text = rows.nth(index).inner_text()
        if re.search(r"[1-9]\d* planned", text):
            open_amounts.add(euro(-180_000) in text)
    assert open_amounts == {True}, "every open prepayment carries the new amount"


def test_ak3_no_screen_has_a_vat_field(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-008 AK3 "Es gibt im gesamten Tool kein USt-Feld und keine
    USt-Berechnung (Kleinunternehmer)"."""
    app.open(page, "/tax")
    assert shown(page, "there is no VAT in z-finance")

    for path in SCREENS:
        app.open(page, path)
        named = page.evaluate(
            """() => {
                const names = [];
                for (const label of document.querySelectorAll('label, legend, th, .ui-kpi-label')) {
                    names.push(label.textContent || '');
                }
                for (const control of document.querySelectorAll('input, select, textarea, button')) {
                    names.push(control.getAttribute('aria-label') || '');
                    names.push(control.getAttribute('placeholder') || '');
                    names.push(control.getAttribute('name') || '');
                }
                return names;
            }"""
        )
        offending = [name for name in named if VAT_WORDS.search(name)]
        assert offending == [], f"{path} offers a VAT field: {offending}"
