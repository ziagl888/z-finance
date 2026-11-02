"""UC-ZF-002 — record an income, an expense or a transfer quickly, in the browser.

AK1 is the only criterion of the whole document with a stopwatch in it. It is driven the way it is
written — keyboard only, no mouse: ``Alt+N`` opens the form from any page, ``Tab`` walks the fields,
a letter picks the category in the closed select, ``Enter`` saves. The time is measured by the run,
because a duration is not something a page can display.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import pytest

import _scenario as scenario
from _ui import body, euro, kpi_value, row, shown, wait_row


@pytest.fixture(scope="module")
def ready(app: Any) -> dict[str, Any]:
    """Two private accounts and the default categories."""
    categories = scenario.adopt_categories(app)
    return {
        "categories": categories,
        "checking": scenario.account(app, "Entry checking", opening_cents=200_000),
        "savings": scenario.account(app, "Entry savings", kind="savings", opening_cents=100_000),
    }


def _type_booking(page: Any, amount: str, text: str, category_letter: str) -> None:
    """One booking, keyboard only: amount, text, category by its first letter, Enter.

    The fixed wait after ``Alt+N`` is only for the route/DOM to settle (the amount field always
    exists and accepts input right away); a submit no longer needs the accounts fetch to have
    finished before it is typed — ``EntryForm.onSubmit`` waits for that internally instead of
    racing it (T-2026-CU-9130-043), so a save started this fast still lands.
    """
    page.keyboard.press("Alt+n")  # opens the form from anywhere and focuses the amount
    page.wait_for_timeout(120)
    page.keyboard.type(amount)
    page.keyboard.press("Tab")
    page.keyboard.type(text)
    page.keyboard.press("Tab")
    page.keyboard.type(category_letter)  # the closed select jumps to the first option with that letter
    page.keyboard.press("Enter")
    page.wait_for_selector("span.ui-notice", timeout=5000)


def test_ak1_five_bookings_without_a_mouse_in_under_sixty_seconds(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK1 "Fünf Buchungen lassen sich nacheinander ohne Maus in
    unter 60 Sekunden erfassen (Tastenkürzel öffnen, Tab/Enter navigieren)"."""
    app.open(page, "/")  # start on the dashboard: the shortcut has to work from any page

    started = time.perf_counter()
    for index in range(5):
        _type_booking(page, f"1{index}.50", f"Keyboard booking {index}", "G")
    elapsed = time.perf_counter() - started

    wait_row(page, "Keyboard booking 4")
    rows = page.locator("table.entry-table tbody tr")
    assert rows.count() >= 5
    assert shown(page, "Keyboard booking 4")
    assert elapsed < 60, f"five bookings took {elapsed:.1f} s"


