"""UC-ZF-012 — plan the payoff of the debts and follow it, in the browser.

The plan is a table of months, so the criteria are read as months: the test does not hard-code
"January 2028" but counts the months between the first month of the plan and the month a debt or the
whole plan ends in. That is what "nach 5 Monaten" and "schuldenfrei nach 10 Monaten" mean.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import pytest

import _scenario as scenario
from _ui import body, euro, kpi, kpi_value, read_cents, shown, text

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _month_index(label: str) -> int:
    """ "September 2026" as a number of months, so two plan months can be subtracted."""
    match = re.search(r"(" + "|".join(MONTHS) + r")\s+(\d{4})", label)
    assert match, f"no month in {label!r}"
    return int(match.group(2)) * 12 + MONTHS.index(match.group(1))


def _first_of_next_month() -> date:
    today = date.today()
    index = today.year * 12 + today.month
    return date(index // 12, index % 12 + 1, 1)


def _month_start(back: int) -> date:
    today = date.today()
    index = today.year * 12 + today.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def _plan_months(page: Any) -> Any:
    """The "Month by month" table of the plan."""
    return page.locator('[aria-labelledby="payoff-months"] table').first


def _order_table(page: Any) -> Any:
    """The "Order" table: which debt is paid off when."""
    return page.locator('[aria-labelledby="payoff-order"] table').first


def _order_row(page: Any, creditor: str) -> Any:
    return _order_table(page).locator("tr", has_text=creditor).first


def _save_plan(page: Any, *, strategy: str, extra: str | None = None) -> None:
    page.get_by_text(strategy, exact=True).click()
    if extra is not None:
        page.locator("#payoff-budget").fill(extra)
    page.get_by_role("button", name="Save plan").click()
    page.wait_for_selector("p.ui-notice, span.ui-notice")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)


@pytest.fixture(scope="module")
def two_debts(app: Any) -> dict[str, Any]:
    """The situation of AK1: 2,000 € at 100 € a month, 4,000 € at 200 €, and income to plan with."""
    categories = scenario.adopt_categories(app)
    checking = scenario.account(
        app, "Payoff checking", opening_cents=1_000_000, opening=date.today() - timedelta(days=200)
    )
    for back in (1, 2, 3):
        scenario.book(
            app,
            day=_month_start(back) + timedelta(days=4),
            cents=150_000,
            account_id=checking,
            category_id=categories["Fees"],
            text=f"Payoff income {back}",
        )
    first_due = _first_of_next_month()
    small = scenario.debt(
        app,
        creditor="Debt A small",
        total_cents=200_000,
        installment_cents=10_000,
        pay_account_id=checking,
        first_due=first_due,
        interest_rate_pct="1.0",
    )
    large = scenario.debt(
        app,
        creditor="Debt B large",
        total_cents=400_000,
        installment_cents=20_000,
        pay_account_id=checking,
        first_due=first_due,
        interest_rate_pct="9.0",
    )
    return {"checking": checking, "small": small, "large": large}


def test_ak1_the_plan_puts_the_extra_on_the_smallest_debt(app: Any, page: Any, two_debts: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK1 "Schulden A (2.000 €, Rate 100 €) und B (4.000 €, Rate
    200 €), Budget 600 €, kleinste zuerst → 300 € Sondertilgung auf A; A nach 5 Monaten getilgt, danach
    600 € auf B; schuldenfrei nach 10 Monaten"."""
    app.open(page, "/payoff")
    _save_plan(page, strategy="Smallest first", extra="300")

    assert shown(page, f"On top of the instalments ({euro(30_000)}): {euro(60_000)} a month in total")

    months = _plan_months(page)
    first = months.locator("tbody tr").first
    assert euro(30_000) in first.inner_text(), "the whole extra goes into the plan's first month"
    assert "Debt A small" in first.inner_text(), "and onto the smallest debt"

    start = _month_index(text(first))
    order = _order_table(page)
    assert _month_index(text(_order_row(page, "Debt A small"))) - start == 4, "A is paid off in the fifth month"
    assert order.locator("tbody tr").first.inner_text().startswith("1"), "A is first in the order"
    assert "Debt A small" in order.locator("tbody tr").first.inner_text()
    assert _month_index(kpi_value(page, "Debt-free")) - start == 9, "debt-free in the tenth month"


