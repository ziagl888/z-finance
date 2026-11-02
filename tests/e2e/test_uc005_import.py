"""UC-ZF-005 — import a bank statement as CSV and match it, in the browser.

Every test drives the four steps of the assistant with a statement built in the test: semicolon
separated, ``dd.mm.yyyy`` dates and a decimal comma, the format of the example statement in the
manual. Each test gets its own instance, because an import is not undoable and the criteria are
about what the second import of the same file does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import _scenario as scenario
from _ui import body, euro, kpi_value, row, shown

STATEMENT = "statement.csv"


@dataclass
class Prepared:
    """An instance with the default categories, one account and a rule for "SVS"."""

    app: Any
    account: int
    categories: dict[str, int]


def _prepare(factory: Any) -> Prepared:
    """A fresh instance: categories, a business account and the rule of AK2."""
    app = factory()
    categories = scenario.adopt_categories(app)
    account = scenario.account(
        app,
        "Import checking",
        area="business",
        opening_cents=500_000,
        opening=date.today() - timedelta(days=60),
    )
    rule = {"match_kind": "contains", "pattern": "SVS", "category_id": categories["SVS"], "priority": 0}
    app.post("/api/rules", rule)
    return Prepared(app=app, account=account, categories=categories)


def _eur(cents: int) -> str:
    """The German notation a bank writes: ``-85000`` -> ``-850,00``."""
    sign = "-" if cents < 0 else ""
    euros, rest = divmod(abs(cents), 100)
    return f"{sign}{euros:,}".replace(",", ".") + f",{rest:02d}"


def _csv(rows: list[tuple[date, str, int, str]]) -> str:
    """A statement of ``(day, text, cents, counterparty)`` rows."""
    lines = [f"{day.strftime('%d.%m.%Y')};{text};{_eur(cents)};{who}" for day, text, cents, who in rows]
    return "\n".join(["Date;Text;Amount;Counterparty", *lines]) + "\n"


def _upload(page: Any, app: Any, csv: str, *, account: str = "Import checking") -> None:
    """Step 1: choose the account, hand in the file, go on to the profile."""
    app.open(page, "/import")
    page.get_by_label("Account", exact=True).select_option(label=account)
    page.locator("input[type=file]").set_input_files(
        files=[{"name": STATEMENT, "mimeType": "text/csv", "buffer": csv.encode("utf-8")}]
    )
    page.get_by_role("button", name="Continue to the profile").click()
    page.wait_for_load_state("networkidle")


def _map_new_profile(page: Any, bank: str = "Test Bank") -> None:
    """Step 2 for a statement of this shape, then show the preview."""
    page.get_by_label("Bank name", exact=True).fill(bank)
    page.get_by_label("Date column", exact=True).select_option(label="Date")
    page.get_by_label("Text column (purpose)", exact=True).select_option(label="Text")
    page.get_by_label("Amount column", exact=True).select_option(label="Amount")
    page.get_by_label("Counterparty column", exact=True).select_option(label="Counterparty")
    page.get_by_label("Date format", exact=True).fill("%d.%m.%Y")
    page.get_by_label("Decimal separator", exact=True).select_option(index=0)
    _show_preview(page)


def _show_preview(page: Any) -> None:
    page.get_by_role("button", name="Show the preview").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)


def _apply(page: Any) -> None:
    """Step 3: write the bookings and wait for the protocol."""
    page.get_by_role("button", name="Apply:").click()
    page.wait_for_selector("text=Import finished", timeout=15000)


def _protocol(page: Any) -> dict[str, int]:
    """The four figures of the protocol (step 4)."""
    return {
        label: int(kpi_value(page, label))
        for label in ("New", "Matched to planned items", "Duplicates", "Uncategorised")
    }


def test_ak1_the_same_file_a_second_time_brings_no_new_booking(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK1 "Dieselbe CSV zweimal importiert: zweiter Lauf meldet 0
    neue Buchungen"."""
    prepared = _prepare(one_app)
    csv = _csv(
        [
            (date.today() - timedelta(days=3), "SVS QUARTER", -15_000, "SVS"),
            (date.today() - timedelta(days=2), "DEMO SHOP 4711", -3_720, "Demo Shop"),
        ]
    )

    _upload(page, prepared.app, csv)
    _map_new_profile(page)
    _apply(page)
    assert _protocol(page)["New"] == 2

    _upload(page, prepared.app, csv)
    page.get_by_label("Profile", exact=True).select_option(label="Test Bank")
    _show_preview(page)

    assert int(kpi_value(page, "New")) == 0, "the second run brings no new booking"
    assert int(kpi_value(page, "Duplicates")) == 2, "both lines are known from the first run"
    assert shown(page, "every line is a duplicate"), body(page)


