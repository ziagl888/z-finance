"""Shared fixtures: an app instance on a throwaway data directory and a fake SPA bundle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.app import create_app
from zfinance.config import Settings


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    """A minimal stand-in for the Vite build output (index.html + one hashed asset)."""
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>z-finance</title><div id=root></div>", encoding="utf-8")
    (web / "assets" / "index-abc123.js").write_text("console.log('z');", encoding="utf-8")
    return web


@pytest.fixture
def settings(tmp_path: Path, web_dir: Path) -> Settings:
    """Settings pointing at a temporary data directory that does not exist yet."""
    return Settings(data_dir=tmp_path / "data", web_dir=web_dir)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A TestClient with the lifespan run (data dir created, migrations applied)."""
    # Loopback base URL: the local-only guard refuses TestClient's default Host "testserver".
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as test_client:
        yield test_client
