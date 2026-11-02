"""Windows launcher: the program the installed shortcut starts (UC NF10).

``z-finance`` (backend/zfinance/cli.py) is the developer entry point: it binds a fixed port and
prints to a console. The installed program has different duties, and this module is what the
Start-menu shortcut runs:

- take the first free port from 8765 upwards, so a second program on 8765 does not break the start;
- keep one instance: a second double-click opens the browser at the running instance instead of
  starting a second server;
- put an icon into the notification area with the four entries UC NF10 asks for — open, back up,
  open the data folder, quit;
- open the browser as soon as the server accepts connections.

Everything the app stores stays below the per-user data directory (UC NF2); this module adds only
``<data dir>/runtime/`` with the lock file, the address of the running instance and the log (the
installed program is windowed, so there is no console for it to print to).

Like ``cli.py``, this launcher has no login; the loopback interface is its only boundary. A
non-loopback ``--host`` is therefore refused unless ``--allow-network`` is passed as well
(T-2026-CU-9130-044, sibling of T-2026-CU-9130-036): ``is_loopback`` and the risk message are
reused from ``zfinance.cli``, not re-implemented.

Invariants:
- At most one instance per data directory holds the lock; every other start only opens a browser.
- The lock is released when the process ends, also on a crash (the operating system owns it).
- No outbound network connection: the server binds the loopback address, nothing else is contacted.
- "Quit" stops the tray icon, so main() returns and removes the runtime state before the process ends.
- On Windows the running instance holds the mutex ``APP_MUTEX_NAME`` that the installer checks.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import socket
import subprocess  # nosec B404 - the tray opens the data folder through the file manager
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import uvicorn

from zfinance import __version__
from zfinance.app import create_app
from zfinance.app_settings import get_setting
from zfinance.cli import _NETWORK_RISK, browser_url, is_loopback
from zfinance.config import DEFAULT_HOST, DEFAULT_PORT, Settings
from zfinance.db import connect
from zfinance.domain.backup.service import backups_dir, create_snapshot
from zfinance.i18n import DEFAULT_LANGUAGE, LANGUAGE_SETTING_KEY

logger = logging.getLogger(__name__)

#: How many ports above the default are tried before giving up.
PORT_ATTEMPTS = 20
#: Where the lock file and the address of the running instance live, below the data directory.
RUNTIME_DIRNAME = "runtime"
LOCK_FILENAME = "launcher.lock"
STATE_FILENAME = "launcher.json"
LOG_FILENAME = "z-finance.log"
#: The installed program is windowed and prints nowhere, so the log is a file. One megabyte plus
#: one rolled-over copy is enough to answer "what happened at the last start?" and never grows.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 1
#: How long the browser thread waits for the server to accept connections before giving up.
SERVER_WAIT_SECONDS = 20.0
_POLL_SECONDS = 0.1

#: Tray labels as data, not as a gettext catalog: the tray is drawn outside any request, and the
#: backend catalogs are loaded per request language. Same approach as the default category sets
#: (domain/accounts/service.py). The keys are the four entries UC NF10 names.
TRAY_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "open": "Open z-finance",
        "backup": "Back up now",
        "folder": "Open data folder",
        "quit": "Quit",
    },
    "de": {
        "open": "z-finance öffnen",
        "backup": "Jetzt sichern",
        "folder": "Datenordner öffnen",
        "quit": "Beenden",
    },
}
_MENU_ORDER = ("open", "backup", "folder", "quit")
#: The entries after which the tray icon stops, so that ``icon.run()`` returns and main() ends.
_STOPS_ICON = frozenset({"quit"})

#: Named mutex the running instance holds on Windows. packaging/z-finance.iss names the same
#: string as ``AppMutex``, so Setup sees a running z-finance before it copies a file and asks the
#: user to quit it, instead of failing on files in use. The suffix is the installer's AppId.
APP_MUTEX_NAME = "z-finance-8E2C5A17-9F43-4B6E-A0D8-6C1F2B7E4A95"


class LauncherError(Exception):
    """The launcher cannot start (no free port, unusable data directory)."""


@dataclass(frozen=True)
class RuntimeState:
    """Where the running instance listens, written for the next double-click to find."""

    url: str
    port: int
    pid: int


def runtime_dir(data_dir: Path) -> Path:
    """Folder below the data directory that holds the lock file and the runtime state."""
    return data_dir / RUNTIME_DIRNAME


def replace_missing_streams() -> None:
    """Give ``sys.stdout`` and ``sys.stderr`` a sink when the build has no console.

    PyInstaller's ``console=False`` — what makes this a program a lay user double-clicks rather
    than a black window — leaves both at ``None``. Anything that so much as asks them whether they
    are a terminal then raises, far from where the start actually went wrong.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def configure_logging(data_dir: Path) -> Path:
    """Send the log to a file below the data directory, and to the console when there is one.

    Returns the path of the log file. It lives beside the lock and the runtime state, so the data
    directory keeps its one runtime folder and a support question has one place to look.
    """
    console = sys.stderr is not None
    replace_missing_streams()
    directory = runtime_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_FILENAME
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
    ]
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers, force=True)
    return path


