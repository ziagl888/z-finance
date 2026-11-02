"""IMAP mailbox: find new receipt messages from allowlisted senders, mark them when done.

Only unread, unflagged messages are candidates. The ``From`` header is read first (``BODY.PEEK``
never sets ``\\Seen``); a message not from exactly one allowlisted address — or without the subject
secret when one is configured — is left exactly as it was. Everything in a message is untrusted:
photos are recognised by their bytes, the text is only passed on as data.

Invariants:

* No flag is changed on a message that is not from an allowlisted sender.
* ``\\Seen`` is set only through ``mark_processed``, which the run calls after the API accepted
  every photo of the message.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import imaplib
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any, Protocol

from zfinance_agent.config import Config, normalize_address

#: The API's limit per photo (``MAX_PHOTO_BYTES`` of the receipts feature).
MAX_PHOTO_BYTES = 10 * 1024 * 1024
#: The API's limit for the ``hints`` field.
MAX_HINTS = 1000


class MailboxError(Exception):
    """The mailbox cannot be reached or refused the login."""


class OAuthRequired(MailboxError):
    """The server does not accept a password login (stop condition: choose another mailbox)."""


class ImapClient(Protocol):
    """The part of ``imaplib.IMAP4`` the agent uses."""

    capabilities: tuple[str, ...]

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        """Authenticate with user name and password."""

    def select(self, mailbox: str = ..., readonly: bool = ...) -> tuple[str, list[Any]]:
        """Open a mailbox."""

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        """Run a UID command (``SEARCH``, ``FETCH``, ``STORE``)."""

    def logout(self) -> tuple[str, list[Any]]:
        """End the session."""


def detect_photo(data: bytes) -> str | None:
    """File extension for a JPEG, PNG or WebP photo, from the magic bytes; else ``None``."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@dataclass
class Photo:
    """One image attachment the API accepts."""

    data: bytes
    extension: str


@dataclass
class InboxMessage:
    """A message from an allowlisted sender, ready to be processed."""

    uid: str
    sender: str
    message_ref: str
    hints: str
    photos: list[Photo] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def sender_of(header: str) -> str | None:
    """The normalised address if the ``From`` header holds exactly one address, else ``None``."""
    addresses = [addr for _, addr in getaddresses([header]) if addr]
    if len(addresses) != 1 or "@" not in addresses[0]:
        return None
    return normalize_address(addresses[0])


def hints_of(message: EmailMessage, secret: str) -> str:
    """Subject plus plain-text body without quoted lines and the secret word, ≤ ``MAX_HINTS`` characters."""
    parts = [str(message.get("Subject", ""))]
    body = message.get_body(preferencelist=("plain",))
    if body is not None:
        try:
            text = body.get_content()
        except (LookupError, ValueError):
            text = ""
        parts.extend(line for line in str(text).splitlines() if not line.lstrip().startswith(">"))
    joined = " ".join(" ".join(parts).split())
    if secret:
        joined = " ".join(re.sub(re.escape(secret), " ", joined, flags=re.IGNORECASE).split())
    return joined[:MAX_HINTS]


def photos_of(message: EmailMessage) -> tuple[list[Photo], list[str]]:
    """Image parts the API accepts, and a note per image part that is left out."""
    photos: list[Photo] = []
    skipped: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_maintype() not in {"image", "application"}:
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        extension = detect_photo(payload)
        if extension is None:
            if part.get_content_maintype() == "image":
                skipped.append("unsupported image type")
            continue
        if len(payload) > MAX_PHOTO_BYTES:
            skipped.append("photo larger than 10 MB")
            continue
        photos.append(Photo(payload, extension))
    return photos, skipped


def _is_oauth_refusal(text: str) -> bool:
    lowered = text.casefold()
    return any(word in lowered for word in ("oauth", "xoauth", "application-specific", "app password"))


def _connect_tls(host: str, port: int) -> imaplib.IMAP4_SSL:
    """IMAP over TLS with certificate and host name checks.

    ``imaplib`` without an ``ssl_context`` uses ``ssl._create_stdlib_context``, which verifies
    nothing; a man in the middle would read the password and the receipts.
    """
    return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=60)


class Mailbox:
    """A logged-in IMAP session on the configured folder."""

    def __init__(self, client: ImapClient, config: Config) -> None:
        self._client = client
        self._config = config

    @classmethod
    def open(cls, config: Config, factory: Callable[[str, int], ImapClient] | None = None) -> Mailbox:
        """Connect over TLS, log in with the password and select the folder."""
        connect = factory or _connect_tls
        try:
            client = connect(config.imap_host, config.imap_port)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailboxError(f"cannot connect: {type(exc).__name__}") from exc
        if "LOGINDISABLED" in client.capabilities:
            raise OAuthRequired("the server disables password login (LOGINDISABLED)")
        try:
            client.login(config.imap_user, config.imap_password)
        except imaplib.IMAP4.error as exc:
            if _is_oauth_refusal(str(exc)):
                raise OAuthRequired("the server asks for OAuth or an app password") from exc
            raise MailboxError("login refused") from exc
        except OSError as exc:
            raise MailboxError(f"connection lost: {type(exc).__name__}") from exc
        status, _ = client.select(config.imap_folder)
        if status != "OK":
            raise MailboxError("folder not found")
        return cls(client, config)

    def _command(self, command: str, *args: Any) -> list[Any]:
        try:
            status, data = self._client.uid(command, *args)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailboxError(f"{command} failed: {type(exc).__name__}") from exc
        if status != "OK":
            raise MailboxError(f"{command} failed")
        return data

    def candidate_uids(self) -> list[str]:
        """UIDs of unread, unflagged messages, oldest first."""
        data = self._command("SEARCH", "UNSEEN", "UNFLAGGED")
        raw = data[0] if data else b""
        return [uid.decode() if isinstance(uid, bytes) else str(uid) for uid in (raw or b"").split()]

    def _fetch(self, uid: str, item: str) -> bytes:
        for entry in self._command("FETCH", uid, f"({item})"):
            if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], bytes):
                return entry[1]
        raise MailboxError("FETCH returned no data")

    def read(self, uid: str) -> InboxMessage | None:
        """The message if it passes the allowlist and secret check, else ``None`` (left untouched)."""
        header = email.message_from_bytes(
            self._fetch(uid, "BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)]"), policy=email.policy.default
        )
        sender = sender_of(str(header.get("From", "")))
        if sender is None or sender not in self._config.allowed_senders:
            return None
        secret = self._config.subject_secret
        if secret and secret.casefold() not in str(header.get("Subject", "")).casefold():
            return None
        message = email.message_from_bytes(self._fetch(uid, "BODY.PEEK[]"), policy=email.policy.default)
        if not isinstance(message, EmailMessage):  # policy.default always builds one; narrows the type
            raise MailboxError("unexpected message type")
        photos, skipped = photos_of(message)
        message_ref = " ".join(str(message.get("Message-ID", "") or f"uid:{uid}").split())[:300]
        return InboxMessage(uid, sender, message_ref, hints_of(message, secret), photos, skipped)

    def mark_processed(self, uid: str) -> None:
        """Set ``\\Seen``: the message will not be fetched again."""
        self._command("STORE", uid, "+FLAGS", "(\\Seen)")

    def flag_failed(self, uid: str) -> None:
        """Set ``\\Flagged`` on a message the API refused: later runs skip it, the mail client shows it."""
        self._command("STORE", uid, "+FLAGS", "(\\Flagged)")

    def close(self) -> None:
        """Log out; errors on the way out are ignored."""
        with contextlib.suppress(OSError, imaplib.IMAP4.error):
            self._client.logout()
