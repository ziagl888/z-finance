"""UC-ZF-004 — work through the due items, in the browser.

An item is only overdue through time passing, which a run cannot wait for: the planned occurrences
are moved into the past with ``demo_data.backdate_planned``, the same helper (and the same reason)
the demo data of the manual uses.

AK1 has a second half: "... und ist in der Buchungsliste als aus Dauerbuchung stammend erkennbar".
The booking list of ``/entry`` shows a "recurring" badge on a row whose ``source`` is
``"recurring"`` (``TransactionList.tsx``, fixed in T-2026-CU-9130-039; was finding F-1 of
``docs/qa/acceptance-1.0.md``).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

import _scenario as scenario
import demo_data
from _ui import body, euro, kpi_value, read_cents, row, shown


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, Any]:
    """The default categories and one current account with a reserve of zero."""
    categories = scenario.adopt_categories(app)
    return {"categories": categories, "account": scenario.account(app, "Due checking", opening_cents=500_000)}


def _overdue_item(app: Any, ready: dict[str, Any], *, description: str, cents: int, day: date, category: str) -> int:
    """A recurring item whose first occurrence sits on ``day``, so it is due (or overdue) now."""
    recurring_id = scenario.recurring(
        app,
        description=description,
        cents=cents,
        account_id=ready["account"],
        category_id=ready["categories"][category],
        day_of_month=min(28, max(1, day.day)),
    )
    first = scenario.planned_items(app, recurring_id)[0]
    demo_data.backdate_planned(app.db_path, int(first["id"]), day)
    return recurring_id


def _balance(page: Any) -> int:
    """Today's balance of the account, off the accounts screen."""
    return read_cents(row(page, "Due checking").inner_text())


def test_ak1_a_confirmed_item_moves_the_balance(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-004 AK1 "Ein bestätigter Posten erhöht/vermindert den Kontosaldo
    um seinen Betrag und ist in der Buchungsliste als aus Dauerbuchung stammend erkennbar"."""
    _overdue_item(app, ready, description="AK1 rent", cents=-95_000, day=date.today(), category="Housing")

    app.open(page, "/accounts")
    before = _balance(page)

    app.open(page, "/due")
    row(page, "AK1 rent").get_by_role("button", name="Confirm").click()
    page.wait_for_selector("p.due-notice")
    assert shown(page, "Booked")

    app.open(page, "/accounts")
    assert _balance(page) == before - 95_000
    app.open(page, "/due")
    assert "AK1 rent" not in body(page), "a confirmed item leaves the due list"

    app.open(page, "/entry")
    assert shown(row(page, "AK1 rent"), "recurring"), "the booking list marks a booking that came from a recurring item"


def test_ak2_confirming_another_amount_leaves_the_recurring_item_alone(
    app: Any, page: Any, ready: dict[str, Any]
) -> None:
    """Oracle: spec-anchored — UC-ZF-004 AK2 "Bestätigen mit geändertem Betrag ändert die Dauerbuchung
    nicht (nächster Posten hat den alten Betrag)"."""
    _overdue_item(app, ready, description="AK2 insurance", cents=-18_600, day=date.today(), category="Insurance")

    app.open(page, "/due")
    item = row(page, "AK2 insurance")
    item.locator('input[aria-label="Amount (€)"]').fill("200.00")
    item.get_by_role("button", name="Confirm").click()
    page.wait_for_selector("p.due-notice")

    app.open(page, "/recurring")
    row(page, "AK2 insurance").click()
    page.wait_for_selector("#rec-items-title")
    items = page.locator("section.rec-items table").inner_text()
    assert euro(-20_000) in items, "the confirmed occurrence carries the changed amount"
    planned = page.locator("section.rec-items tr", has_text="planned").first.inner_text()
    assert euro(-18_600) in planned, "the next occurrence keeps the amount of the recurring item"


def test_ak3_a_skipped_item_is_in_neither_the_balance_nor_the_forecast(
    app: Any, page: Any, ready: dict[str, Any]
) -> None:
    """Oracle: spec-anchored — UC-ZF-004 AK3 "Ein übersprungener Posten fehlt in Saldo und Vorschau,
    die Dauerbuchung läuft weiter"."""
    _overdue_item(app, ready, description="AK3 software", cents=-4_900, day=date.today(), category="Software")

    app.open(page, "/accounts")
    balance = _balance(page)
    app.open(page, "/forecast")
    planned_before = read_cents(kpi_value(page, "Planned expenses"))

    app.open(page, "/due")
    page.once("dialog", lambda dialog: dialog.accept())
    row(page, "AK3 software").get_by_role("button", name="Skip").click()
    page.wait_for_selector("p.due-notice")
    assert shown(page, "Skipped")

    app.open(page, "/accounts")
    assert _balance(page) == balance, "a skipped item must not touch the balance"
    app.open(page, "/forecast")
    assert abs(read_cents(kpi_value(page, "Planned expenses"))) == abs(planned_before) - 4_900

    app.open(page, "/recurring")
    assert "planned" in row(page, "AK3 software").inner_text(), "the recurring item goes on"


def test_ak4_an_item_overdue_for_thirty_one_days_is_highlighted(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-004 AK4 "Ein 31 Tage überfälliger Posten wird beim Öffnen von
    „Fällig" hervorgehoben"."""
    _overdue_item(
        app,
        ready,
        description="AK4 hosting",
        cents=-12_000,
        day=date.today() - timedelta(days=31),
        category="Software",
    )

    app.open(page, "/due")

    late = page.locator("tr.due-row-late", has_text="AK4 hosting")
    assert late.count() == 1, "the row of a long overdue item carries its own marker"
    assert shown(late, "Over 30 days overdue")
    assert shown(late, "31 days overdue")
    assert shown(page.locator("p.due-alert"), "more than 30 days overdue")
