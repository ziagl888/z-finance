"""What the first-run wizard leaves behind (SPEC AK20, UC NF10 / UC-ZF-001 step 4).

The wizard is a screen, but its promise is a state of the database: after **Finish** there is a
first account, the categories the user kept, an optional levy rate, and the first run is over. That
promise is checked here through the API, in the order the wizard calls it (account, levy, setup) —
the screen's own behaviour is `frontend/src/features/first-run/first-run.test.tsx`.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

FIRST_ACCOUNT = {
    "name": "Everyday account",
    "area": "private",
    "type": "giro",
    "opening_balance_cents": 120_000,
    "opening_date": "2026-01-02",
    "reserve_floor_cents": 0,
}


def offered_categories(client: TestClient, language: str = "en") -> list[dict[str, Any]]:
    """The default set the wizard shows in step 3."""
    offer = client.get("/api/accounts/setup", params={"language": language}).json()
    assert offer["offer_default_set"] is True
    return list(offer["default_categories"])


def test_wizard_seeds_chosen_categories(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK6: kept categories exist, removed ones do not."""
    drafts = offered_categories(client)
    dropped = drafts[1]["name"]
    kept = [draft for draft in drafts if draft["name"] != dropped]

    assert client.post("/api/accounts", json=FIRST_ACCOUNT).status_code == 201
    assert client.post("/api/accounts/setup", json={"categories": kept}).status_code == 201

    names = {category["name"] for category in client.get("/api/categories").json()}
    assert dropped not in names
    assert {draft["name"] for draft in kept} <= names


def test_wizard_creates_the_first_account(client: TestClient) -> None:
    """Oracle: spec-anchored — UC NF10: the first start leads through the first account."""
    created = client.post("/api/accounts", json=FIRST_ACCOUNT)
    assert created.status_code == 201
    account = created.json()
    assert account["name"] == "Everyday account"
    assert account["opening_balance_cents"] == 120_000
    assert [entry["id"] for entry in client.get("/api/accounts").json()] == [account["id"]]


def test_wizard_ends_the_first_run(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK6: afterwards the setup is done, once."""
    drafts = offered_categories(client)
    client.post("/api/accounts", json=FIRST_ACCOUNT)
    client.post("/api/accounts/setup", json={"categories": drafts})

    assert client.get("/api/accounts/setup").json()["offer_default_set"] is False
    assert client.post("/api/accounts/setup", json={"categories": drafts}).status_code == 409


def test_wizard_adopts_a_renamed_category(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-001 step 4 (AK3): the offered set is editable before adoption."""
    drafts = offered_categories(client)
    drafts[0] = {**drafts[0], "name": "Wages"}
    client.post("/api/accounts", json=FIRST_ACCOUNT)
    client.post("/api/accounts/setup", json={"categories": drafts})

    names = {category["name"] for category in client.get("/api/categories").json()}
    assert "Wages" in names


def test_wizard_stores_an_entered_levy_rate(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK6b: 35 % is sent as 3500 basis points."""
    current = client.get("/api/levy/settings").json()
    assert current["settings_missing"] is True

    client.post("/api/accounts", json=FIRST_ACCOUNT)
    stored = client.put(
        "/api/levy/settings",
        json={
            "levy_rate_bp": 3500,
            "levy_base": current["levy_base"],
            "due_month_day": current["due_month_day"],
            "levy_pot_id": current["levy_pot_id"],
            "account_id": current["account_id"],
        },
    )
    assert stored.status_code == 200
    assert stored.json()["levy_rate_bp"] == 3500

    client.post("/api/accounts/setup", json={"categories": offered_categories(client)})
    assert client.get("/api/levy/settings").json()["levy_rate_bp"] == 3500


def test_a_skipped_levy_step_leaves_the_rate_unset(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK6b: an empty levy step stores nothing."""
    client.post("/api/accounts", json=FIRST_ACCOUNT)
    client.post("/api/accounts/setup", json={"categories": offered_categories(client)})

    levy = client.get("/api/levy/settings").json()
    assert levy["settings_missing"] is True
    assert levy["levy_rate_bp"] == 0


def test_the_german_set_is_offered_in_german(client: TestClient) -> None:
    """Oracle: spec-anchored — UC NF6 + accounts.service._DEFAULT_SETS: the set follows the language."""
    german = {draft["name"] for draft in offered_categories(client, "de")}
    english = {draft["name"] for draft in offered_categories(client, "en")}
    assert german != english
