"""The browser acceptance run: real Chromium against the real program (release 1.0, task -029).

Every test here starts z-finance as its own process on an empty data directory, fills exactly the
state an acceptance criterion of ``docs/UC-z-finance.md`` names — through the public HTTP API, or by
typing it into the UI where the criterion is about the UI — and then reads the answer off the
rendered page. Nothing is asserted against the API that the criterion says a person has to see.

The run is not part of ``uv run pytest``: it starts browsers and takes minutes. Every test carries
the marker ``e2e`` (added by ``pytest_collection_modifyitems`` below), and ``addopts`` in
``pyproject.toml`` deselects it. To run it:

    just build                                          # the SPA bundle the server serves
    uv run --group docs playwright install chromium      # once per machine
    uv run --group docs pytest tests/e2e -m e2e

The result of a run belongs in ``docs/qa/acceptance-1.0.md``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

import live_app


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything below ``tests/e2e`` as ``e2e``, so no module can forget it."""
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.e2e)


@dataclass
class App:
    """One running z-finance: its base URL, an HTTP client for the setup, and its data directory."""

    base: str
    client: httpx.Client
    data_dir: Path

    @property
    def db_path(self) -> Path:
        """The SQLite file of this instance (what ``demo_data``'s two backdating helpers need)."""
        return self.data_dir / "z-finance.sqlite3"

    def open(self, page: Any, path: str = "/") -> None:
        """Open a route in the browser and wait until the page has its data."""
        page.goto(f"{self.base}{path}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(250)  # charts and fonts settle

    def ok(self, response: httpx.Response) -> Any:
        """The JSON of a 2xx answer; anything else fails the setup with the server's own message."""
        if response.status_code >= 300:
            raise AssertionError(f"setup failed: {response.request.method} {response.request.url}: {response.text}")
        return response.json()

    def post(self, path: str, body: Any) -> Any:
        """``POST`` JSON and return the answer."""
        return self.ok(self.client.post(path, json=body))

    def put(self, path: str, body: Any) -> Any:
        """``PUT`` JSON and return the answer."""
        return self.ok(self.client.put(path, json=body))

    def get(self, path: str, **params: Any) -> Any:
        """``GET`` and return the answer."""
        return self.ok(self.client.get(path, params=params or None))


@pytest.fixture(scope="session")
def chromium() -> Iterator[Any]:
    """One Chromium for the whole run; every test gets its own context and page."""
    missing = live_app.bundle_missing()
    if missing:
        raise RuntimeError(missing)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(chromium: Any) -> Iterator[Any]:
    """A fresh page in a fresh context (no state carried between tests), UI language English."""
    context = live_app.browser_context(chromium, lang="en", height=1400)
    try:
        yield context.new_page()
    finally:
        context.close()


def _factory(stack: contextlib.ExitStack, tmp_path_factory: pytest.TempPathFactory) -> Callable[[], App]:
    """A ``start()`` that runs one more instance and registers it with ``stack``."""
    count = 0

    def start() -> App:
        nonlocal count
        count += 1
        data_dir = tmp_path_factory.mktemp(f"zf-e2e-{count}-")
        base = stack.enter_context(live_app.server(data_dir))
        client = stack.enter_context(httpx.Client(base_url=base, timeout=60))
        return App(base=base, client=client, data_dir=data_dir)

    return start


@pytest.fixture(scope="module")
def new_app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Callable[[], App]]:
    """Factory for instances a module-wide fixture needs; they run until the module is done."""
    with contextlib.ExitStack() as stack:
        yield _factory(stack, tmp_path_factory)


@pytest.fixture
def one_app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Callable[[], App]]:
    """Factory for the instances of a single test; they stop when the test is over.

    A criterion that cannot share its state (an import happens once, a proposal is confirmed once)
    builds its own instance. Stopping it with the test keeps the number of running programs at two
    or three: every one of them writes, and several dozen at once make the machine, not the tool,
    the thing under test.
    """
    with contextlib.ExitStack() as stack:
        yield _factory(stack, tmp_path_factory)


@pytest.fixture(scope="module")
def app(new_app: Callable[[], App]) -> App:
    """One empty instance for the module (the usual case: a module is one use case)."""
    return new_app()


@pytest.fixture(scope="session")
def today() -> date:
    """The day the run happens on; every scenario dates its rows relative to it."""
    return date.today()
