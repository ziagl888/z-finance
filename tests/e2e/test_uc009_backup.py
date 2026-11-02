"""UC-ZF-009 — back the data up and bring it back, in the browser.

AK1 needs two instances: the one that writes the backup and an empty one that reads it. The file
travels the way it does for a user — it lies in ``backups/<timestamp>-manual/export.json`` of the
first instance's data directory and is handed to the restore form of the second one.

AK2 needs the program to be started again and again, which only a process can do: the data directory
is handed to a dozen fresh servers in turn, and the browser then counts the automatic snapshots the
screen lists.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

import _scenario as scenario
import live_app
from _ui import body, shown


@pytest.fixture(scope="module")
def filled(app: Any) -> dict[str, Any]:
    """An instance with two accounts and four bookings: something worth backing up."""
    categories = scenario.adopt_categories(app)
    checking = scenario.account(app, "Backup checking", opening_cents=250_000)
    savings = scenario.account(app, "Backup savings", kind="savings", opening_cents=700_000)
    for index, amount in enumerate((-2_500, -1_000, -750, 4_000)):
        scenario.book(
            app,
            day=date.today() - timedelta(days=index + 1),
            cents=amount,
            account_id=checking,
            category_id=categories["Groceries"] if amount < 0 else categories["Fees"],
            text=f"Backup booking {index}",
        )
    return {"checking": checking, "savings": savings}


def _back_up(app: Any, page: Any) -> Path:
    """Click "Back up now" and return the ``export.json`` of the snapshot it wrote."""
    app.open(page, "/backup")
    page.get_by_role("button", name="Back up now").click()
    page.wait_for_selector("p.ui-notice")
    assert shown(page, "Backup saved to")
    exports = sorted((app.data_dir / "backups").glob("*-manual/export.json"))
    assert exports, "the snapshot folder of the manual backup is there"
    return exports[-1]


def _accounts_table(page: Any) -> str:
    """The account rows with their balances, as the screen prints them."""
    return page.locator("table", has_text="Reserve floor").first.inner_text()


def test_ak1_a_backup_restored_into_an_empty_instance_has_the_same_figures(
    app: Any, page: Any, filled: dict[str, Any], one_app: Any
) -> None:
    """Oracle: spec-anchored — UC-ZF-009 AK1 "Export → leere Instanz → Wiederherstellen ergibt
    identische Kontosalden und Buchungsanzahl"."""
    export = _back_up(app, page)
    app.open(page, "/accounts")
    original_accounts = _accounts_table(page)
    app.open(page, "/entry")
    original_bookings = page.locator("table.entry-table tbody tr").count()

    empty = one_app()
    empty.open(page, "/")  # a fresh installation opens the first-run wizard
    page.wait_for_url("**/first-run")
    page.get_by_role("link", name="Backup").click()  # to the restore without finishing the first run
    page.wait_for_selector("input[type=file]")
    page.locator("input[type=file]").set_input_files(str(export))
    page.get_by_role("button", name="Restore", exact=True).click()
    page.wait_for_selector("p.ui-notice", timeout=30000)
    assert shown(page, "Restored"), body(page)

    empty.open(page, "/accounts")
    assert _accounts_table(page) == original_accounts
    empty.open(page, "/entry")
    assert page.locator("table.entry-table tbody tr").count() == original_bookings


def test_ak2_every_start_backs_up_and_ten_are_kept(page: Any, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Oracle: spec-anchored — UC-ZF-009 AK2 "Beim Start entsteht automatisch eine Sicherung; es liegen
    nie mehr als 10 automatische Sicherungen im Verzeichnis"."""
    data_dir = tmp_path_factory.mktemp("zf-e2e-restarts-")
    with live_app.server(data_dir) as base, httpx.Client(base_url=base, timeout=60) as client:
        offer = client.get("/api/accounts/setup", params={"language": "en"}).json()
        client.post("/api/accounts/setup", json={"categories": offer["default_categories"]})

    for _ in range(12):  # twelve more starts of the same installation
        with live_app.server(data_dir):
            time.sleep(0.1)  # the snapshot folders are named by the second

    with live_app.server(data_dir) as base:
        page.goto(f"{base}/backup")
        page.wait_for_load_state("networkidle")
        listed = page.locator("table", has_text="Created").first.inner_text()

    automatic = listed.count("Automatic")
    assert automatic >= 1, "a start writes a backup"
    assert automatic == 10, f"never more than ten automatic snapshots are kept, found {automatic}"


def test_ak3_a_restore_into_a_database_with_data_is_refused(app: Any, page: Any, filled: dict[str, Any]) -> None:
    """Oracle: spec-anchored — UC-ZF-009 AK3 "Wiederherstellen in eine nicht-leere Datenbank wird
    verweigert"."""
    export = _back_up(app, page)

    app.open(page, "/backup")
    page.locator("input[type=file]").set_input_files(str(export))
    page.get_by_role("button", name="Restore", exact=True).click()
    page.wait_for_selector("p.error", timeout=30000)

    assert shown(page.locator("p.error").first, "The database already holds data")
    assert shown(page.locator("p.error").first, "never merges")
    app.open(page, "/accounts")
    assert "Backup checking" in _accounts_table(page), "the refused restore changed nothing"
