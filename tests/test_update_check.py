"""Opt-in update notice (SPEC AK20, UC NF10/NF2): off by default, one URL, metadata only.

Every test here uses a fake opener. The real one talks to GitHub, and a test suite that reaches the
internet is exactly what UC NF2 forbids — the one case that must never make a request (the check
switched off) is therefore also the only one that may use the production path.
"""

from __future__ import annotations

import io
import json
import sqlite3
import urllib.request
from collections.abc import Iterator
from typing import IO

import pytest
from fastapi.testclient import TestClient

from zfinance.app_settings import get_setting
from zfinance.config import Settings
from zfinance.db import connect
from zfinance.domain.update_check import service


@pytest.fixture
def conn(client: TestClient, settings: Settings) -> Iterator[sqlite3.Connection]:
    """A connection to the database the app fixture migrated."""
    connection = connect(settings.db_path)
    try:
        yield connection
    finally:
        connection.close()


def release(tag: str, url: str | None = None) -> bytes:
    """A GitHub release answer, cut down to the two fields the service reads."""
    return json.dumps(
        {
            "tag_name": tag,
            "html_url": url or f"https://github.com/{service.REPOSITORY}/releases/tag/{tag}",
            "body": "release notes",
        }
    ).encode()


def opener_for(payload: bytes, seen: list[str]) -> service.Opener:
    """An opener that records the URL it was asked for and answers with ``payload``."""

    def opener(request: urllib.request.Request) -> IO[bytes]:
        seen.append(request.full_url)
        return io.BytesIO(payload)

    return opener


def refusing_opener(request: urllib.request.Request) -> IO[bytes]:
    """An opener that fails the test if it is ever called."""
    raise AssertionError(f"the update check must not open {request.full_url}")


# --- version comparison (AK9) --------------------------------------------------------------------


def test_version_ordering() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK9: 0.1.0 < 0.2.0 < 1.0.0, v-prefix ignored."""
    assert service.is_newer("0.1.0", "v0.2.0") is True
    assert service.is_newer("0.2.0", "v1.0.0") is True
    assert service.is_newer("0.1.0", "v0.1.0") is False
    assert service.is_newer("1.0.0", "v0.9.9") is False
    assert service.is_newer("1.0", "v1.0.0") is False
    assert service.is_newer("1.0.0", "v1.0.1") is True


def test_unparsable_tag_is_no_update() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK9: a tag that is no version is no update."""
    assert service.parse_version("nightly") is None
    assert service.is_newer("0.1.0", "nightly") is False
    assert service.is_newer("0.0.0+unknown", "v0.1.0") is True


# --- the opt-in (AK8) ----------------------------------------------------------------------------


def test_the_check_is_off_by_default(client: TestClient) -> None:
    """Oracle: spec-anchored — UC NF2: no outbound connection unless the user asks for one."""
    body = client.get("/api/update-check").json()
    assert body["enabled"] is False
    assert body["latest_version"] is None
    assert body["update_available"] is False