def test_ak2_a_rule_gives_the_line_its_category(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK2 "Eine Zeile mit „SVS" im Text und einer Regel „enthält
    SVS → Kategorie SVS" landet in Kategorie SVS"."""
    prepared = _prepare(one_app)
    csv = _csv([(date.today() - timedelta(days=4), "SVS QUARTER 3", -15_000, "SVS")])

    _upload(page, prepared.app, csv)
    _map_new_profile(page)

    line = row(page, "SVS QUARTER 3")
    assert shown(line, "From a rule")
    assert line.locator("select").first.input_value() == str(prepared.categories["SVS"])

    _apply(page)
    prepared.app.open(page, "/entry")
    assert "SVS" in row(page, "SVS QUARTER 3").inner_text()


def test_ak3_a_line_replaces_the_planned_item_it_matches(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK3 "Eine CSV-Zeile mit Betrag -850 € am 03.10. ersetzt den
    geplanten Posten „Miete -850 €, 01.10." statt eine zweite Buchung zu erzeugen"."""
    prepared = _prepare(one_app)
    planned_on = date.today() + timedelta(days=3)
    scenario.expected_item(
        prepared.app,
        day=planned_on,
        cents=-85_000,
        account_id=prepared.account,
        category_id=prepared.categories["Housing"],
        text="Rent",
    )
    csv = _csv([(planned_on + timedelta(days=2), "RENT SEPTEMBER", -85_000, "Demo Landlord")])

    _upload(page, prepared.app, csv)
    _map_new_profile(page)

    line = row(page, "RENT SEPTEMBER")
    assert shown(line, "Matches planned item")
    assert "Rent" in line.inner_text()

    _apply(page)
    protocol = _protocol(page)
    assert protocol["Matched to planned items"] == 1
    assert protocol["New"] == 0

    prepared.app.open(page, "/entry")
    assert page.locator("table.entry-table tbody tr").count() == 1, "the planned item became this booking"
    assert euro(-85_000) in body(page)


def test_ak4_a_file_without_the_amount_column_is_refused_by_name(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK4 "Eine CSV, in der die Betragsspalte fehlt, wird komplett
    abgelehnt mit Nennung der Spalte; es entsteht keine Buchung"."""
    prepared = _prepare(one_app)
    prepared.app.post(
        "/api/imports/profiles",
        {
            "bank_name": "Column Bank",
            "column_map": {"date": "Date", "text": "Text", "amount": "Amount", "counterparty": "Counterparty"},
            "delimiter": "semicolon",
            "encoding": "utf-8",
            "date_format": "%d.%m.%Y",
            "decimal_sep": ",",
        },
    )
    without_amount = "Date;Text;Counterparty\n01.09.2026;DEMO SHOP;Demo Shop\n"

    _upload(page, prepared.app, without_amount)
    page.get_by_label("Profile", exact=True).select_option(label="Column Bank")
    page.wait_for_timeout(200)
    refused = page.locator("p[role=alert]")
    if page.get_by_role("button", name="Show the preview").is_enabled():
        _show_preview(page)

    assert shown(page, "Amount"), body(page)
    assert refused.count() >= 1, "the file has to be refused, naming the column"
    assert shown(refused.first, "Amount")
    assert page.get_by_role("button", name="Apply:").count() == 0

    prepared.app.open(page, "/entry")
    assert shown(page, "No bookings yet"), "a refused file writes nothing"


def test_ak5_the_protocol_agrees_with_the_booking_list(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK5 "Das Protokoll nach dem Import nennt die Anzahl neu /
    Duplikate / gematcht / unkategorisiert, und diese Zahlen stimmen mit der Buchungsliste überein"."""
    prepared = _prepare(one_app)
    planned_on = date.today() + timedelta(days=2)
    scenario.expected_item(
        prepared.app,
        day=planned_on,
        cents=-85_000,
        account_id=prepared.account,
        category_id=prepared.categories["Housing"],
        text="Rent",
    )
    first = _csv([(date.today() - timedelta(days=6), "DEMO SHOP OLD", -1_000, "Demo Shop")])
    _upload(page, prepared.app, first)
    _map_new_profile(page)
    _apply(page)

    second = _csv(
        [
            (date.today() - timedelta(days=6), "DEMO SHOP OLD", -1_000, "Demo Shop"),  # duplicate of the first import
            (date.today() - timedelta(days=5), "SVS QUARTER 4", -15_000, "SVS"),  # a rule gives it a category
            (date.today() - timedelta(days=4), "UNKNOWN MERCHANT", -2_500, "Unknown"),  # no rule: uncategorised
            (planned_on + timedelta(days=1), "RENT", -85_000, "Demo Landlord"),  # matches the planned item
        ]
    )
    _upload(page, prepared.app, second)
    page.get_by_label("Profile", exact=True).select_option(label="Test Bank")
    _show_preview(page)
    _apply(page)

    protocol = _protocol(page)
    assert protocol == {"New": 2, "Matched to planned items": 1, "Duplicates": 1, "Uncategorised": 1}

    prepared.app.open(page, "/entry")
    bookings = page.locator("table.entry-table tbody tr")
    assert bookings.count() == 4, "one from the first import, two new ones and the matched planned item"
    assert page.locator("table.entry-table tbody tr", has_text="DEMO SHOP OLD").count() == 1, "no duplicate"
    uncategorised = page.locator("table.entry-table tbody tr", has_text="UNKNOWN MERCHANT").first
    assert uncategorised.locator("td").nth(2).inner_text().strip() == "", "the uncategorised line has no category"


def test_ak6_a_line_that_cannot_be_booked_is_listed_and_skipped(page: Any, one_app: Any) -> None:
    """Oracle: spec-anchored — UC-ZF-005 "Fehlerverhalten": "unparsebare Zeilen im Protokoll gelistet
    und übersprungen. Nichts wird still verworfen." The line that cannot be booked here is line 41 of
    a file of 50, dated before the opening date of the account.

    AK6 itself ("ein Import mit einem Fehler in Zeile 40 von 50 schreibt keine der 50 Zeilen") is
    about a failure *while writing* (Erweiterung 6a). The assistant validates every line before it
    writes anything, so the browser cannot produce that failure; it is covered by the backend
    (``tests/test_db_transaction.py``) and recorded as not observable in
    ``docs/qa/acceptance-1.0.md``, together with what this test measured instead.
    """
    prepared = _prepare(one_app)
    opening = date.today() - timedelta(days=60)
    rows: list[tuple[date, str, int, str]] = []
    for index in range(50):
        day = opening + timedelta(days=index + 1)
        if index == 39:
            day = opening - timedelta(days=5)  # before the opening date: the account refuses it
        rows.append((day, f"LINE {index + 1:02d}", -1_000 - index, "Demo Shop"))

    _upload(page, prepared.app, _csv(rows))
    _map_new_profile(page)

    assert int(kpi_value(page, "Lines with errors")) == 1
    errors = page.locator('table[aria-label="Lines with errors"]')
    assert shown(errors, "opening date"), "the protocol says why the line is not imported"
    assert shown(errors, "41"), "and which line it is"

    _apply(page)
    assert _protocol(page)["New"] == 49
    assert shown(page, "Lines with errors: 1")

    prepared.app.open(page, "/entry")
    assert "LINE 40" not in body(page), "the line that cannot be booked is not written"
