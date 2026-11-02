"""The Windows launcher (SPEC AK20, UC NF10): port search, single instance, browser, tray menu.

The pieces that decide how the installed program behaves are plain functions, so they are tested
without freezing anything: pystray's event loop and the drawn pixels are not our contract, the menu
*structure* is.
"""

from __future__ import annotations

import logging
import socket
import sys
import time
import types
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

import zfinance_launcher as launcher
from zfinance.config import ENV_DATA_DIR, Settings
from zfinance.domain.backup.service import backups_dir


class FakeServer:
    """Stand-in for ``uvicorn.Server``: the two flags the launcher reads, and a ``run`` that ends."""

    def __init__(self, *, started: bool = True, should_exit: bool = False) -> None:
        self.started = started
        self.should_exit = should_exit
        self.ran = False

    def run(self) -> None:
        self.ran = True


def _make_launcher(settings: Settings, server: FakeServer, opened: list[str]) -> launcher.Launcher:
    return launcher.Launcher(settings, "http://127.0.0.1:8765", open_url=opened.append, server=server)  # type: ignore[arg-type]


# --- free port (AK2) -----------------------------------------------------------------------------


def test_find_free_port_takes_the_first_port_when_it_is_free() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK2: the search starts at the given port."""
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert launcher.find_free_port(free, attempts=2) == free


def test_find_free_port_skips_an_occupied_port() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK2: an occupied port moves the search one up."""
    with closing(socket.socket()) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        occupied = taken.getsockname()[1]
        with closing(socket.socket()) as neighbour:
            try:
                neighbour.bind(("127.0.0.1", occupied + 1))
            except OSError:
                pytest.skip("the port above the occupied one is in use on this machine")
        assert launcher.find_free_port(occupied, attempts=2) == occupied + 1


def test_find_free_port_gives_up_after_the_attempts() -> None:
    """Oracle: spec-anchored — zfinance_launcher.find_free_port contract: ``attempts`` is a bound."""
    with closing(socket.socket()) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        with pytest.raises(launcher.LauncherError):
            launcher.find_free_port(taken.getsockname()[1], attempts=1)


# --- single instance (AK3) -----------------------------------------------------------------------


def test_the_lock_refuses_a_second_holder(tmp_path: Path) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK3: one instance per data directory."""
    path = tmp_path / "launcher.lock"
    first = launcher.InstanceLock(path)
    second = launcher.InstanceLock(path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


def test_first_start_writes_the_runtime_state(tmp_path: Path) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK3: the address is written for the next start."""
    state = launcher.RuntimeState(url="http://127.0.0.1:8770", port=8770, pid=4242)
    launcher.write_state(tmp_path, state)
    assert launcher.read_state(tmp_path) == state
    launcher.clear_state(tmp_path)
    assert launcher.read_state(tmp_path) is None


def test_an_unreadable_runtime_state_counts_as_none(tmp_path: Path) -> None:
    """Oracle: spec-anchored — read_state contract: a broken file is "no instance", never a crash."""
    launcher.runtime_dir(tmp_path).mkdir(parents=True)
    (launcher.runtime_dir(tmp_path) / launcher.STATE_FILENAME).write_text("{ not json", encoding="utf-8")
    assert launcher.read_state(tmp_path) is None


def test_second_start_opens_the_running_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK3: a second start opens the browser and ends."""
    data_dir = Path(str(tmp_path)).resolve()
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    launcher.write_state(data_dir, launcher.RuntimeState(url="http://127.0.0.1:8771", port=8771, pid=99))
    held = launcher.InstanceLock(launcher.runtime_dir(data_dir) / launcher.LOCK_FILENAME)
    assert held.acquire() is True
    opened: list[str] = []
    try:
        exit_code = launcher.main(["--no-tray"], open_url=opened.append)
    finally:
        held.release()
    assert exit_code == 0
    assert opened == ["http://127.0.0.1:8771"]


# --- browser (AK3b) ------------------------------------------------------------------------------


def test_first_start_opens_the_browser_when_the_server_is_ready(settings: Settings) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK3b: the tab opens once the server accepts."""
    opened: list[str] = []
    _make_launcher(settings, FakeServer(started=True), opened).open_browser_when_ready()
    assert opened == ["http://127.0.0.1:8765"]


