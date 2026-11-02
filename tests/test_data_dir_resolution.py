"""AK2 (SPEC AK10, UC NF2): where the data lives, and the browser switch of the CLI (AK5b)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs
import pytest
from fastapi.testclient import TestClient

from zfinance.app import create_app
from zfinance.cli import parse_args, should_open_browser
from zfinance.config import DB_FILENAME, ENV_DATA_DIR, ENV_NO_BROWSER, Settings, resolve_data_dir


def test_env_override_wins(tmp_path: Path) -> None:
    """Oracle: spec-anchored — SPEC AK10 "per ZFINANCE_DATA_DIR überschreibbar"."""
    assert resolve_data_dir({ENV_DATA_DIR: str(tmp_path / "custom")}) == (tmp_path / "custom").resolve()


def test_blank_override_falls_back_to_default() -> None:
    """Oracle: spec-anchored — config.py contract: a blank ZFINANCE_DATA_DIR counts as unset."""
    assert resolve_data_dir({ENV_DATA_DIR: "  "}) == resolve_data_dir({})


def test_default_is_platformdirs_user_data_dir() -> None:
    """Oracle: spec-anchored — SPEC AK10 ``platformdirs.user_data_dir("z-finance")`` without author folder."""
    assert resolve_data_dir({}) == Path(platformdirs.user_data_dir("z-finance", appauthor=False))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path layout")
def test_windows_default_is_localappdata_z_finance() -> None:
    """Oracle: spec-anchored — UC NF2 literal path ``%LOCALAPPDATA%\\z-finance\\``."""
    assert resolve_data_dir({}) == Path(os.environ["LOCALAPPDATA"]) / "z-finance"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux path layout")
def test_linux_default_is_local_share_z_finance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — UC NF2 literal path ``~/.local/share/z-finance/``."""
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert resolve_data_dir({}) == Path.home() / ".local" / "share" / "z-finance"


def test_startup_creates_dir_and_database(tmp_path: Path, web_dir: Path) -> None:
    """Oracle: spec-anchored — SPEC AK10 "Daten in einer SQLite-Datei im Benutzerdaten-Verzeichnis"."""
    data_dir = tmp_path / "nested" / "data"
    settings = Settings.from_env({ENV_DATA_DIR: str(data_dir)}, web_dir=web_dir)
    assert not data_dir.exists()

    with TestClient(create_app(settings)):
        pass

    assert (data_dir / DB_FILENAME).is_file()


@pytest.mark.parametrize(
    ("argv", "environ", "expected"),
    [
        ([], {}, True),
        (["--no-browser"], {}, False),
        ([], {ENV_NO_BROWSER: "1"}, False),
        ([], {ENV_NO_BROWSER: "TRUE"}, False),
        ([], {ENV_NO_BROWSER: "0"}, True),
        (["--reload"], {}, False),
    ],
)
def test_browser_switch(argv: list[str], environ: dict[str, str], expected: bool) -> None:
    """Oracle: spec-anchored — task prompt "Browser-Öffnen per Env/Flag abschaltbar" (AK5b)."""
    assert should_open_browser(parse_args(argv), environ) is expected


def test_cli_defaults_bind_localhost_8765() -> None:
    """Oracle: spec-anchored — UC NF1 ``http://127.0.0.1:8765``."""
    args = parse_args([])
    assert (args.host, args.port) == ("127.0.0.1", 8765)
