"""UC-ZF-006 — look ahead at the liquidity of the coming months, in the browser.

The screen's own table twin ("Show as table") is the oracle for values on the curve: the chart draws
one path for a series and has no per-point element, so a figure is read where the screen itself
writes it out. The day of a curve point is chosen the way the chart offers it to the keyboard —
focus, arrow keys, Enter — which is the same handler a click uses.

AK7 is the core rule of the tool at the UI: with an open levy gap the line "Available after levies"
is the balance minus the gap, and the reserve is judged against that line, not against the balance.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import pytest

import _scenario as scenario
from _ui import body, date_label, euro, kpi, kpi_value, months_ahead, read_cents, shown

HORIZON = {3: "3 months", 6: "6 months", 12: "12 months"}


def _next_month_day(day: int) -> date:
    """The ``day``th of next month: always in the future, whatever day the run happens on."""
    today = date.today()
    index = today.year * 12 + today.month  # one month ahead, zero-based month index
    return date(index // 12, index % 12 + 1, day)


def _table(page: Any) -> Any:
    """The table twin of the forecast chart (the values the curve draws)."""
    if page.get_by_role("button", name="Show as table").count():
        page.get_by_role("button", name="Show as table").click()
        page.wait_for_selector("table.chart-table")
    return page.locator("table.chart-table")


def _day_row(page: Any, day: date) -> str:
    """The row of the table twin for one day."""
    return _table(page).locator("tr", has_text=date_label(page, day)).first.inner_text()


def _select_view(page: Any, label: str) -> None:
    page.locator("#forecast-view").select_option(label=label)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)


@pytest.fixture(scope="module")
def basic(app: Any) -> dict[str, Any]:
    """The situation of AK1: 2,000 € on the account, a reserve of 1,000 €, -1,500 € and +3,000 €."""
    scenario.adopt_categories(app)
    account = scenario.account(app, "AK1 forecast", opening_cents=200_000, reserve_cents=100_000)
    low, high = _next_month_day(10), _next_month_day(20)
    scenario.expected_item(app, day=low, cents=-150_000, account_id=account, text="AK1 out")
    scenario.expected_item(app, day=high, cents=300_000, account_id=account, text="AK1 in")
    return {"account": account, "low": low, "high": high}


def test_ak1_minimum_and_the_first_day_under_the_reserve(app: Any, page: Any, basic: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK1 "Konto: Saldo 2.000 €, Reserve 1.000 €, geplante Posten
    -1.500 € am 10. und +3.000 € am 20. → Vorschau meldet Minimum 500 € am 10. und Unterschreitung ab
    dem 10."."""
    low = basic["low"]

    app.open(page, "/forecast")
    _select_view(page, "AK1 forecast")

    assert kpi_value(page, "Lowest point") == euro(50_000)
    assert date_label(page, low) in kpi(page, "Lowest point").inner_text()
    assert date_label(page, low) in kpi(page, "First day under the reserve").inner_text()
    assert shown(page.locator("p.forecast-alert").first, f"Below your reserve from {date_label(page, low)}")