def find_free_port(start: int = DEFAULT_PORT, attempts: int = PORT_ATTEMPTS, host: str = DEFAULT_HOST) -> int:
    """Return the first port from ``start`` upwards that can be bound on ``host`` right now.

    Deliberately without ``SO_REUSEADDR``: the question is whether another program already listens
    there, and a reusable bind would answer it wrongly.
    """
    for port in range(start, start + attempts):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    raise LauncherError(f"no free port between {start} and {start + attempts - 1}")


def write_state(data_dir: Path, state: RuntimeState) -> None:
    """Remember where this instance listens."""
    directory = runtime_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / STATE_FILENAME).write_text(
        json.dumps({"url": state.url, "port": state.port, "pid": state.pid}), encoding="utf-8"
    )


def read_state(data_dir: Path) -> RuntimeState | None:
    """The address of the running instance, or None when there is none or the file is unreadable."""
    path = runtime_dir(data_dir) / STATE_FILENAME
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("url"), str):
        return None
    return RuntimeState(url=data["url"], port=int(data.get("port", 0)), pid=int(data.get("pid", 0)))


def clear_state(data_dir: Path) -> None:
    """Forget the address; the instance is gone."""
    (runtime_dir(data_dir) / STATE_FILENAME).unlink(missing_ok=True)


def _lock(handle: IO[str]) -> None:
    """Take an exclusive lock on the first byte of ``handle``, or raise ``OSError``."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO[str]) -> None:
    """Release the lock taken by :func:`_lock`; a failure here is not worth an exception."""
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:  # pragma: no cover - the file is closed right after
        logger.debug("could not release the instance lock")


class InstanceLock:
    """One lock file per data directory; whoever holds it is *the* running instance.

    An operating-system lock, not a pid file: it disappears with the process, so a crash does not
    leave a stale lock that blocks the next start.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> bool:
        """Take the lock. False when another instance holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600), "r+", encoding="utf-8")
        try:
            _lock(handle)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """Release the lock and close the file. Safe to call when it was never taken."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        _unlock(handle)
        handle.close()

    def __enter__(self) -> InstanceLock:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


def _create_mutex(name: str) -> int | None:
    """Create (or open) the named Windows mutex and return its handle; None elsewhere or on failure."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateMutexW
    create.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    handle = create(None, False, name)
    if not handle:
        logger.warning("could not create the mutex %s (error %d)", name, ctypes.get_last_error())
        return None
    return int(handle)


def _close_handle(handle: int) -> None:
    """Close a handle from :func:`_create_mutex`."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    close(handle)


class AppMutex:
    """The named mutex Inno Setup's ``AppMutex`` looks for; a no-op outside Windows.

    The instance lock above already keeps one instance; this handle only tells Setup that the
    program runs. Windows closes it with the process, so a crash leaves nothing behind either.
    """

    def __init__(self, name: str = APP_MUTEX_NAME) -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        """Create the mutex. False when there is none (not Windows, or Windows refused it)."""
        self._handle = _create_mutex(self.name)
        return self._handle is not None

    def release(self) -> None:
        """Close the handle. Safe to call when it was never created."""
        handle, self._handle = self._handle, None
        if handle is not None:
            _close_handle(handle)


