"""``z-finance`` command: start the local server and open the browser.

uv run z-finance                 # 127.0.0.1:8765, opens the browser
uv run z-finance --no-browser    # same, without the browser (also: ZFINANCE_NO_BROWSER=1)
uv run z-finance --reload        # developer mode: restart on code changes (used by `just dev`)

z-finance has no login; the loopback interface is its only boundary. A non-loopback ``--host``
is therefore refused unless ``--allow-network`` is passed as well (T-2026-CU-9130-036).
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import threading
import time
import webbrowser
from collections.abc import Mapping, Sequence

import uvicorn

from zfinance.app import create_app
from zfinance.config import DEFAULT_HOST, DEFAULT_PORT, Settings, browser_disabled
from zfinance.local_guard import LOOPBACK_HOSTS

logger = logging.getLogger(__name__)

_BROWSER_WAIT_SECONDS = 15.0
_NETWORK_RISK = "z-finance has no login; anyone on this network can read and change your data"
_WILDCARD_V4 = "0.0.0.0"  # noqa: S104  # nosec B104 — compared against, never bound by this module
_WILDCARD_V6 = "::"


def is_loopback(host: str) -> bool:
    """True for a loopback name (``localhost``) or IP literal (127.0.0.0/8, ``::1``)."""
    if host.lower() in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def browser_url(host: str, port: int) -> str:
    """URL for a browser on this machine: a wildcard bind opens loopback, an IPv6 literal gets brackets."""
    target = {_WILDCARD_V4: "127.0.0.1", _WILDCARD_V6: "[::1]"}.get(host, host)
    if ":" in target and not target.startswith("["):
        target = f"[{target}]"
    return f"http://{target}:{port}"


def is_wildcard(host: str) -> bool:
    """True when ``host`` binds every interface (``0.0.0.0`` or ``::``)."""
    return host in (_WILDCARD_V4, _WILDCARD_V6)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line; a non-loopback ``--host`` without ``--allow-network`` exits with code 2."""
    parser = argparse.ArgumentParser(prog="z-finance", description="Start the local z-finance server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument("--reload", action="store_true", help="developer mode: reload on code changes")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="accept a non-loopback --host; there is no login, the network can then read and change your data",
    )
    args = parser.parse_args(argv)
    if not args.allow_network and not is_loopback(args.host):
        parser.error(
            f"--host {args.host} makes z-finance reachable from the network. {_NETWORK_RISK}. "
            "Add --allow-network only if you accept that."
        )
    return args


def should_open_browser(args: argparse.Namespace, environ: Mapping[str, str] | None = None) -> bool:
    """Open a tab unless ``--no-browser``, ``ZFINANCE_NO_BROWSER`` or ``--reload`` (dev) says otherwise."""
    return not (args.no_browser or args.reload or browser_disabled(environ))


def _open_when_ready(server: uvicorn.Server, url: str) -> None:
    """Open ``url`` once uvicorn reports it is accepting connections (gives up after a timeout)."""
    deadline = time.monotonic() + _BROWSER_WAIT_SECONDS
    while not server.started:
        if server.should_exit or time.monotonic() > deadline:
            return
        time.sleep(0.1)
    webbrowser.open(url)


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point of the ``z-finance`` console script."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env(host=args.host, port=args.port)
    url = browser_url(args.host, args.port)
    logger.info("data directory: %s", settings.data_dir)
    if not is_loopback(args.host):
        logger.warning("listening on %s with --allow-network: %s", args.host, _NETWORK_RISK)
    if is_wildcard(args.host):
        logger.warning(
            "--host %s: z-finance only answers requests whose address bar names %s, localhost or the loopback "
            "address; a LAN address gets 403. Use this machine at %s, or bind to one LAN address "
            "(--host <that address>) and open exactly that address from the other machine",
            args.host,
            args.host,
            url,
        )

    if args.reload:
        # Reload needs an import string; the factory re-reads the environment in the worker process.
        uvicorn.run("zfinance.app:create_app", factory=True, host=args.host, port=args.port, reload=True)
        return

    server = uvicorn.Server(uvicorn.Config(create_app(settings), host=args.host, port=args.port))
    if should_open_browser(args):
        threading.Thread(target=_open_when_ready, args=(server, url), daemon=True).start()
    logger.info("z-finance running on %s (Ctrl+C to stop)", url)
    server.run()


if __name__ == "__main__":
    main()
