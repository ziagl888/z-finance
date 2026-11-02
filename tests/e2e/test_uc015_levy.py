"""UC-ZF-015 — the levy reserve and the revenue a month needs, in the browser.

The screen is the one that carries the core rule in figures: what the income of this year will cost
in income tax and SVS (target), how much of it is set aside, and what is still open (gap). AK7 is the
rule itself — an income of 10,000 € at 30 % raises the target and the gap by 3,000 € and the free
amount by 7,000 €, not by 10,000 €.

The prepayments the criteria ask for are made the way a user makes them: the tax plan of UC-ZF-008
writes a planned item, the item is moved to today (only a day can be moved, not the payment) and
confirmed, and the booking is then a prepayment of the running year.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import pytest

import _scenario as scenario
import demo_data
from _ui import body, date_label, euro, kpi_value, month_label, read_cents, row, shown, text


@dataclass
class Levy:
    """An instance with a levy rate, a levy pot, business income of the running year, and — for the
    fixtures that also create a debt — the first instalment's due date."""

    app: Any
    business: int
    savings: int
    categories: dict[str, int]
    pot: int
    debt_due: date | None = None
    """The first instalment's due date, for a fixture that also created a debt (T-2026-CU-9130-042):
    the month it falls in can differ from "this month" when today is a month's last day, so a test
    must ask for that month rather than assume it."""


def _prepare(factory: Any, *, rate_bp: int = 3500, income_cents: int = 2_000_000) -> Levy:
    """Business income of this year, a levy pot, and the rate the criteria use."""
    app = factory()
    categories = scenario.adopt_categories(app)
    business = scenario.account(
        app, "Levy business", area="business", opening_cents=500_000, opening=date(date.today().year, 1, 1)
    )
    savings = scenario.account(app, "Levy savings", kind="savings", opening_cents=1_000_000)
    pot = scenario.pot(app, "Levy reserve", savings, is_levy_pot=True)
    scenario.levy_settings(app, rate_bp=rate_bp, account_id=business, pot_id=pot)
    if income_cents:
        scenario.book(
            app,
            day=date.today(),
            cents=income_cents,
            account_id=business,
            category_id=categories["Fees"],
            text="Levy income",
        )
    return Levy(app=app, business=business, savings=savings, categories=categories, pot=pot)


def _prepay(levy: Levy, cents: int) -> None:
    """Pay one income-tax prepayment of ``cents`` this year (tax plan, confirmed due item)."""
    levy.app.put(
        "/api/settings/tax",
        {
            "business_account_id": levy.business,
            "est_vz_quarter_cents": cents,
            "svs_quarter_cents": None,
            "est_due_dates": ["02-15", "05-15", "08-15", "11-15"],
            "svs_due_dates": ["02-28", "05-31", "08-31", "11-30"],
        },
    )
    plans = [item for item in levy.app.get("/api/recurring") if item["description"] == "Income tax prepayment"]
    first = min(
        (item for plan in plans for item in scenario.planned_items(levy.app, plan["id"])),
        key=lambda item: item["date"],
    )
    demo_data.backdate_planned(levy.app.db_path, int(first["id"]), date.today())
    levy.app.post(f"/api/due/{first['id']}/confirm", {})