def test_ak2_the_total_view_does_not_move_with_a_transfer(app: Any, page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK2 "Die Gesamtsicht über alle Konten ändert sich durch eine
    Umbuchung nicht"."""
    fresh = one_app()
    scenario.adopt_categories(fresh)
    checking = scenario.account(fresh, "AK2 checking", opening_cents=300_000)
    savings = scenario.account(fresh, "AK2 savings", kind="savings", opening_cents=100_000)
    scenario.expected_item(fresh, day=_next_month_day(5), cents=-40_000, account_id=checking, text="AK2 rent")

    fresh.open(page, "/forecast")
    _select_view(page, "All accounts")
    before = {label: kpi_value(page, label) for label in ("Lowest point", "Planned income", "Planned expenses")}
    curve = _table(page).inner_text()

    scenario.transfer(
        fresh,
        day=_next_month_day(12),
        cents=50_000,
        from_account_id=checking,
        to_account_id=savings,
        text="AK2 transfer",
    )

    fresh.open(page, "/forecast")
    _select_view(page, "All accounts")
    assert {label: kpi_value(page, label) for label in before} == before
    assert _table(page).inner_text() == curve, "the curve over all accounts is the same curve"


def test_ak3_a_what_if_item_changes_the_curve_and_does_not_survive_a_reload(
    app: Any, page: Any, basic: dict[str, Any]
) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK3 "Ein Was-wäre-wenn-Posten verändert die Kurve, ist aber
    nach Neuladen der Seite nicht mehr vorhanden"."""
    app.open(page, "/forecast")
    _select_view(page, "AK1 forecast")
    lowest = kpi_value(page, "Lowest point")

    page.locator("#forecast-wi-amount").fill("500.00")
    page.locator("#forecast-wi-date").fill(_next_month_day(3).isoformat())
    page.locator("#forecast-wi-account").select_option(label="AK1 forecast")
    page.locator("#forecast-wi-text").fill("AK3 what if")
    page.get_by_role("button", name="Add to forecast").click()
    page.wait_for_selector('ul[aria-label="What-if items"]')
    page.wait_for_timeout(400)

    assert kpi_value(page, "Lowest point") != lowest, "the hypothetical item has to move the curve"

    page.reload()
    page.wait_for_load_state("networkidle")
    _select_view(page, "AK1 forecast")

    assert page.locator('ul[aria-label="What-if items"]').count() == 0
    assert "AK3 what if" not in body(page)
    assert kpi_value(page, "Lowest point") == lowest


def test_ak4_a_point_of_the_curve_lists_exactly_that_day(app: Any, page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK4 "Klick auf einen Kurvenpunkt zeigt genau die Posten mit
    diesem Fälligkeitsdatum"."""
    fresh = one_app()
    scenario.adopt_categories(fresh)
    account = scenario.account(fresh, "AK4 checking", opening_cents=500_000)
    chosen = _next_month_day(12)
    scenario.expected_item(fresh, day=chosen, cents=-20_000, account_id=account, text="AK4 first item")
    scenario.expected_item(fresh, day=chosen, cents=-30_000, account_id=account, text="AK4 second item")
    scenario.expected_item(fresh, day=chosen + timedelta(days=1), cents=-40_000, account_id=account, text="AK4 other")

    fresh.open(page, "/forecast")
    _select_view(page, "AK4 checking")
    start = date.fromisoformat(fresh.get("/api/forecast", horizon=12)["start"])

    page.locator("rect.chart-hit").first.focus()
    steps = (chosen - start).days
    for _ in range(steps // 7):
        page.keyboard.press("Shift+ArrowRight")
    for _ in range(steps % 7):
        page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    page.wait_for_selector('table[aria-label="Items on the chosen day"]')
    page.wait_for_timeout(300)

    assert shown(page.locator("#forecast-day-title"), f"Items on {date_label(page, chosen)}")
    items = page.locator('table[aria-label="Items on the chosen day"]')
    assert items.locator("tbody tr").count() == 2
    text = items.inner_text()
    assert "AK4 first item" in text and "AK4 second item" in text
    assert "AK4 other" not in text


@pytest.mark.perf
def test_ak5_the_twelve_month_forecast_renders_in_under_a_second(app: Any, page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK5 "Die 12-Monats-Vorschau über alle Konten rendert bei
    10.000 Buchungen und 200 geplanten Posten in unter einer Sekunde". Measured in the browser, from
    the request for the forecast to the drawn curve, on the machine of the run."""
    fresh = one_app()
    scenario.adopt_categories(fresh)
    account = scenario.account(
        fresh, "AK5 checking", opening_cents=5_000_000, opening=date.today() - timedelta(days=950)
    )
    scenario.bulk_bookings(fresh.db_path, account, count=10_000, planned=200, start=date.today() - timedelta(days=900))

    fresh.open(page, "/forecast")
    page.get_by_text(HORIZON[3], exact=True).click()  # start short, so the long one is a fresh render
    page.wait_for_selector('g[data-line="balance"] path')
    page.wait_for_timeout(500)

    started = time.perf_counter()
    page.get_by_text(HORIZON[12], exact=True).click()
    page.wait_for_selector('g[data-line="balance"] path')
    page.wait_for_function("() => document.querySelector('figure.chart')?.getAttribute('aria-busy') !== 'true'")
    elapsed = time.perf_counter() - started

    assert page.locator('g[data-line="balance"] path').count() >= 1
    assert elapsed < 1.0, f"the twelve months over all accounts took {elapsed:.2f} s to render"


def test_ak6_a_pot_with_a_planned_expense_drops_its_account_at_the_target_date(
    app: Any, page: Any, one_app: Any
) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK6 "Ein Spartopf (Ziel 3.000 €, Zieldatum in 6 Monaten,
    „Ausgabe einplanen") senkt die Kurve des Sparkontos am Zieldatum um 3.000 €; die Gesamtsicht zeigt
    die Einzahlungen davor nicht als Abfluss"."""
    fresh = one_app()
    scenario.adopt_categories(fresh)
    checking = scenario.account(fresh, "AK6 checking", opening_cents=600_000)
    savings = scenario.account(fresh, "AK6 savings", kind="savings", opening_cents=300_000)
    target = months_ahead(date.today().replace(day=15), 6)
    pot = scenario.pot(
        fresh,
        "AK6 holiday",
        savings,
        target_amount_cents=300_000,
        target_date=target.isoformat(),
        plan_expense=True,
    )
    fresh.put(f"/api/pots/{pot}/savings-plan", {"from_account_id": checking, "amount_cents": 50_000})

    fresh.open(page, "/forecast")
    _select_view(page, "AK6 savings")
    before_target = read_cents(_day_row(page, target - timedelta(days=1)))
    on_target = read_cents(_day_row(page, target))
    assert on_target == before_target - 300_000, "the planned expense drops the curve of the savings account"

    _select_view(page, "All accounts")
    assert read_cents(kpi_value(page, "Planned expenses")) in (-300_000, 300_000), (
        "over all accounts only the pot's expense is an outflow, the deposits are transfers"
    )


def test_ak7_available_after_levies_is_net_of_the_gap_and_breaks_the_reserve(app: Any, page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-006 AK7 "Saldo 12.000 €, offene Abgaben-Lücke 3.000 €, Reserve
    10.000 € → die Linie „verfügbar nach Abgaben" liegt bei 9.000 € und die Reserve gilt als
    unterschritten, obwohl der Brutto-Saldo darüber liegt" (the core rule of the tool)."""
    fresh = one_app()
    categories = scenario.adopt_categories(fresh)
    business = scenario.account(fresh, "AK7 business", area="business", opening_cents=200_000, reserve_cents=1_000_000)
    scenario.levy_settings(fresh, rate_bp=3000, account_id=business)
    scenario.book(
        fresh,
        day=date.today() - timedelta(days=1),
        cents=1_000_000,
        account_id=business,
        category_id=categories["Fees"],
        text="AK7 fee",
    )

    fresh.open(page, "/forecast")
    _select_view(page, "AK7 business")

    today_row = _day_row(page, date.today())
    assert euro(1_200_000) in today_row, "the balance is twelve thousand"
    assert euro(900_000) in today_row, "available after levies is nine thousand"
    assert shown(page, f"Open levy gap today: {euro(300_000)}")
    assert shown(page.locator("p.forecast-alert").first, "Below your reserve from")
    assert date_label(page, date.today()) in kpi(page, "First day under the reserve").inner_text()
