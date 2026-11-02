"""A non-loopback ``--host`` needs ``--allow-network`` in the launcher too (T-2026-CU-9130-044).

Sibling of ``tests/test_cli_host.py``: the launcher (``packaging/zfinance_launcher.py``) parses its
own ``--host`` and must apply the identical refusal, reusing ``zfinance.cli.is_loopback`` rather
than re-implementing it. The launcher writes to a log file instead of a console (it is windowed),
so the "warning is logged" assertions read that file instead of ``caplog``.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

import zfinance_launcher as launcher
from zfinance.config import ENV_DATA_DIR

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 — the exposed bind address is what these tests are about
NON_LOOPBACK_HOSTS = (ALL_INTERFACES, "::", "192.168.1.61", "example.lan")


@pytest.mark.parametrize("host", NON_LOOPBACK_HOSTS)
def test_non_loopback_host_is_refused_without_the_opt_in(host: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-044 AK1: refused, exit != 0, message names the risk."""
    with pytest.raises(SystemExit) as exc:
        launcher.parse_args(["--host", host])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--allow-network" in err
    assert "no login" in err
    assert "read and change your data" in err


@pytest.mark.parametrize("host", NON_LOOPBACK_HOSTS)
def test_non_loopback_host_is_accepted_with_the_opt_in(host: str) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-044 AK2: a second explicit flag lifts the refusal."""
    args = launcher.parse_args(["--host", host, "--allow-network"])
    assert (args.host, args.allow_network) == (host, True)


@pytest.mark.parametrize("host", ("127.0.0.1", "localhost", "LOCALHOST", "::1", "127.0.0.2"))
def test_loopback_hosts_need_no_opt_in(host: str) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-044 AK3: the default Start-menu launch is unaffected."""
    assert launcher.parse_args(["--host", host]).host == host


def test_default_start_has_no_host_argument_and_is_loopback() -> None:
    """Oracle: spec-anchored — SPEC AK3: no arguments at all (the real Start-menu shortcut) stays loopback."""
    args = launcher.parse_args([])
    assert launcher.is_loopback(args.host) is True
    assert args.allow_network is False


def test_opted_in_start_logs_the_risk_to_the_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — SPEC AK2: the opted-in start writes the risk to the runtime log file.

    The installed program is windowed and prints nowhere (see ``configure_logging``), so "a warning
    is logged" means the log file, not a console. A pre-acquired instance lock makes ``main`` take
    the "already running" short-circuit right after logging the warning, so no real server starts.
    """
    data_dir = Path(str(tmp_path)).resolve()
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    launcher.write_state(data_dir, launcher.RuntimeState(url="http://127.0.0.1:8771", port=8771, pid=99))
    held = launcher.InstanceLock(launcher.runtime_dir(data_dir) / launcher.LOCK_FILENAME)
    assert held.acquire() is True
    try:
        exit_code = launcher.main(
            ["--host", ALL_INTERFACES, "--allow-network", "--no-tray", "--no-browser"], open_url=lambda _u: None
        )
    finally:
        held.release()
    assert exit_code == 0
    log_text = (launcher.runtime_dir(data_dir) / launcher.LOG_FILENAME).read_text(encoding="utf-8")
    assert "no login" in log_text
    assert ALL_INTERFACES in log_text


def test_loopback_start_does_not_warn_in_the_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — SPEC AK3: the default (loopback) launch logs no network-risk warning."""
    data_dir = Path(str(tmp_path)).resolve()
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    launcher.write_state(data_dir, launcher.RuntimeState(url="http://127.0.0.1:8771", port=8771, pid=99))
    held = launcher.InstanceLock(launcher.runtime_dir(data_dir) / launcher.LOCK_FILENAME)
    assert held.acquire() is True
    try:
        exit_code = launcher.main(["--no-tray", "--no-browser"], open_url=lambda _u: None)
    finally:
        held.release()
    assert exit_code == 0
    log_text = (launcher.runtime_dir(data_dir) / launcher.LOG_FILENAME).read_text(encoding="utf-8")
    assert "no login" not in log_text


class _RecordingLauncher:
    """Stands in for ``Launcher``: remembers the URL it was given and never starts a server."""

    seen_urls: ClassVar[list[str]] = []

    def __init__(self, settings: object, url: str, *, open_url: object = None) -> None:
        type(self).seen_urls.append(url)

    def start(self) -> None:
        return None

    def open_browser_when_ready(self) -> None:
        return None

    def quit(self) -> None:
        return None

    def join(self) -> None:
        return None


class _NoMutex:
    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None


@pytest.mark.parametrize(
    ("host", "expected"),
    (
        ("::1", "http://[::1]:8771"),
        (ALL_INTERFACES, "http://127.0.0.1:8771"),
        ("::", "http://[::1]:8771"),
        ("192.168.1.61", "http://192.168.1.61:8771"),
        ("example.lan", "http://example.lan:8771"),
    ),
)
def test_launcher_opens_and_records_a_valid_url_for_any_bind_address(
    host: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-HAR-9130-020: same URL table as ``cli.browser_url`` (test_cli_host).

    The URL handed to the browser and written to ``launcher.json`` (read by the receipt agent) is one value.
    """
    data_dir = Path(str(tmp_path)).resolve()
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    _RecordingLauncher.seen_urls = []
    written: list[str] = []
    real_write_state = launcher.write_state
    monkeypatch.setattr(launcher, "Launcher", _RecordingLauncher)
    monkeypatch.setattr(launcher, "AppMutex", _NoMutex)
    monkeypatch.setattr(launcher, "find_free_port", lambda port, host: 8771)

    def recording_write_state(directory: Path, state: launcher.RuntimeState) -> None:
        written.append(state.url)
        real_write_state(directory, state)

    monkeypatch.setattr(launcher, "write_state", recording_write_state)
    exit_code = launcher.main(["--host", host, "--allow-network", "--no-tray", "--no-browser"])
    assert exit_code == 0
    assert _RecordingLauncher.seen_urls == [expected]
    assert written == [expected]