def test_ak2_another_strategy_changes_the_order_but_no_debt(app: Any, page: Any, two_debts: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK2 "Ein Strategiewechsel ändert Reihenfolge und Datum, aber
    keine Schuld und keine Rate"."""
    app.open(page, "/debts")
    debts_before = page.locator("table", has_text="Creditor").first.inner_text()
    app.open(page, "/payoff")
    small_ends_before = _month_index(text(_order_row(page, "Debt A small")))

    _save_plan(page, strategy="Most expensive first")

    order = _order_table(page)
    assert "Debt B large" in order.locator("tbody tr").first.inner_text(), "the expensive debt comes first now"
    assert _month_index(text(_order_row(page, "Debt A small"))) > small_ends_before, "and A is paid off later"

    app.open(page, "/debts")
    assert page.locator("table", has_text="Creditor").first.inner_text() == debts_before, (
        "a strategy changes no debt and no instalment"
    )


def test_ak3_without_an_available_amount_there_is_no_plan(app: Any, page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK3 "Bei negativem verfügbarem Betrag wird kein Plan
    berechnet, sondern der Fehlbetrag je Monat angezeigt"."""
    poor = one_app()
    categories = scenario.adopt_categories(poor)
    checking = scenario.account(
        poor, "Poor checking", opening_cents=100_000, opening=date.today() - timedelta(days=200)
    )
    for back in (1, 2, 3):
        scenario.book(
            poor,
            day=_month_start(back) + timedelta(days=3),
            cents=50_000,
            account_id=checking,
            category_id=categories["Fees"],
            text=f"Small income {back}",
        )
    scenario.recurring(
        poor,
        description="Rent",
        cents=-70_000,
        account_id=checking,
        category_id=categories["Housing"],
        day_of_month=1,
    )
    scenario.debt(
        poor,
        creditor="Poor debt",
        total_cents=300_000,
        installment_cents=30_000,
        pay_account_id=checking,
        first_due=_first_of_next_month(),
    )

    poor.open(page, "/payoff")

    assert shown(page, "No plan possible")
    assert shown(page, "exceed your income by")
    assert read_cents(kpi_value(page, "Available per month")) < 0
    assert "Month by month" not in body(page), "no plan is computed"


def test_ak4_a_missed_special_payment_shows_as_behind_plan(app: Any, page: Any, two_debts: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK4 "Nach einer ausgelassenen Sondertilgung steht der Plan auf
    „hinter Plan" … und der Nachholbetrag wird genannt". The third part of the criterion — the
    debt-free date moving by a month — needs a month of real time to pass and is recorded as not
    reproducible in one run in ``docs/qa/acceptance-1.0.md``."""
    app.open(page, "/payoff")
    _save_plan(page, strategy="Smallest first", extra="300")
    scenario.backdate_payoff_month(app.db_path, _month_start(1).strftime("%Y-%m"))

    app.open(page, "/payoff")

    progress = page.locator('[aria-labelledby="payoff-progress"]')
    assert shown(progress, "behind plan")
    assert shown(page.locator("p.payoff-warning"), "extra next month")
    assert read_cents(text(page.locator("p.payoff-warning"))) > 0, "the catch-up amount is named"


def test_ak5_the_what_if_changes_the_date_and_saves_nothing(app: Any, page: Any, two_debts: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-012 AK5 "Was-wäre-wenn +100 € verändert das angezeigte Datum,
    speichert aber nichts"."""
    app.open(page, "/payoff")
    _save_plan(page, strategy="Smallest first", extra="300")
    debt_free = kpi_value(page, "Debt-free")

    page.locator("#payoff-whatif").fill("10000")  # + 100.00 € a month
    page.wait_for_timeout(1200)  # the slider asks the server after a short pause

    result = page.locator("p.payoff-whatif-result").first
    assert shown(result, "Debt-free"), text(result)
    assert shown(result, "instead of"), "the date moves with more money a month"

    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    assert kpi_value(page, "Debt-free") == debt_free, "the try changed no plan"
    assert kpi(page, "Debt-free").inner_text().count("planned:") == 0
