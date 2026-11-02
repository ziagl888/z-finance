"""SPEC-T-2026-CU-9130-004 AK4: the UI language chosen in the settings is stored in the backend."""

from __future__ import annotations

from fastapi.testclient import TestClient

from zfinance.app import create_app
from zfinance.app_settings import get_setting
from zfinance.config import Settings
from zfinance.db import connect


def test_language_is_null_until_chosen(client: TestClient) -> None:
    """Oracle: spec-anchored — AK4: never chosen → language null; supported is exactly ["en", "de"]."""
    response = client.get("/api/i18n/language")

    assert response.status_code == 200
    assert response.json() == {"language": None, "supported": ["en", "de"]}


def test_choice_is_stored_and_survives_restart(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — AK4 / AK18 "Sprachumschaltung persistiert": PUT de, new app on the same data dir → de."""
    response = client.put("/api/i18n/language", json={"language": "de"})
    assert response.status_code == 200
    assert response.json() == {"language": "de", "supported": ["en", "de"]}

    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as restarted:
        assert restarted.get("/api/i18n/language").json()["language"] == "de"

    conn = connect(settings.db_path)
    try:
        assert get_setting(conn, "ui.language") == "de"
    finally:
        conn.close()


def test_unsupported_language_is_refused_and_not_stored(client: TestClient) -> None:
    """Oracle: spec-anchored — AK4: an unsupported code → 422, the stored value is unchanged."""
    client.put("/api/i18n/language", json={"language": "en"})

    response = client.put("/api/i18n/language", json={"language": "xx"})

    assert response.status_code == 422
    assert client.get("/api/i18n/language").json()["language"] == "en"
