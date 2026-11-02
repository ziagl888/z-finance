"""UC-ZF-011 — savings pots with an emergency order, in the browser.

Every criterion gets its own account, so the figures of one pot card cannot be disturbed by the pots
of another test. AK3 needs two months of saving that did not happen; they are produced by moving the
pot's plan start into the past (``_scenario.backdate_pot_plan``), the same technique the demo data
uses for an overdue item.

AK7 asks for the rank to be changed *by dragging*. The list is real HTML5 drag and drop, so the test
drags; what the criterion is really after — the next emergency proposal following the new order — is
asserted after the drag.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

import _scenario as scenario
from _ui import euro, months_ahead, read_cents, row, shown, text


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, int]:
    """Categories and one savings account per criterion."""
    scenario.adopt_categories(app)
    return {
        "categories": 0,
        "ak1": scenario.account(app, "AK1 savings", kind="savings", opening_cents=1_000_000),
        "ak2": scenario.account(app, "AK2 savings", kind="savings", opening_cents=200_000),
        "ak3": scenario.account(app, "AK3 savings", kind="savings", opening_cents=1_000_000),
        "ak4": scenario.account(app, "AK4 savings", kind="savings", opening_cents=100_000),
        "ak5": scenario.account(app, "AK5 savings", kind="savings", opening_cents=100_000),
        "ak6": scenario.account(app, "AK6 savings", kind="savings", opening_cents=1_000_000),
    }


def _card(page: Any, account: str) -> Any:
    """The card of one account: its figures, its pots and its emergency order."""
    return page.locator(f'section[aria-label="{account}"]')


def _figure(page: Any, account: str, label: str) -> int:
    """One of the four figures on top of an account's card."""
    return read_cents(text(_card(page, account).locator("div.ui-kpi", has_text=label).first.locator(".ui-kpi-value")))


def _pot(page: Any, name: str) -> Any:
    return page.locator(f'article[aria-label="{name}"]')