def tray_language(settings: Settings) -> str:
    """The language the tray labels use: the stored UI language, English when there is none.

    Read once at start; the database may not exist yet on a first run, which is not an error.
    """
    try:
        with closing(connect(settings.db_path)) as conn:
            stored = get_setting(conn, LANGUAGE_SETTING_KEY, None)
    except Exception:
        return DEFAULT_LANGUAGE
    return str(stored) if stored in TRAY_LABELS else DEFAULT_LANGUAGE


def back_up_now(settings: Settings) -> Path:
    """Write a manual snapshot, exactly like the Backup page does, and return its folder."""
    with closing(connect(settings.db_path)) as conn:
        snapshot = create_snapshot(conn, backups_dir(settings.data_dir), "manual", data_dir=settings.data_dir)
    return snapshot.path


def open_data_folder(data_dir: Path) -> None:
    """Show the data directory in the system's file manager."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(data_dir)  # noqa: S606 # nosec B606 - a directory path, not a command line
    elif sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", str(data_dir)], check=False)  # noqa: S603 # nosec B603
    else:
        subprocess.run(["/usr/bin/xdg-open", str(data_dir)], check=False)  # noqa: S603 # nosec B603


class Launcher:
    """The running instance: a uvicorn server in a thread plus the actions the tray offers."""

    def __init__(
        self,
        settings: Settings,
        url: str,
        *,
        open_url: Callable[[str], Any] = webbrowser.open,
        server: uvicorn.Server | None = None,
    ) -> None:
        self.settings = settings
        self.url = url
        self._open_url = open_url
        self._server = server or uvicorn.Server(
            uvicorn.Config(
                create_app(settings),
                host=settings.host,
                port=settings.port,
                log_level="info",
                # One line per request would fill the rotating log with the SPA's polling and push
                # out the start of the program, which is the thing the log exists to explain. A
                # single-user tool on the loopback address has no use for an access log.
                access_log=False,
                # Not uvicorn's console logging: its formatter asks sys.stdout whether it is a
                # terminal, and a windowed build has no sys.stdout. With no config of its own,
                # uvicorn leaves its loggers propagating to the root that configure_logging() set,
                # so its lines land in the log file like everybody else's.
                log_config=None,
            )
        )
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Run the server in a background thread (the tray needs the main thread)."""
        thread = threading.Thread(target=self._server.run, name="z-finance-server")
        thread.start()
        self._thread = thread

    def wait_until_ready(self, timeout: float = SERVER_WAIT_SECONDS) -> bool:
        """Block until the server accepts connections; False on timeout or an early exit."""
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if self._server.should_exit or time.monotonic() > deadline:
                return False
            time.sleep(_POLL_SECONDS)
        return True

    def open_in_browser(self) -> None:
        """Tray entry "Open": show the running instance."""
        self._open_url(self.url)

    def open_browser_when_ready(self) -> None:
        """Open the browser once the server is up; give up silently if it never comes up."""
        if self.wait_until_ready():
            self.open_in_browser()

    def back_up(self) -> None:
        """Tray entry "Back up now". A failure is logged, never raised into the tray loop."""
        try:
            path = back_up_now(self.settings)
        except Exception:
            logger.exception("manual backup from the tray failed")
        else:
            logger.info("backup written to %s", path)

    def show_data_folder(self) -> None:
        """Tray entry "Open data folder"."""
        open_data_folder(self.settings.data_dir)

    def quit(self) -> None:
        """Tray entry "Quit": ask the server to finish the current requests and stop."""
        self._server.should_exit = True

    def join(self) -> None:
        """Wait for the server thread to end."""
        if self._thread is not None:
            self._thread.join()


def menu_entries(launcher: Launcher, language: str = DEFAULT_LANGUAGE) -> tuple[tuple[str, Callable[[], None]], ...]:
    """The tray menu as plain data: the four entries of UC NF10, in order, in ``language``."""
    labels = TRAY_LABELS.get(language, TRAY_LABELS[DEFAULT_LANGUAGE])
    actions: dict[str, Callable[[], None]] = {
        "open": launcher.open_in_browser,
        "backup": launcher.back_up,
        "folder": launcher.show_data_folder,
        "quit": launcher.quit,
    }
    return tuple((labels[key], actions[key]) for key in _MENU_ORDER)