def test_first_start_does_not_open_a_browser_that_never_starts(settings: Settings) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK3b: "and only then"."""
    opened: list[str] = []
    _make_launcher(settings, FakeServer(started=False, should_exit=True), opened).open_browser_when_ready()
    assert opened == []


# --- tray menu (AK4a, AK4b, AK4c) ----------------------------------------------------------------


def test_tray_menu_has_the_four_entries(settings: Settings) -> None:
    """Oracle: spec-anchored — UC NF10: "bietet Beenden, Sichern, Ordner öffnen" plus opening the tool."""
    instance = _make_launcher(settings, FakeServer(), [])
    assert [label for label, _ in launcher.menu_entries(instance)] == [
        "Open z-finance",
        "Back up now",
        "Open data folder",
        "Quit",
    ]
    assert [label for label, _ in launcher.menu_entries(instance, "de")] == [
        "z-finance öffnen",
        "Jetzt sichern",
        "Datenordner öffnen",
        "Beenden",
    ]


def test_an_unknown_tray_language_falls_back_to_english(settings: Settings) -> None:
    """Oracle: spec-anchored — ARCHITECTURE "Internationalisation": English is the fallback."""
    instance = _make_launcher(settings, FakeServer(), [])
    assert launcher.menu_entries(instance, "fr") == launcher.menu_entries(instance, "en")


def test_the_open_entry_opens_the_running_instance(settings: Settings) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK4a: "Open" shows the running instance."""
    opened: list[str] = []
    entries = dict(launcher.menu_entries(_make_launcher(settings, FakeServer(), opened)))
    entries["Open z-finance"]()
    assert opened == ["http://127.0.0.1:8765"]


def test_back_up_now_writes_a_manual_snapshot(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK4b + ARCHITECTURE "Backup and restore"."""
    assert client.get("/api/health").status_code == 200  # the lifespan created the database
    path = launcher.back_up_now(settings)
    assert path.parent == backups_dir(settings.data_dir)
    assert path.name.endswith("-manual")
    assert (path / "export.json").is_file()


def test_quit_asks_the_server_to_stop(settings: Settings) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK4c: "Quit" ends the server, it does not kill it."""
    server = FakeServer()
    instance = _make_launcher(settings, server, [])
    dict(launcher.menu_entries(instance))["Quit"]()
    assert server.should_exit is True


class FakeIcon:
    """Stand-in for ``pystray.Icon``: keeps the menu, records ``stop``, and runs a hook in ``run``.

    The real ``Icon.run()`` blocks until ``Icon.stop()`` is called. The fake cannot block, so a
    ``run`` hook that leaves the icon unstopped raises instead of hanging the suite.
    """

    instances: ClassVar[list[FakeIcon]] = []
    on_run: ClassVar[Callable[[FakeIcon], None] | None] = None

    def __init__(self, name: str, *, icon: Any, title: str, menu: tuple[Any, ...]) -> None:
        self.name, self.title, self.items = name, title, menu
        self.stopped = False
        FakeIcon.instances.append(self)

    def run(self) -> None:
        hook = FakeIcon.on_run
        if hook is not None:
            hook(self)
            if not self.stopped:
                raise RuntimeError("pystray's Icon.run() would never return: nobody called icon.stop()")

    def stop(self) -> None:
        self.stopped = True

    def click(self, label: str) -> None:
        """Choose a menu entry the way pystray does: callback(icon, item)."""
        item = next(entry for entry in self.items if entry.text == label)
        item.action(self, item)


@dataclass(frozen=True)
class FakeMenuItem:
    text: str
    action: Callable[[Any, Any], None]


@pytest.fixture
def fake_pystray(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeIcon]]:
    """Install a fake ``pystray`` module; the real one is a packaging-only dependency."""
    module = types.ModuleType("pystray")
    module.Icon = FakeIcon  # type: ignore[attr-defined]
    module.Menu = lambda *items: items  # type: ignore[attr-defined]
    module.MenuItem = FakeMenuItem  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pystray", module)
    monkeypatch.setattr(launcher, "tray_icon_image", lambda: None)
    FakeIcon.instances = []
    FakeIcon.on_run = None
    yield FakeIcon
    FakeIcon.instances = []
    FakeIcon.on_run = None


