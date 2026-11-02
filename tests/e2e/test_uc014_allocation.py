"""UC-ZF-014 — split an income onto debts and reserves by rule, in the browser.

The heart of the use case is the levy share: it is not a suggestion but a reservation, so AK6 checks
the dashboard figure right after the income is booked and before anything is confirmed — it has to
have risen by 2,800 € of a 4,000 € income at a rate of 30 %, never by 4,000 €.

Every test builds its own instance: a proposal is made once per income, and confirming or rejecting
one cannot be undone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import _scenario as scenario
from _ui import body, euro, kpi, kpi_value, read_cents, row, shown, text


@dataclass
class Prepared:
    """An instance with a levy rate, a levy pot, a savings pot, optionally a debt, and the rule."""

    app: Any
    business: int
    savings: int
    categories: dict[str, int]
    levy_pot: int
    saving_pot: int
    debt: int | None


def _prepare(factory: Any, *, with_debt: bool = True, rule: bool = True) -> Prepared:
    """The situation of UC-ZF-014: rate 30 %, a rule of 20 % debts and 5 % emergency fund from 500 €."""
    app = factory()
    categories = scenario.adopt_categories(app)
    business = scenario.account(
        app, "Allocation business", area="business", opening_cents=500_000, opening=date.today() - timedelta(days=120)
    )
    savings = scenario.account(app, "Allocation savings", kind="savings", opening_cents=200_000)
    # Both pots sit on the savings account, so every share of the proposal is a real transfer (AK3).
    levy_pot = scenario.pot(app, "Levy reserve", savings, is_levy_pot=True)
    saving_pot = scenario.pot(app, "Emergency fund", savings, target_amount_cents=500_000)
    scenario.levy_settings(app, rate_bp=3000, account_id=business, pot_id=levy_pot)
    debt = None
    if with_debt:
        debt = scenario.debt(
            app,
            creditor="Allocation loan",
            total_cents=500_000,
            installment_cents=20_000,
            pay_account_id=business,
            first_due=date.today() + timedelta(days=20),
            area="business",
        )
    if rule:
        app.post(
            "/api/allocation/rules",
            {
                "name": "Fees split",
                "area": "business",
                "category_id": categories["Fees"],
                "min_amount_cents": 50_000,
                "targets": [
                    {"kind": "debt_plan", "percent_bp": 2000},
                    {"kind": "pot", "pot_id": saving_pot, "percent_bp": 500},
                ],
                "fallback_kind": "reserve",
            },
        )
        # A rule only proposes for income booked after it was saved, and ``created_at`` counts seconds.
        time.sleep(1.1)
    return Prepared(
        app=app,
        business=business,
        savings=savings,
        categories=categories,
        levy_pot=levy_pot,
        saving_pot=saving_pot,
        debt=debt,
    )


def _income(prepared: Prepared, cents: int, text_: str = "Client fee") -> int:
    """Book a business income the rule can see."""
    return scenario.book(
        prepared.app,
        day=date.today(),
        cents=cents,
        account_id=prepared.business,
        category_id=prepared.categories["Fees"],
        text=text_,
    )


def test_ak1_the_proposal_splits_the_income_as_the_rule_says(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK1 "Abgabensatz 30 %, Regel 20 % Schulden / 5 % Notgroschen ab
    500 € business; Einnahme 4.000 € → Vorschlag 1.200 € Abgaben-Topf / 800 € Schulden / 200 €
    Notgroschen, 1.800 € frei"."""
    prepared = _prepare(one_app)
    _income(prepared, 400_000)

    prepared.app.open(page, "/allocation")

    lines = page.locator("table.allocation-lines").first
    assert euro(120_000) in text(row(page, "Levy pot")), "30 % of the income are set aside"
    assert shown(row(page, "Levy pot"), "set aside")
    # The lines that can still be changed carry their amount in an input, the levy line does not.
    assert row(page, 'Debt "Allocation loan"').locator("input").input_value() == "800.00"
    assert row(page, 'Pot "Emergency fund"').locator("input").input_value() == "200.00"
    assert euro(180_000) in text(row(page, "Stays free"))
    assert lines.locator("tbody tr").count() == 4, "three targets and the line that stays free"


