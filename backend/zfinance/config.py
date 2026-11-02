"""Runtime configuration: data directory, bind address, bundle location.

Configuration comes from environment variables and CLI flags, never from a file in the
repository, so a checkout can never carry someone's data path or personal settings.

Environment variables:
    ZFINANCE_DATA_DIR    Directory holding the SQLite database and attachments. Overrides the
                         per-user default (developer mode, tests, portable installs).
    ZFINANCE_NO_BROWSER  "1"/"true"/"yes"/"on" stops ``z-finance`` from opening a browser tab.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

APP_NAME = "z-finance"
DB_FILENAME = "z-finance.sqlite3"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

ENV_DATA_DIR = "ZFINANCE_DATA_DIR"
ENV_NO_BROWSER = "ZFINANCE_NO_BROWSER"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Where ``npm run build`` writes the SPA bundle (see frontend/vite.config.ts).
DEFAULT_WEB_DIR = Path(__file__).parent / "web"


def resolve_data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the data directory without creating it.

    ``ZFINANCE_DATA_DIR`` wins when set to a non-blank value (``~`` is expanded). Otherwise the
    per-user data directory from platformdirs is used: ``%LOCALAPPDATA%\\z-finance`` on Windows,
    ``~/.local/share/z-finance`` on Linux, ``~/Library/Application Support/z-finance`` on macOS.
    ``appauthor=False`` keeps Windows from nesting an extra author folder.
    """
    env = os.environ if environ is None else environ
    override = env.get(ENV_DATA_DIR, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def browser_disabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when ``ZFINANCE_NO_BROWSER`` asks the CLI not to open a browser tab."""
    env = os.environ if environ is None else environ
    return env.get(ENV_NO_BROWSER, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings for one app instance.

    Tests build their own instance with a temporary ``data_dir`` instead of touching the
    environment; ``Settings.from_env()`` is what the CLI uses.
    """

    data_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    web_dir: Path = field(default=DEFAULT_WEB_DIR)

    @property
    def db_path(self) -> Path:
        """Path of the single SQLite database file inside the data directory."""
        return self.data_dir / DB_FILENAME

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **overrides: object) -> Settings:
        """Build settings from the environment; keyword overrides (host, port, web_dir) come from the CLI."""
        return cls(data_dir=resolve_data_dir(environ), **overrides)  # type: ignore[arg-type]