def test_disabled_check_makes_no_request(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK8: nothing leaves the machine while off."""
    with pytest.raises(service.Disabled):
        service.check_now(conn, opener=refusing_opener)


def test_the_api_refuses_a_run_while_the_check_is_off(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK8; uses the real opener on purpose: the
    refusal happens before any request, so a green test proves nothing was opened."""
    assert client.post("/api/update-check/run").status_code == 409


def test_check_reports_a_newer_release(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK8: one URL, the newer tag with its link."""
    service.set_enabled(conn, True)
    seen: list[str] = []
    state = service.check_now(conn, current_version="0.1.0", opener=opener_for(release("v0.2.0"), seen))
    assert seen == [service.RELEASES_API_URL]
    assert state.latest_version == "v0.2.0"
    assert state.update_available is True
    assert state.release_url == f"https://github.com/{service.REPOSITORY}/releases/tag/v0.2.0"


def test_check_reports_up_to_date(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK8: the same version is no update."""
    service.set_enabled(conn, True)
    state = service.check_now(conn, current_version="0.2.0", opener=opener_for(release("v0.2.0"), []))
    assert state.update_available is False
    assert state.latest_version == "v0.2.0"


def test_only_the_github_api_over_https_is_opened(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK8: any other URL is refused before opening."""
    service.set_enabled(conn, True)
    seen: list[str] = []
    for url in ("http://api.github.com/repos/ziagl888/z-finance/releases/latest", "https://example.invalid/x"):
        with pytest.raises(service.UpdateCheckError):
            service.check_now(conn, opener=opener_for(release("v9.9.9"), seen), url=url)
    assert seen == []


def test_a_foreign_release_link_is_replaced_by_the_project_page(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — service._release_link contract: the link shown is never a foreign URL."""
    service.set_enabled(conn, True)
    state = service.check_now(conn, opener=opener_for(release("v9.9.9", "https://example.invalid/pay"), []))
    assert state.release_url == service.RELEASES_PAGE_URL


def test_an_oversized_response_is_refused(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK8: at most 256 KiB are read."""
    service.set_enabled(conn, True)
    oversized = b'{"tag_name": "v9.9.9", "pad": "' + b"x" * service.MAX_RESPONSE_BYTES + b'"}'
    with pytest.raises(service.UpdateCheckError):
        service.check_now(conn, opener=opener_for(oversized, []))


def test_a_failed_check_keeps_the_last_result(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK8: a refusal leaves the stored result alone."""
    service.set_enabled(conn, True)
    service.check_now(conn, current_version="0.1.0", opener=opener_for(release("v0.2.0"), []))

    def broken(_request: urllib.request.Request) -> IO[bytes]:
        raise OSError("no network")

    with pytest.raises(service.UpdateCheckError):
        service.check_now(conn, opener=broken)
    assert service.state(conn, current_version="0.1.0").latest_version == "v0.2.0"


# --- metadata only (AK8b) ------------------------------------------------------------------------


def test_check_stores_only_metadata_and_writes_no_file(conn: sqlite3.Connection, settings: Settings) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK8b: no file, four settings, nothing executed."""
    service.set_enabled(conn, True)
    before = {path.relative_to(settings.data_dir) for path in settings.data_dir.rglob("*")}
    service.check_now(conn, current_version="0.1.0", opener=opener_for(release("v0.2.0"), []))
    after = {path.relative_to(settings.data_dir) for path in settings.data_dir.rglob("*")}
    assert after == before

    keys = {row[0] for row in conn.execute("SELECT key FROM app_setting WHERE key LIKE 'update_check.%'")}
    assert keys == {
        service.ENABLED_KEY,
        service.LATEST_VERSION_KEY,
        service.RELEASE_URL_KEY,
        service.CHECKED_AT_KEY,
    }
    assert get_setting(conn, service.LATEST_VERSION_KEY) == "v0.2.0"


def test_switching_the_check_off_forgets_the_result(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — set_enabled contract: no stale notice survives switching it off."""
    service.set_enabled(conn, True)
    service.check_now(conn, current_version="0.1.0", opener=opener_for(release("v0.2.0"), []))
    service.set_enabled(conn, False)
    remembered = service.state(conn, current_version="0.1.0")
    assert remembered.enabled is False
    assert remembered.latest_version is None
    assert remembered.release_url == service.RELEASES_PAGE_URL


def test_the_api_round_trip_switches_and_reports(client: TestClient) -> None:
    """Oracle: spec-anchored — /api/update-check contract: PUT stores the switch, GET reports it."""
    assert client.put("/api/update-check", json={"enabled": True}).json()["enabled"] is True
    assert client.get("/api/update-check").json()["enabled"] is True
    assert client.put("/api/update-check", json={"enabled": False}).json()["enabled"] is False


def test_the_release_page_link_belongs_to_this_repository() -> None:
    """Oracle: spec-anchored — UC NF10: the notice links the installer of this project."""
    assert service.RELEASES_PAGE_URL == "https://github.com/ziagl888/z-finance/releases"
    assert service.RELEASES_API_URL.startswith("https://api.github.com/repos/ziagl888/z-finance/")


def test_the_feature_has_no_startup_hook() -> None:
    """Oracle: spec-anchored — UC NF2: nothing asks GitHub by itself.

    The app's lifespan calls every ``run_startup_*`` function a feature offers (backend/zfinance/
    app.py). This feature offers none, which is what keeps the check to the button the user presses.
    """
    assert [name for name in dir(service) if name.startswith("run_startup")] == []