def test_ak6_the_free_figure_rises_by_the_income_net_of_the_levy(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK6 "Unmittelbar nach Buchung der Einnahme, vor Bestätigung des
    Vorschlags, ist „frei bis Monatsende" um 2.800 € gestiegen, nicht um 4.000 €" (the core rule)."""
    prepared = _prepare(one_app)

    prepared.app.open(page, "/")
    before = read_cents(kpi_value(page, "Free until"))

    _income(prepared, 400_000)

    prepared.app.open(page, "/")
    after = read_cents(kpi_value(page, "Free until"))
    assert after - before == 280_000, f"free rose by {euro(after - before)}, not by {euro(280_000)}"
    assert shown(kpi(page, "Free until"), f"of which reserved for levies: {euro(120_000)}")

    prepared.app.open(page, "/allocation")
    assert shown(page, "To allocate"), "and the proposal is still waiting, nothing is booked"


def test_ak7_the_levy_share_cannot_be_lowered(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK7 "Der Abgaben-Anteil lässt sich im Vorschlag nicht unter den
    Abgabensatz senken; ein Versuch zeigt den Grund". The screen prevents the attempt instead of
    answering it: the levy line is the only one without an input. The reason stands in the rule form."""
    prepared = _prepare(one_app)
    _income(prepared, 400_000)

    prepared.app.open(page, "/allocation")
    page.wait_for_selector("table.allocation-lines")  # a missing proposal must not pass as "no input"

    levy_line = row(page, "Levy pot")
    assert levy_line.locator("input").count() == 0, "the levy share is not editable"
    assert row(page, 'Pot "Emergency fund"').locator("input").count() == 1, "the other shares are"

    page.get_by_role("button", name="New rule").click()
    page.wait_for_selector("#allocation-name")
    assert shown(page, "already set aside from the moment the income is booked, so you cannot lower it")


def test_ak2_an_income_below_the_minimum_makes_no_proposal(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK2 "Eine Einnahme unter dem Mindestbetrag erzeugt keinen
    Vorschlag"."""
    prepared = _prepare(one_app)
    _income(prepared, 40_000, "Small fee")

    prepared.app.open(page, "/allocation")

    assert kpi_value(page, "To allocate") == "0"
    assert shown(page, "Nothing to allocate")
    assert "Small fee" not in body(page)


def test_ak3_confirming_books_three_transfers_and_moves_no_money_out(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK3 "Bestätigen erzeugt genau drei Umbuchungen; Gesamtsumme
    aller Konten unverändert; Restschuld und Topfstände entsprechend geändert"."""
    prepared = _prepare(one_app)
    _income(prepared, 400_000)

    prepared.app.open(page, "/debts")
    debt_before = abs(read_cents(text(row(page, "Allocation loan"))))
    prepared.app.open(page, "/accounts")
    accounts_before = _sum_of_accounts(page)

    prepared.app.open(page, "/allocation")
    page.get_by_role("button", name="Confirm and book").click()
    page.wait_for_selector("p.ui-notice, span.ui-notice")
    assert shown(page, "Allocated")

    prepared.app.open(page, "/entry")
    assert _booked_transfer_legs(page) == 6, "three transfers, two bookings each"

    prepared.app.open(page, "/accounts")
    assert _sum_of_accounts(page) == accounts_before, "the money moved between accounts, nowhere else"
    prepared.app.open(page, "/debts")
    assert abs(read_cents(text(row(page, "Allocation loan")))) == debt_before - 80_000
    prepared.app.open(page, "/pots")
    assert read_cents(text(page.locator('article[aria-label="Emergency fund"]').locator("p.pot-balance"))) == 20_000
    assert read_cents(text(page.locator('article[aria-label="Levy reserve"]').locator("p.pot-balance"))) == 120_000


def _booked_transfer_legs(page: Any) -> int:
    """Booked transfer bookings in the list; the planned instalments of a debt are transfers too."""
    legs = page.locator("table.entry-table tbody tr", has_text="Transfer")
    return sum(1 for index in range(legs.count()) if "planned" not in text(legs.nth(index)))


def _sum_of_accounts(page: Any) -> int:
    """Every balance of the accounts table, added up."""
    rows = page.locator("table", has_text="Reserve floor").first.locator("tbody tr")
    return sum(read_cents(text(rows.nth(index))) for index in range(rows.count()))


def test_ak4_without_an_open_debt_the_share_goes_to_the_fallback(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK4 "Ohne offene Schuld landet der Schulden-Anteil im
    konfigurierten Ersatzziel"."""
    prepared = _prepare(one_app, with_debt=False)
    _income(prepared, 400_000)

    prepared.app.open(page, "/allocation")

    reserve = row(page, "Total reserve")
    assert reserve.count() == 1, body(page)
    assert reserve.locator("input").input_value() == "800.00", "the debt share went to the configured fallback"
    assert shown(reserve, "stays on the account")
    assert "Debt" not in text(page.locator("table.allocation-lines").first)


def test_ak5_a_proposal_open_for_seven_days_is_highlighted_and_unbooked(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-014 AK5 "Ein 7 Tage offener Vorschlag ist im Dashboard
    hervorgehoben und nicht gebucht"."""
    prepared = _prepare(one_app)
    _income(prepared, 400_000)
    proposals = prepared.app.get("/api/allocation")["proposals"]
    assert len(proposals) == 1
    scenario.backdate_proposal(prepared.app.db_path, int(proposals[0]["id"]), date.today() - timedelta(days=7))

    prepared.app.open(page, "/")

    waiting = page.locator("tr.dashboard-waiting")
    assert waiting.count() == 1, "the dashboard marks the proposal that has been waiting"
    assert shown(waiting, "7 days")
    assert shown(page.locator("p.dashboard-alert").first, "waiting for 7 days or more")

    prepared.app.open(page, "/allocation")
    assert shown(page.locator("span.ui-badge", has_text="open for").first, "open for 7 days")
    assert "ui-kpi-alert" in (kpi(page, "To allocate").get_attribute("class") or "")
    prepared.app.open(page, "/entry")
    assert _booked_transfer_legs(page) == 0, "nothing is booked"
