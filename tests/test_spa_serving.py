"""AK4: the built SPA is served by the backend without swallowing the API."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from zfinance.app import create_app
from zfinance.config import Settings


def test_root_serves_index(client: TestClient) -> None:
    """Oracle: spec-anchored — UC NF1 "Frontend als gebautes Bundle im Backend eingebettet"."""
    response = client.get("/")

    assert response.status_code == 200
    assert "<div id=root>" in response.text


def test_client_route_falls_back_to_index(client: TestClient) -> None:
    """Oracle: spec-anchored — spa.py contract: reloading a client-side route returns index.html."""
    response = client.get("/accounts/42")

    assert response.status_code == 200
    assert "<div id=root>" in response.text


def test_static_asset_served(client: TestClient) -> None:
    """Oracle: spec-anchored — spa.py contract: an existing bundle file is served as itself."""
    response = client.get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert response.text == "console.log('z');"


def test_unknown_api_path_is_json_404(client: TestClient) -> None:
    """Oracle: spec-anchored — spa.py contract: /api/* never falls through to the SPA."""
    for path in ("/api", "/api/does-not-exist"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")


def test_path_traversal_does_not_leave_bundle(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — spa.py contract: resolved paths must stay inside the bundle directory."""
    secret = settings.web_dir.parent / "secret.txt"
    secret.write_text("do not serve", encoding="utf-8")

    for path in ("/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt"):
        assert "do not serve" not in client.get(path).text


def test_missing_bundle_gives_hint(tmp_path: Path, web_dir: Path) -> None:
    """Oracle: spec-anchored — spa.py contract: no bundle → 503 with a build hint, API still up."""
    shutil.rmtree(web_dir)
    app = create_app(Settings(data_dir=tmp_path / "data", web_dir=web_dir))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/")
        assert response.status_code == 503
        assert "just build" in response.text
        assert client.get("/api/health").status_code == 200
