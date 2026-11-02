"""AK1: ``GET /api/health`` reports liveness, app version and schema version."""

from __future__ import annotations

from fastapi.testclient import TestClient

from zfinance import __version__
from zfinance.migrator import discover


def test_health_returns_ok(client: TestClient) -> None:
    """Oracle: spec-anchored — task T-2026-CU-9130-002 "Basis-Endpunkt /api/health" (AK1a)."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["status"] == "ok"


def test_health_reports_versions(client: TestClient) -> None:
    """Oracle: spec-anchored — AK1b: version = installed package version, schema = highest shipped migration."""
    body = client.get("/api/health").json()

    assert body["version"] == __version__
    assert body["schema_version"] == max(m.number for m in discover())
