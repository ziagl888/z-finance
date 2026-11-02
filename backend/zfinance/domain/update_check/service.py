"""Update notice: is a newer z-finance published? (UC NF10, SPEC AK20)

z-finance makes no outbound connection on its own (UC NF2). This feature is the one exception the
use case asks for, and it stays off until the user switches it on. Switched on and asked, it reads
the newest published release of the project from the GitHub API and remembers version, link and
time. It downloads nothing, writes no file, installs nothing and runs nothing: the user follows the
link and installs the new version the same way as the first one (`docs/manual/en/install.md`).

Everything is kept in `app_setting` under the prefix ``update_check.``: ``enabled`` (the opt-in)
plus the result of the last run — ``latest_version``, ``release_url`` and ``checked_at``. No
migration and no table of its own; the feature holds four scalars.

Invariants:
- No request leaves the machine while ``update_check.enabled`` is false.
- The only URL ever opened is the releases endpoint of this repository, over https.
- A failed or refused check leaves the remembered result untouched.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import zip_longest
from typing import IO, Any

from zfinance import __version__
from zfinance.app_settings import get_setting, set_setting
from zfinance.db import transaction
from zfinance.i18n import N_

logger = logging.getLogger(__name__)

#: The public repository z-finance is released from (UC NF10 "Installer von der GitHub-Release-Seite").
REPOSITORY = "ziagl888/z-finance"
RELEASES_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPOSITORY}/releases"
#: The only prefixes a URL may carry to be opened or shown as a link.
API_URL_PREFIX = f"https://api.github.com/repos/{REPOSITORY}/"
RELEASE_LINK_PREFIX = f"https://github.com/{REPOSITORY}/releases/"

#: A release description is a few kilobytes; more than this is not an answer we asked for.
MAX_RESPONSE_BYTES = 256 * 1024
TIMEOUT_SECONDS = 10.0

ENABLED_KEY = "update_check.enabled"
LATEST_VERSION_KEY = "update_check.latest_version"
RELEASE_URL_KEY = "update_check.release_url"
CHECKED_AT_KEY = "update_check.checked_at"

#: A leading "v" is the usual tag style; everything after the digits (a pre-release suffix) is cut.
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")

#: What ``urllib.request.urlopen`` is to this module: a request in, a readable response out.
Opener = Callable[[urllib.request.Request], IO[bytes]]


class UpdateCheckError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``;
    ``str(exc)`` is the English text.
    """

    status_code = 502

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class Disabled(UpdateCheckError):
    """A check was asked for while the opt-in is off."""

    status_code = 409


@dataclass(frozen=True)
class UpdateState:
    """What the screen shows: the opt-in, the running version and the last answer from GitHub."""

    enabled: bool
    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str
    checked_at: str | None


def parse_version(text: str) -> tuple[int, ...] | None:
    """The leading dotted number of ``text`` as a tuple, or None when it does not start with one.

    ``"v1.2.3"`` and ``"1.2.3-rc1"`` are ``(1, 2, 3)``; ``"nightly"`` is None. Local version parts
    of the running version (``"0.1.0+unknown"``) are cut the same way.
    """
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match[1].split("."))


def is_newer(current: str, tag: str) -> bool:
    """True when release ``tag`` is a higher version than ``current``.

    Missing components count as zero (``1.0`` equals ``1.0.0``). A side that is not a version at
    all means "nothing newer": an unreadable tag must not nag the user, and it must not be an error
    either — the release simply carries no version we understand.
    """
    latest = parse_version(tag)
    running = parse_version(current)
    if latest is None or running is None:
        return False
    pairs = list(zip_longest(latest, running, fillvalue=0))
    return [left for left, _ in pairs] > [right for _, right in pairs]


def is_enabled(conn: sqlite3.Connection) -> bool:
    """Whether the user switched the update check on. Off until they do (UC NF2)."""
    return bool(get_setting(conn, ENABLED_KEY, False))


