"""A non-loopback ``--host`` needs ``--allow-network``: the Host guard is no login (T-2026-CU-9130-036)."""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from zfinance import cli
from zfinance.app import create_app
from zfinance.config import Settings

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 — the exposed bind address is what these tests are about
NON_LOOPBACK_HOSTS = (ALL_INTERFACES, "::", "192.168.1.61", "example.lan")


@pytest.mark.parametrize("host", NON_LOOPBACK_HOSTS)
def test_non_loopback_host_is_refused_without_the_opt_in(host: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Oracle: spec-anchored — task decision (owner, 2026-09-21): refused, exit != 0, the message names the risk."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["--host", host])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--allow-network" in err
    assert "no login" in err
    assert "read and change your data" in err


@pytest.mark.parametrize("host", NON_LOOPBACK_HOSTS)
def test_non_loopback_host_is_accepted_with_the_opt_in(host: str) -> None:
    """Oracle: spec-anchored — task decision: a second explicit flag lifts the refusal."""
    args = cli.parse_args(["--host", host, "--allow-network"])
    assert (args.host, args.allow_network) == (host, True)


@pytest.mark.parametrize("host", ("127.0.0.1", "localhost", "LOCALHOST", "::1", "127.0.0.2"))
def test_loopback_hosts_need_no_opt_in(host: str) -> None:
    """Oracle: spec-anchored — task "Laien-Pfad bleibt unberuehrt"; loopback = 127.0.0.0/8, ::1, localhost."""
    assert cli.parse_args(["--host", host]).host == host


def test_opted_in_start_logs_the_risk(caplog: pytest.LogCaptureFixture) -> None:
    """Oracle: spec-anchored — SPEC AK2: the opted-in start says the network can reach the data."""
    with patch.object(cli, "uvicorn") as fake_uvicorn, caplog.at_level(logging.WARNING, logger=cli.__name__):
        cli.main(["--host", ALL_INTERFACES, "--allow-network", "--no-browser"])
    fake_uvicorn.Server.return_value.run.assert_called_once()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("no login" in message and ALL_INTERFACES in message for message in warnings)


def test_forged_host_passes_the_guard_when_bound_to_all_interfaces(settings: Settings) -> None:
    """Oracle: spec-anchored — task measurement of 2026-09-20 (403 via the LAN IP, 200 with ``Host: 127.0.0.1``).

    Pins why ``--allow-network`` exists: the Host check stops a browser (it cannot set ``Host``),
    not a client on the same network.
    """
    app = create_app(replace(settings, host=ALL_INTERFACES))
    with TestClient(app, base_url="http://192.168.1.61:8814") as lan:
        for path in ("/api/health", "/api/accounts"):
            assert lan.get(path).status_code == 403
            assert lan.get(path, headers={"Host": "127.0.0.1"}).status_code == 200


@pytest.mark.parametrize(
    ("host", "expected"),
    (
        ("127.0.0.1", "http://127.0.0.1:8765"),
        ("localhost", "http://localhost:8765"),
        ("::1", "http://[::1]:8765"),
        (ALL_INTERFACES, "http://127.0.0.1:8765"),
        ("::", "http://[::1]:8765"),
        ("192.168.1.61", "http://192.168.1.61:8765"),
        ("example.lan", "http://example.lan:8765"),
    ),
)
def test_browser_url_is_valid_for_every_bind_address(host: str, expected: str) -> None:
    """Oracle: spec-anchored — task trigger (b): ``::1`` needs brackets, a wildcard bind opens loopback."""
    assert cli.browser_url(host, 8765) == expected


@pytest.mark.parametrize("host", (ALL_INTERFACES, "::"))
def test_wildcard_start_names_the_usable_address(host: str, caplog: pytest.LogCaptureFixture) -> None:
    """Oracle: spec-anchored — task decision: a wildcard bind logs which address works; the guard stays narrow."""
    with patch.object(cli, "uvicorn"), caplog.at_level(logging.WARNING, logger=cli.__name__):
        cli.main(["--host", host, "--allow-network", "--no-browser"])
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("403" in m and cli.browser_url(host, 8765) in m for m in warnings)


def test_specific_lan_bind_logs_no_wildcard_hint(caplog: pytest.LogCaptureFixture) -> None:
    """Oracle: spec-anchored — the hint is only for a wildcard bind."""
    with patch.object(cli, "uvicorn"), caplog.at_level(logging.WARNING, logger=cli.__name__):
        cli.main(["--host", "192.168.1.61", "--allow-network", "--no-browser"])
    assert not any("403" in r.getMessage() for r in caplog.records)


def test_opened_browser_url_is_the_normalised_one() -> None:
    """Oracle: spec-anchored — task trigger (b): the tab opens on a URL a browser accepts."""
    with patch.object(cli, "uvicorn") as fake_uvicorn, patch.object(cli.threading, "Thread") as fake_thread:
        cli.main(["--host", "::", "--allow-network", "--port", "9001"])
    assert fake_thread.call_args.kwargs["args"][1] == "http://[::1]:9001"
    fake_uvicorn.Server.return_value.run.assert_called_once()
