"""AK18 / SPEC-T-2026-CU-9130-004 AK3: API error messages answer in the request language.

German expectations are the translations in ``backend/zfinance/i18n/de/LC_MESSAGES/messages.po``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from zfinance.app import create_app
from zfinance.app_settings import set_setting
from zfinance.config import Settings
from zfinance.db import connect
from zfinance.i18n import negotiate_language

UNKNOWN_API_PATH = "/api/does-not-exist"


def store_language(settings: Settings, language: str) -> None:
    conn = connect(settings.db_path)
    try:
        set_setting(conn, "ui.language", language)
    finally:
        conn.close()


def test_stored_setting_wins_over_accept_language(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — AK3a: the ui.language setting beats the header; de.po "Not Found" → "Nicht gefunden"."""
    store_language(settings, "de")

    response = client.get(UNKNOWN_API_PATH, headers={"Accept-Language": "en-US,en;q=0.9"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Nicht gefunden"}


def test_stored_english_wins_over_german_header(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — AK3a: an explicit English choice is kept even for a German browser."""
    store_language(settings, "en")

    response = client.get(UNKNOWN_API_PATH, headers={"Accept-Language": "de-DE"})

    assert response.json() == {"detail": "Not Found"}


def test_accept_language_used_without_setting(client: TestClient) -> None:
    """Oracle: spec-anchored — AK3b: no setting → best supported Accept-Language entry (de-AT → de)."""
    response = client.get(UNKNOWN_API_PATH, headers={"Accept-Language": "fr;q=1, de-AT;q=0.8, en;q=0.5"})

    assert response.json() == {"detail": "Nicht gefunden"}


def test_english_without_setting_or_header(client: TestClient) -> None:
    """Oracle: spec-anchored — AK3c / NF6 "Englisch (Standard)": nothing chosen, no usable header → English."""
    response = client.get(UNKNOWN_API_PATH, headers={"Accept-Language": "fr, es;q=0.5"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_method_not_allowed_is_localized(client: TestClient) -> None:
    """Oracle: spec-anchored — AK3b on a framework error (405); de.po "Methode nicht erlaubt"."""
    response = client.delete("/api/health", headers={"Accept-Language": "de"})

    assert response.status_code == 405
    assert response.json() == {"detail": "Methode nicht erlaubt"}
    assert response.headers["allow"] == "GET"


@pytest.fixture
def probe_client(settings: Settings) -> Iterator[TestClient]:
    """App with one extra POST route raising a detail that is not in any catalog."""
    app = create_app(settings)

    # POST, because the SPA catch-all owns every GET path registered after it.
    @app.post("/api/probe-unknown-detail")
    def probe() -> None:
        raise HTTPException(status_code=409, detail="Some message nobody marked for translation")

    with TestClient(app, base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


def test_unknown_detail_passes_unchanged(probe_client: TestClient) -> None:
    """Oracle: spec-anchored — AK3d: a detail outside the catalog comes back verbatim with its status."""
    response = probe_client.post("/api/probe-unknown-detail", headers={"Accept-Language": "de"})

    assert response.status_code == 409
    assert response.json() == {"detail": "Some message nobody marked for translation"}


def test_parameters_filled_in_after_translation(client: TestClient) -> None:
    """Oracle: spec-anchored — AK3e: de.po text with %(language)s / %(supported)s substituted."""
    response = client.put("/api/i18n/language", json={"language": "fr"}, headers={"Accept-Language": "de"})

    assert response.status_code == 422
    assert response.json() == {"detail": "Sprache fr wird nicht unterstützt. Verfügbar: en, de."}


def test_parameters_in_english(client: TestClient) -> None:
    """Oracle: spec-anchored — AK3e/AK3c: the source text with the parameters substituted."""
    response = client.put("/api/i18n/language", json={"language": "fr"})

    assert response.json() == {"detail": "Unsupported language fr. Available: en, de."}


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("de", "de"),
        ("en-GB,de;q=0.9", "en"),
        ("de;q=0.4, en;q=0.6", "en"),
        ("de;q=0, en;q=0.1", "en"),
        ("de;q=abc, en", "en"),
        ("*", None),
        (",;,", None),
        ("", None),
        (None, None),
    ],
)
def test_negotiate_language_edges(header: str | None, expected: str | None) -> None:
    """Oracle: spec-anchored — RFC 9110 §12.5.4 q-weights (q=0 = not acceptable); malformed entries never raise."""
    assert negotiate_language(header) == expected