def test_tray_quit_stops_the_icon(settings: Settings, fake_pystray: type[FakeIcon]) -> None:
    """Oracle: spec-anchored — T-2026-HAR-9130-022 acceptance (A): after "Quit" the icon is gone.

    pystray's ``Icon.run()`` returns only after ``Icon.stop()``; without it main() never ends.
    """
    server = FakeServer()
    assert launcher.run_tray(_make_launcher(settings, server, [])) is True
    (icon,) = fake_pystray.instances
    icon.click("Quit")
    assert server.should_exit is True
    assert icon.stopped is True


def test_the_other_tray_entries_keep_the_icon(
    settings: Settings, fake_pystray: type[FakeIcon], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oracle: spec-anchored — UC NF10: open, back up and open folder leave z-finance running."""
    shown: list[Path] = []
    monkeypatch.setattr(launcher, "open_data_folder", shown.append)
    monkeypatch.setattr(launcher, "back_up_now", lambda s: s.data_dir)
    opened: list[str] = []
    server = FakeServer()
    launcher.run_tray(_make_launcher(settings, server, opened))
    (icon,) = fake_pystray.instances
    for label in ("Open z-finance", "Back up now", "Open data folder"):
        icon.click(label)
    assert icon.stopped is False
    assert server.should_exit is False
    assert opened == ["http://127.0.0.1:8765"]
    assert shown == [settings.data_dir]


def _health_once(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as response:  # noqa: S310 # nosec B310
            return bool(response.status == 200)
    except OSError:
        return False


def _serve_then_quit(data_dir: Path, served: list[str]) -> Callable[[FakeIcon], None]:
    """A tray ``run`` hook: wait until the started instance answers, then choose "Quit"."""

    def hook(icon: FakeIcon) -> None:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            state = launcher.read_state(data_dir)
            if state is not None and _health_once(state.url):
                served.append(state.url)
                break
            time.sleep(0.1)
        icon.click("Quit")

    return hook


def test_quit_ends_main_and_a_new_start_serves_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_pystray: type[FakeIcon], restored_root_logging: None
) -> None:
    """Oracle: spec-anchored — T-2026-HAR-9130-022 acceptance (A): after "Quit" the process ends,
    launcher.json is removed, and a new start serves the app again."""
    data_dir = (tmp_path / "data").resolve()
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    served: list[str] = []
    fake_pystray.on_run = _serve_then_quit(data_dir, served)
    for _ in range(2):
        assert launcher.main(["--no-browser", "--port", str(port)], open_url=lambda _url: None) == 0
        assert launcher.read_state(data_dir) is None
    assert len(served) == 2
    assert [icon.stopped for icon in fake_pystray.instances] == [True, True]
    lock = launcher.InstanceLock(launcher.runtime_dir(data_dir) / launcher.LOCK_FILENAME)
    assert lock.acquire() is True
    lock.release()


# --- installer mutex (T-2026-HAR-9130-022 B) -----------------------------------------------------


def test_main_holds_the_installer_mutex_while_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_pystray: type[FakeIcon], restored_root_logging: None
) -> None:
    """Oracle: spec-anchored — T-2026-HAR-9130-022 (B): the mutex exists while z-finance runs and
    is closed when it ends. No Windows handle: the two calls are replaced by fakes."""
    monkeypatch.setenv(ENV_DATA_DIR, str((tmp_path / "data").resolve()))
    events: list[str] = []

    def create(name: str) -> int:
        events.append(f"create {name}")
        return 7

    monkeypatch.setattr(launcher, "_create_mutex", create)
    monkeypatch.setattr(launcher, "_close_handle", lambda handle: events.append(f"close {handle}"))

    def run_then_quit(icon: FakeIcon) -> None:
        events.append("running")
        icon.click("Quit")

    fake_pystray.on_run = run_then_quit
    assert launcher.main(["--no-browser"], open_url=lambda _url: None) == 0
    assert events == [f"create {launcher.APP_MUTEX_NAME}", "running", "close 7"]


def test_the_mutex_is_a_no_op_when_windows_refuses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — T-2026-HAR-9130-022 (B): no mutex must never stop a start."""
    monkeypatch.setattr(launcher, "_create_mutex", lambda _name: None)
    closed: list[int] = []
    monkeypatch.setattr(launcher, "_close_handle", closed.append)
    mutex = launcher.AppMutex()
    assert mutex.acquire() is False
    mutex.release()
    assert closed == []


@pytest.mark.skipif(sys.platform != "win32", reason="a named mutex exists only on Windows")
def test_the_windows_mutex_is_visible_until_it_is_released() -> None:
    """Oracle: spec-anchored — Win32 OpenMutexW finds a named mutex while a handle is open, which
    is what Inno Setup's AppMutex check does."""
    import ctypes

    # WinDLL exists only on Windows, and mypy checks this file on Linux too (the skipif marker does
    # not narrow the platform for it), so the module is used through an Any-typed name here.
    win_ctypes: Any = ctypes
    kernel32 = win_ctypes.WinDLL("kernel32", use_last_error=True)
    synchronize = 0x00100000
    name = f"{launcher.APP_MUTEX_NAME}-test-{time.monotonic_ns()}"

    def is_open() -> bool:
        handle = kernel32.OpenMutexW(synchronize, False, name)
        if handle:
            kernel32.CloseHandle(handle)
        return bool(handle)

    mutex = launcher.AppMutex(name)
    assert is_open() is False
    assert mutex.acquire() is True
    assert is_open() is True
    mutex.release()
    assert is_open() is False


def test_tray_language_falls_back_without_a_database(tmp_path: Path) -> None:
    """Oracle: spec-anchored — tray_language contract: a first run has no database yet, and that is fine."""
    assert launcher.tray_language(Settings(data_dir=tmp_path / "missing")) == "en"


# --- no console (AK20) ---------------------------------------------------------------------------


@pytest.fixture
def restored_root_logging() -> Iterator[None]:
    """Give back the root logger the tests found: configure_logging() reconfigures it globally."""
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        for handler in before:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(level)


def test_the_server_is_built_without_a_console(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — SPEC AK20: the installed program is windowed, so it has no stdout.

    PyInstaller's ``console=False`` leaves ``sys.stdout`` and ``sys.stderr`` at ``None``. The
    console formatter uvicorn installs by default asks ``sys.stdout.isatty()``, which is why a
    start that works from a developer console died on the installed one.
    """
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    launcher.Launcher(settings, "http://127.0.0.1:8765")  # a real uvicorn.Config, not the FakeServer


def test_logging_without_a_console_goes_to_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_root_logging: None
) -> None:
    """Oracle: spec-anchored — SPEC AK20: a windowed program prints nowhere, so it writes a log."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    path = launcher.configure_logging(tmp_path)
    logging.getLogger("zfinance_launcher").info("the launcher says hello")
    assert path == launcher.runtime_dir(tmp_path) / launcher.LOG_FILENAME
    assert "the launcher says hello" in path.read_text(encoding="utf-8")


def test_a_missing_console_stream_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_root_logging: None
) -> None:
    """Oracle: spec-anchored — SPEC AK20: nothing the program calls may trip over a None stream."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    launcher.configure_logging(tmp_path)
    assert sys.stdout is not None
    assert sys.stderr is not None
    sys.stdout.write("this must not raise")


def test_a_console_still_gets_the_log(tmp_path: Path, restored_root_logging: None) -> None:
    """Oracle: spec-anchored — the developer start keeps the console output it always had."""
    launcher.configure_logging(tmp_path)
    streams = [h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler]
    assert streams, "a start with a console still prints"
