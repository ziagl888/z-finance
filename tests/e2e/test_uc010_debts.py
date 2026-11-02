"""UC-ZF-010 — record debts and follow the repayment, in the browser.

AK2 asks for something no single screen prints: the sum over all accounts *including* the liability.
It is read the way a person would read it — every balance of the accounts table, added up — before and
after the instalment is confirmed.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

import _scenario as scenario
import demo_data
from _ui import body, date_label, euro, months_ahead, read_cents, row, shown


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, Any]:
    """Categories and the two accounts the instalments are paid from."""
    categories = scenario.adopt_categories(app)
    return {
        "categories": categories,
        "checking": scenario.account(app, "Debt checking", opening_cents=800_000),
        "business": scenario.account(app, "Debt business", area="business", opening_cents=900_000),
    }


def _first_of_next_month() -> date:
    today = date.today()
    index = today.year * 12 + today.month
    return date(index // 12, index % 12 + 1, 1)


def _sum_of_all_accounts(page: Any) -> int:
    """Every balance the accounts table lists, added up — liability accounts included."""
    table = page.locator("table", has_text="Reserve floor").first
    rows = table.locator("tbody tr")
    return sum(read_cents(rows.nth(index).inner_text()) for index in range(rows.count()))


def test_ak1_remaining_amount_expected_end_and_the_planned_instalments(
    app: Any, page: Any, ready: dict[str, Any]
) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK1 "Schuld 5.000 €, 1.000 € getilgt, Rate 250 € monatlich ab
    01.10. → Restbetrag 4.000 €, voraussichtliches Ende 01.01. in 16 Monaten, 12 geplante Raten im
    12-Monats-Horizont"."""
    first_due = _first_of_next_month()

    app.open(page, "/debts")
    page.locator("#debt-creditor").fill("AK1 Car Finance")
    page.locator("#debt-total").fill("5000")
    page.locator("#debt-repaid").fill("1000")
    page.locator("#debt-installment").fill("250")
    page.locator("#debt-interval").select_option(label="Monthly")
    page.locator("#debt-first-due").fill(first_due.isoformat())
    page.locator("#debt-pay-account").select_option(label="Debt checking")
    page.locator("form.debt-form").get_by_role("button", name="Save", exact=True).click()
    page.wait_for_selector('tr:has-text("AK1 Car Finance")')

    listed = row(page, "AK1 Car Finance")
    assert euro(-400_000) in listed.inner_text() or euro(400_000) in listed.inner_text()
    assert shown(listed, f"{euro(25_000)} monthly")
    assert shown(listed, "16 instalments")
    assert date_label(page, months_ahead(first_due, 15), "short") in listed.inner_text()

    app.open(page, "/recurring")
    assert "12 planned" in row(page, "AK1 Car Finance").inner_text()


def test_ak2_a_confirmed_instalment_moves_both_sides_and_nothing_else(
    app: Any, page: Any, ready: dict[str, Any]
) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK2 "Eine bestätigte Rate senkt Restbetrag und Zahlkonto um je
    250 €; die Gesamtsumme aller Konten inklusive Verbindlichkeit bleibt unverändert"."""
    debts = app.get("/api/debts")["debts"]
    debt = next(item for item in debts if item["creditor"] == "AK1 Car Finance")
    first = scenario.planned_items(app, debt["recurring_id"])[0]
    demo_data.backdate_planned(app.db_path, int(first["id"]), date.today())

    app.open(page, "/accounts")
    total_before = _sum_of_all_accounts(page)
    checking_before = read_cents(row(page, "Debt checking").inner_text())
    app.open(page, "/debts")
    remaining_before = read_cents(row(page, "AK1 Car Finance").inner_text())

    app.open(page, "/due")
    row(page, "AK1 Car Finance").get_by_role("button", name="Confirm").click()
    page.wait_for_selector("p.due-notice")

    app.open(page, "/accounts")
    assert read_cents(row(page, "Debt checking").inner_text()) == checking_before - 25_000
    assert _sum_of_all_accounts(page) == total_before, "the money moved, it did not disappear"
    app.open(page, "/debts")
    assert abs(read_cents(row(page, "AK1 Car Finance").inner_text())) == abs(remaining_before) - 25_000


def test_ak3_a_debt_paid_off_has_no_instalments_left(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK3 "Restbetrag 0 → die Schuld steht als abbezahlt, es gibt
    keine geplanten Raten mehr in Vorschau oder „Fällig""."""
    scenario.debt(
        app,
        creditor="AK3 Furniture",
        total_cents=100_000,
        installment_cents=25_000,
        pay_account_id=ready["checking"],
        first_due=_first_of_next_month(),
    )

    app.open(page, "/debts")
    row(page, "AK3 Furniture").click()
    page.wait_for_selector("#debt-pay-amount")
    page.locator("#debt-pay-amount").fill("1000")
    page.locator("#debt-pay-date").fill(date.today().isoformat())
    page.locator("#debt-pay-from").select_option(label="Debt checking")
    page.get_by_role("button", name="Book special payment").click()
    page.wait_for_selector("p.ui-notice, span.ui-notice")

    app.open(page, "/debts")
    listed = row(page, "AK3 Furniture")
    assert shown(listed, "paid off")
    assert read_cents(listed.inner_text()) == 0

    app.open(page, "/due")
    assert "AK3 Furniture" not in body(page), "no instalment is due any more"
    app.open(page, "/forecast")
    page.locator("#forecast-view").select_option(label="Debt checking")
    page.wait_for_load_state("networkidle")
    assert "AK3 Furniture" not in body(page), "and none is in the forecast"


def test_ak4_the_overview_carries_the_figures_and_the_sums(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-010 AK4 "Die Schuldenübersicht zeigt je Schuld Restbetrag, Rate,
    Ende und eine Summe je Bereich und gesamt"."""
    scenario.debt(
        app,
        creditor="AK4 Equipment",
        area="business",
        total_cents=600_000,
        repaid_cents=100_000,
        installment_cents=25_000,
        pay_account_id=ready["business"],
        first_due=_first_of_next_month() + timedelta(days=9),
    )

    app.open(page, "/debts")
    table = page.locator("table", has_text="Creditor").first
    headings = table.locator("thead").inner_text()
    for heading in ("Remaining", "Instalment", "Paid off on"):
        assert shown(headings, heading)

    private = abs(read_cents(row(page, "Sum Private").inner_text()))
    business = abs(read_cents(row(page, "Sum Business").inner_text()))
    total = abs(read_cents(row(page, "Total").inner_text()))
    assert business == 500_000, "the business debt of AK4"
    assert total == private + business
    assert shown(row(page, "AK4 Equipment"), f"{euro(25_000)} monthly")