def test_first_booking_survives_a_slow_accounts_fetch(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK1 (a keyboard booking must actually be saved) plus
    T-2026-CU-9130-043's ACCEPTANCE: a product bug, once found, needs a regression test that fails
    before the fix. `EntryPage` fetches accounts/categories after mount (``EntryPage.tsx:63-74``);
    a booking typed and submitted before that fetch resolves must not be silently dropped
    (``EntryForm.tsx:255-258`` used to reject it with "Choose an account." and never call
    ``save()``, sending no ``POST /api/transactions`` — reproduced by hand by delaying the
    ``GET /api/accounts`` response, see ``docs/specs/SPEC-T-2026-CU-9130-043.md``). Before the fix
    this test is red (no POST sent, the booking never appears); after the fix, ``onSubmit`` awaits
    the accounts fetch and re-derives the account default instead of judging it missing on the
    spot, so the same fast keyboard input still lands once the fetch resolves, without the form
    ever needing to reject or block it.
    """
    posted: list[str] = []
    page.on(
        "request",
        lambda r: posted.append(r.url) if r.method == "POST" and r.url.endswith("/api/transactions") else None,
    )

    def delay_accounts(route: Any) -> None:
        if "/api/accounts" in route.request.url and "include_archived" not in route.request.url:
            page.wait_for_timeout(300)  # simulate the contention a full e2e suite run sees
        route.continue_()

    page.route("**/api/accounts*", delay_accounts)

    app.open(page, "/")
    page.keyboard.press("Alt+n")
    page.wait_for_timeout(120)  # a fast, keyboard-only user does not wait for the network
    page.keyboard.type("10.50")
    page.keyboard.press("Tab")
    page.keyboard.type("Race booking")
    page.keyboard.press("Tab")
    page.keyboard.type("G")
    page.keyboard.press("Enter")

    wait_row(page, "Race booking", timeout=10000)
    assert len(posted) == 1, f"expected exactly one POST /api/transactions, got {posted}"
    assert not shown(page, "Choose an account.")


def test_a_pending_save_does_not_let_the_next_booking_overwrite_it(page: Any, app: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK1/AK2: a booking's own save must not be clobbered by
    whatever the form holds once it resumes. Found during OCR review of this task's fix
    (T-2026-CU-9130-043): `EntryForm.onSubmit` awaits `waitAccountsReady()` before it can call
    `save()`+`reset()`; while every field stayed enabled during that wait, a fast typist starting
    the *next* booking in the same window would have their own (unrelated) values wiped by the
    first submit's `reset()` once it resumed — `reset()` reads a snapshot of `draft` captured when
    `Enter` was first pressed. Fixed by disabling the fields (`fieldset disabled={saving}`) for as
    long as a submit is in flight, so nothing can change under a pending save. This test delays the
    `POST /api/transactions` itself (not the accounts fetch) and tries to type a second booking's
    text while the first is still saving.
    """

    def delay_post(route: Any) -> None:
        if route.request.method == "POST":
            page.wait_for_timeout(600)
        route.continue_()

    page.route("**/api/transactions", delay_post)

    app.open(page, "/entry")
    page.locator("#entry-amount").fill("12.00")
    page.locator("#entry-text").fill("First booking")
    page.locator("#entry-category").select_option(label="Groceries (Expense)")
    page.get_by_role("button", name="Save", exact=True).click()

    # The save is now in flight (POST delayed 600ms): the text field must refuse a second value.
    page.wait_for_timeout(100)
    assert page.locator("#entry-text").is_disabled(), "fields must be disabled while a save is in flight"
    page.locator("#entry-text").fill("Second booking", force=True)  # bypass Playwright's own actionability check
    assert page.locator("#entry-text").input_value() == "First booking", "a disabled field refuses a new value"

    page.wait_for_selector("span.ui-notice", timeout=5000)
    wait_row(page, "First booking")
    assert not shown(page, "Second booking"), "the pending booking's own text must survive"


def test_ak2_after_saving_only_the_date_and_the_account_stay(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK2 "Nach dem Speichern sind Datum und Konto vorbelegt, alle
    anderen Felder leer"."""
    app.open(page, "/entry")
    page.locator("#entry-account").select_option(label="Entry savings")
    page.locator("#entry-amount").fill("42.00")
    page.locator("#entry-text").fill("AK2 booking")
    page.locator("#entry-counterparty").fill("AK2 counterparty")
    page.locator("#entry-memo").fill("AK2 note")
    page.locator("#entry-category").select_option(label="Groceries (Expense)")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_selector("span.ui-notice")

    assert page.locator("#entry-date").input_value() == date.today().isoformat()
    assert page.locator("#entry-account").input_value() != ""
    assert "Entry savings" in page.locator("#entry-account option:checked").inner_text()
    for field in ("#entry-amount", "#entry-text", "#entry-category", "#entry-counterparty", "#entry-memo"):
        assert page.locator(field).input_value() == "", f"{field} should be empty after saving"


def test_ak3_a_known_text_suggests_its_category(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK3 "Eine Buchung mit Text „Billa" schlägt ab dem zweiten
    Mal die zuletzt dafür verwendete Kategorie vor"."""
    groceries = str(ready["categories"]["Groceries"])
    app.open(page, "/entry")
    page.locator("#entry-amount").fill("23.40")
    page.locator("#entry-text").fill("Billa")
    page.locator("#entry-category").select_option(label="Groceries (Expense)")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_selector("span.ui-notice")

    app.open(page, "/entry")  # the second time: only the text is typed
    page.locator("#entry-amount").fill("11.90")
    page.locator("#entry-text").type("Billa", delay=40)
    page.wait_for_function(
        "value => document.querySelector('#entry-category').value === value", arg=groceries, timeout=5000
    )

    assert page.locator("#entry-category").input_value() == groceries
    assert shown(page, "Suggested from earlier bookings")


def test_ak4_a_transfer_leaves_the_sum_of_all_accounts_unchanged(app: Any, page: Any, ready: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK4 "Eine Umbuchung erzeugt zwei Buchungen, die Gesamtsumme
    aller Konten ändert sich um 0 €"."""
    app.open(page, "/")
    before = kpi_value(page, "Free until")
    together = text_of_together(page)

    app.open(page, "/entry")
    page.get_by_text("Transfer", exact=True).click()  # the segmented control: the label carries the click
    page.locator("#entry-account").select_option(label="Entry checking")
    page.locator("#entry-to-account").select_option(label="Entry savings")
    page.locator("#entry-amount").fill("500.00")
    page.locator("#entry-text").fill("AK4 transfer")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_selector("span.ui-notice")

    wait_row(page, "AK4 transfer")
    legs = page.locator("table.entry-table tbody tr", has_text="AK4 transfer")
    assert legs.count() == 2, "a transfer is two bookings"
    assert shown(legs.nth(0), "Transfer")
    app.open(page, "/")
    assert text_of_together(page) == together
    assert kpi_value(page, "Free until") == before


def text_of_together(page: Any) -> str:
    """The "Together" figure of the dashboard: the sum over all payment accounts."""
    return row(page, "Together").inner_text().split("\t")[-1].strip()


def test_ak5_a_future_date_becomes_a_planned_item_outside_the_balance(
    app: Any, page: Any, ready: dict[str, Any]
) -> None:
    """Oracle: spec-anchored — UC-ZF-002 AK5 "Eine Buchung mit Datum in der Zukunft landet als
    geplanter Posten und erscheint in der Vorschau (UC-ZF-006), nicht im Saldo"."""
    app.open(page, "/accounts")
    before = row(page, "Entry savings").inner_text()

    app.open(page, "/entry")
    page.locator("#entry-account").select_option(label="Entry savings")
    page.locator("#entry-date").fill((date.today() + timedelta(days=20)).isoformat())
    page.locator("#entry-amount").fill("1234.00")
    page.locator("#entry-text").fill("AK5 planned expense")
    page.locator("#entry-category").select_option(label="Hardware (Expense)")
    assert shown(page, "The date is in the future")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_selector("span.ui-notice")

    assert shown(page, "Saved as a planned item")
    planned = wait_row(page, "AK5 planned expense")
    assert shown(planned, "planned")

    app.open(page, "/accounts")
    assert row(page, "Entry savings").inner_text() == before, "a planned item must not touch the balance"

    app.open(page, "/forecast")
    page.locator("#forecast-view").select_option(label="Entry savings")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert euro(123_400) in body(page).replace("-", ""), "the planned expense belongs in the forecast"
