"""The local-only guard: foreign Host (DNS rebinding) and cross-site unsafe requests get 403."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from zfinance.app import create_app
from zfinance.config import Settings


def test_loopback_host_is_served(client: TestClient) -> None:
    """Oracle: spec-anchored — NF1 local app on 127.0.0.1: the normal request path keeps working."""
    for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765"):
        assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_foreign_host_is_refused(client: TestClient) -> None:
    """Oracle: spec-anchored — local_guard.py contract: a rebinding page sends its own name as Host."""
    for path in ("/api/health", "/"):
        response = client.get(path, headers={"Host": "evil.example:8765"})
        assert response.status_code == 403
        assert response.json() == {"detail": "host not allowed"}


def test_cross_site_unsafe_request_is_refused(client: TestClient) -> None:
    """Oracle: spec-anchored — local_guard.py contract: a foreign Origin on a state-changing method is refused."""
    response = client.post("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json() == {"detail": "cross-site request refused"}


def test_same_origin_and_originless_unsafe_requests_pass_the_guard(client: TestClient) -> None:
    """Oracle: spec-anchored — local_guard.py contract: the SPA (dev proxy origin too) and curl are not blocked.

    /api/health has no POST handler, so passing the guard shows up as 405 from the router, not 403.
    """
    for headers in ({"Origin": "http://127.0.0.1:5173"}, {"Origin": "http://localhost:8765"}, {}):
        assert client.post("/api/health", headers=headers).status_code == 405


def test_wildcard_bind_accepts_only_its_own_name_and_loopback(settings: Settings) -> None:
    """Oracle: spec-anchored — task decision: the guard is not widened for a wildcard bind (no interface lookup)."""
    app = create_app(replace(settings, host="0.0.0.0"))  # noqa: S104 — the bind address under test
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for host in ("0.0.0.0:8765", "127.0.0.1:8765", "localhost:8765"):
            assert client.get("/api/health", headers={"Host": host}).status_code == 200
        for host in ("192.168.1.61:8765", "my-pc.lan:8765"):
            assert client.get("/api/health", headers={"Host": host}).status_code == 403