def set_enabled(conn: sqlite3.Connection, enabled: bool) -> bool:
    """Store the opt-in. Switching it off also forgets the last answer, so no stale notice remains."""
    with transaction(conn):
        set_setting(conn, ENABLED_KEY, bool(enabled))
        if not enabled:
            for key in (LATEST_VERSION_KEY, RELEASE_URL_KEY, CHECKED_AT_KEY):
                set_setting(conn, key, None)
    return bool(enabled)


def state(conn: sqlite3.Connection, *, current_version: str = __version__) -> UpdateState:
    """The remembered result, without asking GitHub."""
    latest = get_setting(conn, LATEST_VERSION_KEY, None)
    url = get_setting(conn, RELEASE_URL_KEY, None)
    return UpdateState(
        enabled=is_enabled(conn),
        current_version=current_version,
        latest_version=latest,
        update_available=bool(latest) and is_newer(current_version, str(latest)),
        release_url=str(url) if url else RELEASES_PAGE_URL,
        checked_at=get_setting(conn, CHECKED_AT_KEY, None),
    )


def _default_opener(request: urllib.request.Request) -> IO[bytes]:
    """Open ``request``.

    The URL is not user input: ``_read_release`` refused anything but the https GitHub API
    before this is reached, which is what the linters' url-open warnings ask about.
    """
    return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)  # noqa: S310 # nosec B310


def _read_release(url: str, opener: Opener) -> dict[str, Any]:
    """Read the release JSON from ``url``.

    ``url`` must be the releases endpoint of this repository over https; anything else is refused
    before a socket is opened, so a wrong configuration cannot turn the update check into a request
    to somewhere else. At most ``MAX_RESPONSE_BYTES`` are read.
    """
    if not url.startswith(API_URL_PREFIX):
        raise UpdateCheckError(N_("The update check is not configured correctly."))
    request = urllib.request.Request(  # noqa: S310 - prefix checked above, https only
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"z-finance/{__version__}"},
        method="GET",
    )
    try:
        with opener(request) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, ValueError) as exc:
        logger.info("update check could not reach GitHub: %s", exc)
        raise UpdateCheckError(N_("z-finance could not reach GitHub to look for updates.")) from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise UpdateCheckError(N_("The answer from GitHub was unexpectedly large."))
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError(N_("The answer from GitHub could not be read.")) from exc
    if not isinstance(data, dict):
        raise UpdateCheckError(N_("The answer from GitHub could not be read."))
    return data


def _release_link(data: dict[str, Any]) -> str:
    """The link shown to the user: the release page of this repository, never a foreign URL."""
    link = data.get("html_url")
    return link if isinstance(link, str) and link.startswith(RELEASE_LINK_PREFIX) else RELEASES_PAGE_URL


def check_now(
    conn: sqlite3.Connection,
    *,
    current_version: str = __version__,
    opener: Opener | None = None,
    url: str = RELEASES_API_URL,
    now: datetime | None = None,
) -> UpdateState:
    """Ask GitHub for the newest release and remember the answer.

    Refused with :class:`Disabled` while the opt-in is off — that check comes first, so nothing
    leaves the machine. Only tag name, link and time are kept; the rest of the answer is dropped.
    A failure raises and leaves the remembered result untouched.
    """
    if not is_enabled(conn):
        raise Disabled(N_("The update check is switched off."))
    data = _read_release(url, opener or _default_opener)
    tag = data.get("tag_name")
    latest = tag.strip() if isinstance(tag, str) and tag.strip() else None
    link = _release_link(data)
    checked_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    with transaction(conn):
        set_setting(conn, LATEST_VERSION_KEY, latest)
        set_setting(conn, RELEASE_URL_KEY, link)
        set_setting(conn, CHECKED_AT_KEY, checked_at)
    return UpdateState(
        enabled=True,
        current_version=current_version,
        latest_version=latest,
        update_available=latest is not None and is_newer(current_version, latest),
        release_url=link,
        checked_at=checked_at,
    )
