"""Agent settings from the environment, optionally pre-filled from a ``.env`` file.

Credentials live only in the environment or in a ``.env`` file next to the agent (git-ignored);
``.env.example`` in the repository root lists every variable with placeholders. A variable that is
already set in the environment wins over the file. Error messages name a variable, never its value.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

PREFIX = "ZF_AGENT_"
DEFAULT_API_URL = "http://127.0.0.1:8765"
APP_NAME = "z-finance"
DATA_DIR_VARIABLE = "ZFINANCE_DATA_DIR"
SmtpSecurity = Literal["ssl", "starttls"]


class ConfigError(Exception):
    """A required variable is missing or has an invalid value."""


@dataclass(frozen=True)
class Config:
    """Everything one agent run needs."""

    imap_host: str
    imap_user: str
    imap_password: str
    allowed_senders: frozenset[str]
    imap_port: int = 993
    imap_folder: str = "INBOX"
    subject_secret: str = ""
    api_url: str = DEFAULT_API_URL
    max_receipts: int = 10
    claude_bin: str = "claude"
    claude_model: str = ""
    claude_timeout: float = 180.0
    claude_max_budget_usd: float = 0.5
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: SmtpSecurity = "ssl"
    smtp_user: str = ""
    smtp_password: str = ""
    reply_from: str = ""
    log_file: str = ""

    @property
    def reply_enabled(self) -> bool:
        """Replies are sent only when an SMTP host is configured."""
        return bool(self.smtp_host)


def read_env_file(path: Path) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines; ``#`` comments, blank lines and surrounding quotes are allowed."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip().removeprefix("export ").strip()] = value
    return values


def normalize_address(address: str) -> str:
    """The form addresses are compared in: stripped and lower-cased."""
    return address.strip().casefold()


def data_dir(merged: Mapping[str, str]) -> Path:
    """The folder z-finance keeps its data in: ``ZFINANCE_DATA_DIR`` or the per-user default.

    Mirrors ``zfinance.config.resolve_data_dir`` without importing the app, so the agent stays a
    package of its own; a test pins the two together.
    """
    override = merged.get(DATA_DIR_VARIABLE, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / APP_NAME
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / APP_NAME


def launcher_url(merged: Mapping[str, str]) -> str | None:
    """The address the launcher recorded for the running app, or None when there is none to use."""
    try:
        state = json.loads((data_dir(merged) / "runtime" / "launcher.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url = state.get("url") if isinstance(state, dict) else None
    return url.strip() if isinstance(url, str) and url.strip() else None


def resolve_api_url(merged: Mapping[str, str]) -> str:
    """``ZF_AGENT_API_URL`` when set, else the address the launcher recorded, else the default.

    Unset: follow the launcher (it moves up from 8765 when that port is taken). Only http(s) passes.
    """
    explicit = merged.get(PREFIX + "API_URL", "").strip()
    api_url = (explicit or launcher_url(merged) or DEFAULT_API_URL).rstrip("/")
    if urlsplit(api_url).scheme not in {"http", "https"}:
        raise ConfigError(f"{PREFIX}API_URL must start with http:// or https://")
    return api_url


def load_config(environ: Mapping[str, str] | None = None, env_file: Path | None = None) -> Config:
    """Build the config from ``environ`` (default ``os.environ``) over the values of ``env_file``."""
    merged: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        merged.update(read_env_file(env_file))
    merged.update(os.environ if environ is None else environ)

    def get(name: str, default: str | None = None) -> str:
        value = merged.get(PREFIX + name, "").strip()
        if value:
            return value
        if default is None:
            raise ConfigError(f"{PREFIX}{name} is not set")
        return default

    def number[T: (int, float)](name: str, default: T, kind: type[T], low: T, high: T) -> T:
        text = get(name, str(default))
        try:
            value = kind(text)
        except ValueError:
            raise ConfigError(f"{PREFIX}{name} must be a number") from None
        if not low <= value <= high:
            raise ConfigError(f"{PREFIX}{name} must be between {low} and {high}")
        return value

    senders = frozenset(normalize_address(s) for s in get("ALLOWED_SENDERS").split(",") if s.strip())
    if not senders or any("@" not in s for s in senders):
        raise ConfigError(f"{PREFIX}ALLOWED_SENDERS must list e-mail addresses, separated by commas")
    # A From header is trivially forged; the secret word in the subject is the real hurdle, so the
    # agent refuses to start without one instead of booking forged mails unnoticed.
    subject_secret = merged.get(PREFIX + "SUBJECT_SECRET", "").strip()
    if not subject_secret:
        raise ConfigError(
            f"{PREFIX}SUBJECT_SECRET is not set: without a secret word in the subject, "
            "anyone who forges your sender address could book receipts"
        )
    api_url = resolve_api_url(merged)
    security = get("SMTP_SECURITY", "ssl").lower()
    if security not in {"ssl", "starttls"}:
        raise ConfigError(f"{PREFIX}SMTP_SECURITY must be ssl or starttls")
    imap_user = get("IMAP_USER")
    imap_password = get("IMAP_PASSWORD")
    return Config(
        imap_host=get("IMAP_HOST"),
        imap_user=imap_user,
        imap_password=imap_password,
        allowed_senders=senders,
        imap_port=number("IMAP_PORT", 993, int, 1, 65535),
        imap_folder=get("IMAP_FOLDER", "INBOX"),
        subject_secret=subject_secret,
        api_url=api_url,
        max_receipts=number("MAX_RECEIPTS", 10, int, 1, 50),
        claude_bin=get("CLAUDE_BIN", "claude"),
        claude_model=get("CLAUDE_MODEL", ""),
        claude_timeout=number("CLAUDE_TIMEOUT", 180.0, float, 10.0, 1800.0),
        claude_max_budget_usd=number("CLAUDE_MAX_BUDGET_USD", 0.5, float, 0.01, 10.0),
        smtp_host=get("SMTP_HOST", ""),
        smtp_port=number("SMTP_PORT", 465 if security == "ssl" else 587, int, 1, 65535),
        smtp_security="ssl" if security == "ssl" else "starttls",
        smtp_user=get("SMTP_USER", imap_user),
        smtp_password=get("SMTP_PASSWORD", imap_password),
        reply_from=get("REPLY_FROM", imap_user),
        log_file=get("LOG_FILE", ""),
    )