def tray_icon_image(size: int = 64) -> Any:
    """The notification-area icon, drawn at build time from code so no binary lives in the repo."""
    from PIL import Image, ImageDraw  # type: ignore[import-not-found]

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    edge = size // 8
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=(31, 34, 41, 255))
    draw.line(
        [(edge * 2, edge * 2.5), (size - edge * 2, edge * 2.5), (edge * 2, size - edge * 2.5)],
        fill=(212, 175, 105, 255),
        width=max(2, size // 12),
        joint="curve",
    )
    draw.line(
        [(edge * 2, size - edge * 2.5), (size - edge * 2, size - edge * 2.5)],
        fill=(212, 175, 105, 255),
        width=max(2, size // 12),
    )
    return image


def run_tray(launcher: Launcher, language: str = DEFAULT_LANGUAGE) -> bool:
    """Show the tray icon and block until "Quit". False when pystray is unavailable.

    Without a tray the program still works; it then just runs until the window is closed, which is
    why a missing pystray is a logged fallback and not an error.
    """
    try:
        import pystray  # type: ignore[import-not-found]
    except Exception:
        logger.warning("no notification-area icon available; z-finance runs without it")
        return False

    def handler(action: Callable[[], None], stops_icon: bool) -> Callable[[Any, Any], None]:
        """pystray calls a menu callback with the icon and the item; "Quit" also stops the icon.

        Which entry stops the icon is data (``_STOPS_ICON``), not a comparison of callables: a
        bound method is a new object on every attribute access, so ``action is launcher.quit``
        was never true and ``icon.run()`` never returned.
        """

        def run(icon: Any, _item: Any) -> None:
            action()
            if stops_icon:
                icon.stop()

        return run

    entries = menu_entries(launcher, language)
    items = (
        pystray.MenuItem(label, handler(action, key in _STOPS_ICON))
        for key, (label, action) in zip(_MENU_ORDER, entries, strict=True)
    )
    icon = pystray.Icon(
        "z-finance", icon=tray_icon_image(), title=f"z-finance {__version__}", menu=pystray.Menu(*items)
    )
    icon.run()
    return True


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the launcher's command line; a non-loopback ``--host`` without ``--allow-network`` exits code 2."""
    parser = argparse.ArgumentParser(prog="z-finance-launcher", description="Start z-finance and keep it running.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"first port tried (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument("--no-tray", action="store_true", help="do not show the notification-area icon")
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


def main(argv: Sequence[str] | None = None, *, open_url: Callable[[str], Any] = webbrowser.open) -> int:
    """Start z-finance, or hand over to the instance that already runs. Returns the exit code."""
    args = parse_args(argv)
    base = Settings.from_env()
    data_dir = base.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = configure_logging(data_dir)
    logger.info("z-finance %s starting (log: %s)", __version__, log_path)
    if not is_loopback(args.host):
        logger.warning("listening on %s with --allow-network: %s", args.host, _NETWORK_RISK)

    lock = InstanceLock(runtime_dir(data_dir) / LOCK_FILENAME)
    if not lock.acquire():
        running = read_state(data_dir)
        if running is None:
            logger.error("z-finance is already running, but its address is unknown")
            return 1
        logger.info("z-finance already runs on %s; opening it", running.url)
        if not args.no_browser:
            open_url(running.url)
        return 0

    mutex = AppMutex()
    mutex.acquire()
    try:
        port = find_free_port(args.port, host=args.host)
        settings = Settings.from_env(host=args.host, port=port)
        url = browser_url(args.host, port)
        write_state(data_dir, RuntimeState(url=url, port=port, pid=os.getpid()))
        logger.info("z-finance running on %s (data: %s)", url, data_dir)
        launcher = Launcher(settings, url, open_url=open_url)
        launcher.start()
        if not args.no_browser:
            threading.Thread(target=launcher.open_browser_when_ready, daemon=True).start()
        if not args.no_tray:
            run_tray(launcher, tray_language(settings))
            launcher.quit()
        launcher.join()
        return 0
    finally:
        clear_state(data_dir)
        mutex.release()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