def test_ak1_the_monthly_rate_of_a_pot(app: Any, page: Any, ready: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK1 "Topf Ziel 3.000 €, Zieldatum in 10 Monaten, Stand 500 €
    → angezeigte Monatsrate 250 €"."""
    pot = scenario.pot(
        app,
        "AK1 holiday",
        ready["ak1"],
        target_amount_cents=300_000,
        target_date=months_ahead(date.today(), 10).isoformat(),
    )
    app.post(f"/api/pots/{pot}/deposits", {"amount_cents": 50_000, "date": date.today().isoformat()})

    app.open(page, "/pots")

    card = _pot(page, "AK1 holiday")
    assert shown(card, f"Needs {euro(25_000)} a month")
    assert shown(card, f"{euro(50_000)} of {euro(300_000)}")


def test_ak2_the_free_balance_limits_what_can_be_assigned(app: Any, page: Any, ready: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK2 "Freier Saldo = Kontosaldo - Summe aller Topfstände; eine
    Zuweisung über den freien Saldo hinaus wird verweigert"."""
    pot = scenario.pot(app, "AK2 emergency fund", ready["ak2"], target_amount_cents=500_000)
    app.post(f"/api/pots/{pot}/deposits", {"amount_cents": 150_000, "date": date.today().isoformat()})

    app.open(page, "/pots")
    balance = _figure(page, "AK2 savings", "Account balance")
    in_pots = _figure(page, "AK2 savings", "In pots")
    free = _figure(page, "AK2 savings", "Free balance")
    assert free == balance - in_pots

    _pot(page, "AK2 emergency fund").get_by_role("button", name="Add money").click()
    page.wait_for_selector("#pot-money-amount")
    page.locator("#pot-money-amount").fill("900")  # more than the 500 € that are free
    page.get_by_role("button", name="Assign", exact=True).click()
    page.wait_for_selector("[role=alert]")

    refusal = page.locator("[role=alert]").first
    assert shown(refusal, "of the account are free"), text(refusal)
    assert shown(refusal, f"{free / 100:.2f}"), "the refusal names the free balance"
    app.open(page, "/pots")
    assert _figure(page, "AK2 savings", "Free balance") == free, "nothing was assigned"


def test_ak3_two_months_without_a_deposit_put_the_pot_behind_plan(app: Any, page: Any, ready: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK3 "Nach zwei Monaten ohne Einzahlung steht der Topf auf
    „hinter Plan" und nennt die neue Rate"."""
    pot = scenario.pot(
        app,
        "AK3 car",
        ready["ak3"],
        target_amount_cents=300_000,
        target_date=months_ahead(date.today(), 8).isoformat(),
    )
    app.post(f"/api/pots/{pot}/deposits", {"amount_cents": 50_000, "date": date.today().isoformat()})
    scenario.backdate_pot_plan(app.db_path, pot, months_ahead(date.today(), -2))

    app.open(page, "/pots")

    card = _pot(page, "AK3 car")
    assert shown(card, "Behind plan")
    assert shown(card, f"needs {euro(31_250)} a month from now on"), text(card)


def test_ak4_an_expense_assigned_to_a_pot_lowers_both(app: Any, page: Any, ready: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK4 "Eine dem Topf zugeordnete Ausgabe senkt Topfstand und
    Kontosaldo um denselben Betrag"."""
    pot = scenario.pot(app, "AK4 laptop", ready["ak4"], target_amount_cents=100_000)
    app.post(f"/api/pots/{pot}/deposits", {"amount_cents": 80_000, "date": date.today().isoformat()})

    app.open(page, "/pots")
    balance_before = _figure(page, "AK4 savings", "Account balance")
    in_pots_before = _figure(page, "AK4 savings", "In pots")

    scenario.book(app, day=date.today(), cents=-45_00, account_id=ready["ak4"], text="AK4 laptop bag")
    app.open(page, "/pots")
    _pot(page, "AK4 laptop").get_by_role("button", name="Assign expense").click()
    page.wait_for_selector("#pot-expense")
    page.locator("#pot-expense").select_option(index=0)
    page.locator("[role=dialog]").get_by_role("button", name="Assign expense", exact=True).click()
    page.wait_for_selector("p.ui-notice, span.ui-notice")

    app.open(page, "/pots")
    assert _figure(page, "AK4 savings", "Account balance") == balance_before - 4_500
    assert _figure(page, "AK4 savings", "In pots") == in_pots_before - 4_500


def test_ak5_moving_money_between_pots_leaves_the_account_alone(app: Any, page: Any, ready: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK5 "Umschichten 200 € von Topf A nach Topf B ändert den
    Kontosaldo nicht"."""
    first = scenario.pot(app, "AK5 pot A", ready["ak5"], target_amount_cents=100_000)
    scenario.pot(app, "AK5 pot B", ready["ak5"], target_amount_cents=100_000)
    app.post(f"/api/pots/{first}/deposits", {"amount_cents": 50_000, "date": date.today().isoformat()})

    app.open(page, "/pots")
    balance_before = _figure(page, "AK5 savings", "Account balance")

    _pot(page, "AK5 pot A").get_by_role("button", name="Move money").click()
    page.wait_for_selector("#pot-move-amount")
    page.locator("#pot-move-amount").fill("200")
    page.locator("#pot-move-target").select_option(label="AK5 pot B")
    page.get_by_role("button", name="Move", exact=True).click()
    page.wait_for_selector("p.ui-notice, span.ui-notice")

    app.open(page, "/pots")
    assert _figure(page, "AK5 savings", "Account balance") == balance_before
    assert read_cents(text(_pot(page, "AK5 pot A").locator("p.pot-balance"))) == 30_000
    assert read_cents(text(_pot(page, "AK5 pot B").locator("p.pot-balance"))) == 20_000


@pytest.fixture(scope="module")
def emergency_pots(app: Any, ready: dict[str, int]) -> dict[str, int]:
    """The four pots of AK6: TV, holiday, PC in that order, plus a locked emergency fund."""
    account = ready["ak6"]
    pots = {
        "tv": scenario.pot(app, "AK6 TV", account, target_amount_cents=50_000),
        "holiday": scenario.pot(
            app,
            "AK6 holiday",
            account,
            target_amount_cents=200_000,
            target_date=months_ahead(date.today(), 10).isoformat(),
        ),
        "pc": scenario.pot(app, "AK6 PC", account, target_amount_cents=100_000),
        "locked": scenario.pot(app, "AK6 emergency fund", account, target_amount_cents=200_000, locked=True),
    }
    for key, cents in (("tv", 30_000), ("holiday", 90_000), ("pc", 60_000), ("locked", 100_000)):
        app.post(f"/api/pots/{pots[key]}/deposits", {"amount_cents": cents, "date": date.today().isoformat()})
    app.put("/api/pots/ranks", {"account_id": account, "pot_ids": [pots["tv"], pots["holiday"], pots["pc"]]})
    return pots


def _propose(page: Any, amount: str, account: str = "AK6 savings") -> Any:
    """Open the emergency dialog and ask for a proposal."""
    page.get_by_role("button", name="Emergency withdrawal").click()
    page.wait_for_selector("#pot-emergency-amount")
    page.locator("#pot-emergency-account").select_option(label=account)
    page.locator("#pot-emergency-amount").fill(amount)
    page.get_by_role("button", name="Show proposal").click()
    page.wait_for_selector('table:has-text("New balance")')
    return page.locator('table:has-text("New balance")').first


def test_ak6_the_emergency_proposal_follows_the_rank_and_spares_the_locked_pot(
    app: Any, page: Any, emergency_pots: dict[str, int]
) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK6 "Töpfe TV (Rang 1, 300 €), Urlaub (Rang 2, 900 €), PC
    (Rang 3, 600 €), Notgroschen (gesperrt, 1.000 €); Notfall 500 € → Vorschlag: 300 € aus TV, 200 €
    aus Urlaub; Notgroschen unangetastet; Urlaub-Zieldatum verschiebt sich entsprechend"."""
    app.open(page, "/pots")

    proposal = _propose(page, "500")

    tv = row(page, "AK6 TV")
    holiday = row(page, "AK6 holiday")
    assert tv.locator("input").input_value() == "300.00", "rank 1 is emptied first"
    assert euro(0) in tv.inner_text(), "and is empty afterwards"
    assert holiday.locator("input").input_value() == "200.00", "rank 2 covers the rest"
    assert euro(70_000) in holiday.inner_text()
    pc = row(page, "AK6 PC")  # rank 3 is listed, but nothing is taken from it
    assert pc.locator("input").input_value() in ("", "0.00"), "rank 3 is not needed for 500 €"
    assert pc.inner_text().count(euro(60_000)) == 2, "its balance is the same before and after"
    assert "AK6 emergency fund" not in proposal.inner_text(), "a locked pot is never proposed"
    assert "→" in holiday.inner_text(), "the target date of the holiday pot moves"


def test_ak7_dragging_a_pot_changes_the_order_the_next_proposal_follows(
    app: Any, page: Any, emergency_pots: dict[str, int]
) -> None:
    """Oracle: spec-anchored — UC-ZF-011 AK7 "Die Rangfolge lässt sich per Ziehen ändern, und der
    nächste Notfall-Vorschlag folgt der neuen Reihenfolge"."""
    app.open(page, "/pots")
    order = _card(page, "AK6 savings").locator("ol.pot-order-list")
    first_before = text(order.locator("li").first)

    page.drag_and_drop(
        f'li[data-pot-id="{emergency_pots["pc"]}"]',
        f'li[data-pot-id="{emergency_pots["tv"]}"]',
    )
    page.wait_for_selector("p.ui-notice, span.ui-notice")
    app.open(page, "/pots")

    order = _card(page, "AK6 savings").locator("ol.pot-order-list")
    assert text(order.locator("li").first) != first_before, "the drag changed the order"
    assert shown(order.locator("li").first, "AK6 PC"), text(order)

    proposal = _propose(page, "500")
    assert shown(proposal.locator("tbody tr").first, "AK6 PC"), "the next proposal follows the new order"