def test_ak1_target_set_aside_and_gap(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK1 "Satz 35 %, Business-Einnahmen seit Jahresbeginn 20.000 €,
    Abgaben-Topf 4.000 €, Vorauszahlungen 3.000 € → Soll 7.000 €, Ist 7.000 €, Lücke 0 €; bei Topf
    1.000 € → Lücke 3.000 €"."""
    levy = _prepare(one_app)
    _prepay(levy, 300_000)
    levy.app.post(f"/api/pots/{levy.pot}/deposits", {"amount_cents": 400_000, "date": date.today().isoformat()})

    levy.app.open(page, "/levy")

    assert read_cents(kpi_value(page, "Target")) == 700_000
    assert read_cents(kpi_value(page, "Set aside")) == 700_000
    assert read_cents(kpi_value(page, "Gap")) == 0

    levy.app.post(
        f"/api/pots/{levy.pot}/withdrawals", {"amount_cents": 300_000, "date": date.today().isoformat()}
    )  # the pot holds 1,000 € now
    levy.app.open(page, "/levy")

    assert read_cents(kpi_value(page, "Set aside")) == 400_000
    assert read_cents(kpi_value(page, "Gap")) == 300_000


def test_ak2_the_back_payment_stands_in_the_forecast_of_the_next_year(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK2 "Mit Soll 7.000 € und Vorauszahlungen 3.000 € steht in der
    Vorschau am 01.07. des Folgejahres ein geplanter Posten -4.000 € „Abgaben-Nachzahlung""."""
    levy = _prepare(one_app)
    _prepay(levy, 300_000)
    due = date(date.today().year + 1, 7, 1)

    levy.app.open(page, "/levy")
    year_row = row(page, str(date.today().year))
    assert euro(400_000) in text(year_row), "target minus prepayments"
    assert date_label(page, due) in text(year_row)

    items = levy.app.get("/api/forecast/items", date=due.isoformat(), account_id=levy.business)
    assert [item["amount_cents"] for item in items] == [-400_000], "one planned item of 4,000 € on that day"

    levy.app.open(page, "/forecast")
    page.locator("#forecast-view").select_option(label="Levy business")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Show as table").click()
    page.wait_for_selector("table.chart-table")
    assert date_label(page, due) in page.locator("table.chart-table").inner_text()


@pytest.fixture(scope="module")
def revenue(new_app: Any) -> Levy:
    """The cost blocks of AK3: 2,000 fixed costs, 500 instalments, 300 payoff budget, 200 pot rate."""
    levy = _prepare(new_app, income_cents=0)
    scenario.recurring(
        levy.app,
        description="Levy rent",
        cents=-200_000,
        account_id=levy.business,
        category_id=levy.categories["Housing"],
        day_of_month=1,
    )
    # Due tomorrow, never today: a day later than "today" is always in the future (T-2026-CU-9130-042
    # — a fixed +10 days rolled into the next calendar month from the 21st on, and the test then
    # looked for the instalment in the wrong month's chart row). "Tomorrow" only ever rolls into the
    # next month on the current month's last day, which the assertions below account for by asking
    # for the month `debt_due` actually falls in instead of assuming "this month".
    debt_due = date.today() + timedelta(days=1)
    scenario.debt(
        levy.app,
        creditor="Levy loan",
        area="business",
        total_cents=1_000_000,
        installment_cents=50_000,
        pay_account_id=levy.business,
        first_due=debt_due,
    )
    levy = replace(levy, debt_due=debt_due)
    levy.app.put("/api/debts/settings", {"payoff_budget_cents": 30_000})
    saving = scenario.pot(levy.app, "Levy holiday", levy.savings, target_amount_cents=500_000)
    levy.app.put(f"/api/pots/{saving}/savings-plan", {"from_account_id": levy.business, "amount_cents": 20_000})
    scenario.book(
        levy.app,
        day=date.today(),
        cents=-30_000,
        account_id=levy.business,
        category_id=levy.categories["Software"],
        text="Levy software",
    )
    for back, cents in ((2, 300_000), (1, 500_000)):
        index = date.today().year * 12 + date.today().month - 1 - back
        scenario.book(
            levy.app,
            day=date(index // 12, index % 12 + 1, 10),
            cents=cents,
            account_id=levy.business,
            category_id=levy.categories["Fees"],
            text=f"Levy fee {back}",
        )
    return levy


def test_ak3_the_required_revenue_per_month(page: Any, revenue: Levy) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK3 "Fixkosten 2.000 €, Pflichtraten 500 €, Tilgungsbudget
    300 €, Topf-Raten 200 €, Satz 35 % → Umsatzbedarf 4.615 € je Monat (gerundet)"."""
    revenue.app.open(page, "/levy")

    required = read_cents(text(page.locator("p.levy-required-value")))
    assert required == 461_538, f"3,000 € of costs at 35 % are {euro(required)}, rounded 4,615 €"

    blocks = page.locator('table[aria-label="Required revenue by block"]')
    assert euro(200_000) in text(row(page, "Fixed costs"))
    assert euro(50_000) in text(row(page, "Instalments"))
    assert euro(30_000) in text(row(page, "Payoff budget"))
    assert euro(20_000) in text(row(page, "Savings pot rates"))
    assert shown(blocks, "Required revenue")


def test_ak4_months_under_the_required_revenue_are_marked(page: Any, revenue: Levy) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK4 "In der Einnahmen-Grafik ist ein Monat mit 3.000 €
    Einnahmen bei Bedarf 4.615 € markiert, ein Monat mit 5.000 € nicht"."""
    revenue.app.open(page, "/levy")

    chart = page.locator("figure.chart", has_text="Income against required revenue")
    chart.get_by_role("button", name="Show as table").click()
    page.wait_for_selector("table.chart-table")
    table = page.locator("table.chart-table").first

    weak = _month_label(page, 2)
    strong = _month_label(page, 1)
    assert table.locator("tr", has_text=weak).first.inner_text().strip().endswith("Yes"), (
        "3,000 € are below the required revenue"
    )
    assert table.locator("tr", has_text=strong).first.inner_text().strip().endswith("No"), "5,000 € are not"


def _month_label(page: Any, back: int) -> str:
    """How the chart writes the month ``back`` months ago ("July 2026")."""
    index = date.today().year * 12 + date.today().month - 1 - back
    return month_label(page, date(index // 12, index % 12 + 1, 1))


def test_ak5_a_new_rate_recalculates_everything(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK5 "Eine Änderung des Abgabensatzes von 35 auf 40 % erhöht
    Soll, Nachzahlung und Bedarf ohne weitere Eingabe"."""
    levy = _prepare(one_app)
    levy.app.open(page, "/levy")
    target_before = read_cents(kpi_value(page, "Target"))
    gap_before = read_cents(kpi_value(page, "Gap"))

    page.locator("#levy-rate").fill("40")
    page.get_by_role("button", name="Save and recalculate").click()
    page.wait_for_selector("p.ui-notice")

    assert shown(page, "All figures and charts are recalculated")
    assert read_cents(kpi_value(page, "Target")) == 800_000, "40 % of 20,000 €"
    assert read_cents(kpi_value(page, "Target")) > target_before
    assert read_cents(kpi_value(page, "Gap")) > gap_before
    assert euro(800_000) in text(row(page, str(date.today().year))), "the back-payment follows the rate"


def test_ak6_the_expense_chart_matches_the_month_and_the_debts(page: Any, revenue: Levy) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK6 "Die Ausgaben-Grafik zeigt je Monat Ausgaben nach
    Kategorie, die Linie der Verbindlichkeiten und die Restschuld; Werte stimmen mit UC-ZF-007 und
    UC-ZF-010 überein".

    The debt's first instalment is due tomorrow (fixture), which is almost always this month but
    rolls into the next one when today is a month's last day (T-2026-CU-9130-042); the liability
    figures are therefore checked against the month `revenue.debt_due` actually falls in, not
    against a hard-coded "this month" — the expense-by-category figure (`software`) has no such
    date dependency and stays pinned to the real current month.
    """
    revenue.app.open(page, "/month")
    page.locator("#month-area").select_option(label="Total")
    page.wait_for_load_state("networkidle")
    software = abs(read_cents(text(row(page, "Software"))))

    revenue.app.open(page, "/debts")
    remaining = abs(read_cents(text(row(page, "Total"))))
    instalment = abs(read_cents(text(row(page, "Levy loan").locator("td").nth(3))))

    revenue.app.open(page, "/levy")
    expenses = page.locator("figure.chart", has_text="Expenses and liabilities")
    assert shown(expenses, "Liabilities (instalments and special payments)")
    expenses.get_by_role("button", name="Show as table").click()
    page.wait_for_selector("table.chart-table")

    this_month = _month_label(page, 0)
    month_row = expenses.locator("tr", has_text=this_month).first.inner_text()
    assert euro(software) in month_row, "the expenses by category are the ones of the month report"

    assert revenue.debt_due is not None
    liability_month = month_label(page, revenue.debt_due)
    liability_row = expenses.locator("tr", has_text=liability_month).first.inner_text()
    assert euro(instalment) in liability_row, "and the liabilities line is the instalment of the debt"

    debt_chart = page.locator("figure.chart", has_text="Remaining debt")
    debt_chart.get_by_role("button", name="Show as table").click()
    page.wait_for_timeout(300)
    debt_row = debt_chart.locator("tr", has_text=liability_month).first
    # The curve carries what is owed at the *end* of the month the instalment falls in, so it is
    # gone from the remaining balance there.
    assert read_cents(text(debt_row)) == remaining - instalment


def test_ak7_an_income_raises_target_and_gap_and_only_the_rest_is_free(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-015 AK7 "Eingang 10.000 € bei Satz 30 %: sofort nach der Buchung
    steigt das Rückstellungs-Soll um 3.000 €, die Lücke um 3.000 €, und „frei bis Monatsende" um
    7.000 €" (the core rule of the tool)."""
    levy = _prepare(one_app, rate_bp=3000, income_cents=0)

    levy.app.open(page, "/levy")
    target_before = read_cents(kpi_value(page, "Target"))
    gap_before = read_cents(kpi_value(page, "Gap"))
    free_before = read_cents(text(row(page, "Free")))

    scenario.book(
        levy.app,
        day=date.today(),
        cents=1_000_000,
        account_id=levy.business,
        category_id=levy.categories["Fees"],
        text="Ten thousand",
    )

    levy.app.open(page, "/levy")
    assert read_cents(kpi_value(page, "Target")) - target_before == 300_000
    assert read_cents(kpi_value(page, "Gap")) - gap_before == 300_000
    assert read_cents(text(row(page, "Free"))) - free_before == 700_000, "free rises by the net amount"
    assert shown(page, "Open levy gap"), body(page)
