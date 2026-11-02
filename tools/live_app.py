"""A running z-finance for everything that needs the real program instead of a test client.

Two callers share this: ``tools/screenshots.py`` photographs the screens with it, and the browser
acceptance run ``tests/e2e/`` drives them. Both need the same three things — a free port, the app as
its own process on a throwaway data directory, and a Chromium context that is deterministic enough
for an assertion (fixed viewport, light theme, no animation, a known UI language).

The server is started the way an end user starts it (``zfinance.cli.main``), so what is driven here
is the shipped program: the SPA bundle out of ``backend/zfinance/web`` and the same HTTP API.
``just build`` has to have run — see ``bundle_missing``.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess  # nosec B404 - starts the app under test, fixed argument list
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent

#: What a browser run needs: the built SPA the backend serves.
BUNDLE = ROOT / "backend" / "zfinance" / "web" / "index.html"

#: Viewport of every screenshot and every acceptance run (a desktop window, nothing responsive).
WIDTH = 1440
DEFAULT_HEIGHT = 900


def bundle_missing() -> str | None:
    """The instruction to print when the SPA bundle is not built yet, else ``None``."""
    if BUNDLE.exists():
        return None
    return "The SPA bundle is missing: run `just build` first."


#: Ports this process has already handed out. A port is free again the moment its server stops, and
#: a second instance on the same port would talk to another data directory, so a port is used once.
_HANDED_OUT: set[int] = set()


def free_port() -> int:
    """A port nobody listens on right now and this process has not used before."""
    for _ in range(100):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in _HANDED_OUT:
            _HANDED_OUT.add(port)
            return port
    raise RuntimeError("no free port")


@contextlib.contextmanager
def server(data_dir: Path) -> Generator[str]:
    """Run z-finance on ``data_dir`` and a free port; yield its base URL, stop it afterwards."""
    port = free_port()
    env = {**os.environ, "ZFINANCE_DATA_DIR": str(data_dir), "ZFINANCE_NO_BROWSER": "1"}
    code = f"from zfinance.cli import main; main(['--no-browser', '--port', '{port}'])"
    # Its own log file, never a pipe: a pipe nobody drains blocks the program on its first write
    # (see docs/qa/install-protocol.md), and a failing acceptance run needs the traceback.
    data_dir.mkdir(parents=True, exist_ok=True)
    log = (data_dir / "server.log").open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(  # noqa: S603  # nosec B603 - fixed argument list
        [sys.executable, "-c", code], env=env, stdout=log, stderr=subprocess.STDOUT
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError("z-finance did not start")
        yield base
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        log.close()


def browser_context(browser: Any, *, lang: str = "en", height: int = DEFAULT_HEIGHT) -> Any:
    """A Chromium context in ``lang``: fixed viewport, light theme, no animation.

    The UI language comes from ``localStorage`` before the first script of the page runs; the
    browser's own locale follows it, because number and date formats are the platform's.
    """
    context = browser.new_context(
        viewport={"width": WIDTH, "height": height},
        locale="de-AT" if lang == "de" else "en-GB",
        color_scheme="light",
        reduced_motion="reduce",
    )
    context.add_init_script(f"localStorage.setItem('z-finance.locale', '{lang}')")
    return context
