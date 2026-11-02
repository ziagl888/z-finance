"""Test doubles for the receipt agent: an IMAP server object, the claude CLI, the SMTP sender.

Every mail, address and photo is synthetic (``example.test`` addresses, a few bytes behind a real
image signature). No real mailbox is contacted and ``claude`` is never started.
"""

from __future__ import annotations

import imaplib
import io
import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from zfinance_agent.config import Config
from zfinance_agent.log import Log
from zfinance_agent.mailbox import Mailbox
from zfinance_agent.run import Deps

from zfinance.app import create_app
from zfinance.config import Settings

OWNER = "owner@example.test"
STRANGER = "stranger@example.test"
DAY = date.today() - timedelta(days=5)


def jpeg(tag: str) -> bytes:
    """A tiny synthetic 'JPEG': signature, a tag that makes each photo unique, end marker."""
    return b"\xff\xd8\xff\xe0" + tag.encode() + b"\xff\xd9"


def mail(sender: str = OWNER, subject: str = "receipt", text: str = "", photos: tuple[bytes, ...] = ()) -> bytes:
    """A synthetic message with the photos as JPEG attachments."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "receipts@example.test"
    message["Subject"] = subject
    message["Message-ID"] = f"<{abs(hash((sender, subject, text, photos)))}@example.test>"
    message.set_content(text or " ")
    for number, photo in enumerate(photos):
        message.add_attachment(photo, maintype="image", subtype="jpeg", filename=f"photo{number}.jpg")
    return message.as_bytes()


class FakeImap:
    """In-memory IMAP server object with the calls ``Mailbox`` makes (UID SEARCH/FETCH/STORE)."""

    def __init__(self, capabilities: tuple[str, ...] = ("IMAP4REV1", "AUTH=PLAIN")) -> None:
        self.capabilities = capabilities
        self.messages: dict[str, bytes] = {}
        self.flags: dict[str, set[str]] = {}
        self.logged_in = False
        self.fetched: list[tuple[str, str]] = []
        self.login_error: str | None = None

    def add(self, raw: bytes) -> str:
        uid = str(len(self.messages) + 1)
        self.messages[uid] = raw
        self.flags[uid] = set()
        return uid

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        if self.login_error:
            raise imaplib.IMAP4.error(self.login_error)
        self.logged_in = True
        return "OK", [b"logged in"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[Any]]:
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            assert args == ("UNSEEN", "UNFLAGGED")
            found = [uid for uid, flags in self.flags.items() if not flags & {"\\Seen", "\\Flagged"}]
            return "OK", [" ".join(found).encode()]
        if command == "FETCH":
            uid, item = args
            self.fetched.append((uid, item))
            assert "PEEK" in item, "the agent must never fetch with an implicit \\Seen"
            raw = self.messages[uid]
            data = raw.split(b"\n\n", 1)[0] + b"\n\n" if "HEADER" in item else raw
            return "OK", [(f"{uid} (UID {uid} {item} {{{len(data)}}}".encode(), data), b")"]
        if command == "STORE":
            uid, _, flag = args
            self.flags[uid].add(flag.strip("()"))
            return "OK", [b""]
        raise AssertionError(f"unexpected IMAP command {command}")

    def logout(self) -> tuple[str, list[Any]]:
        return "BYE", [b""]


def extraction(**more: Any) -> dict[str, Any]:
    """A valid model answer: 12.34 EUR at a synthetic shop, paid by card."""
    values: dict[str, Any] = {
        "date": DAY.isoformat(),
        "amount_cents": 1_234,
        "merchant": "Test shop",
        "payment_method": "card",
        "category_suggestion": None,
        "confidence": 0.95,
        "currency": "EUR",
        "unreadable": False,
        "line_items": None,
    }
    values.update(more)
    return values


def envelope(structured: Any) -> str:
    """``claude -p --output-format json`` stdout around ``structured``."""
    return json.dumps({"type": "result", "is_error": False, "result": "", "structured_output": structured})


@dataclass
class FakeClaude:
    """Stands in for ``run_claude``: answers from ``answers`` in turn (a str is stdout, an exception is raised)."""

    answers: list[str | BaseException] = field(default_factory=list)
    calls: list[tuple[list[str], Path, float]] = field(default_factory=list)
    photos_seen: list[bytes] = field(default_factory=list)

    @staticmethod
    def _default(photo: bytes) -> str:
        """A valid reading; a photo tagged with digits (``jpeg("250")``) reads as that many cents."""
        tag = photo[4:-2].decode()
        return envelope(extraction(amount_cents=int(tag)) if tag.isdigit() else extraction())

    def __call__(self, args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, cwd, timeout))
        files = list(cwd.iterdir())
        assert len(files) == 1 and files[0].name.startswith("receipt."), files
        self.photos_seen.append(files[0].read_bytes())
        answer = self.answers.pop(0) if self.answers else self._default(self.photos_seen[-1])
        if isinstance(answer, BaseException):
            raise answer
        return subprocess.CompletedProcess(args, 0, stdout=answer, stderr="")


@dataclass
class FakeSmtp:
    """Collects reply mails; ``fail`` makes every send raise."""

    sent: list[EmailMessage] = field(default_factory=list)
    fail: bool = False

    def __call__(self, config: Config, message: EmailMessage) -> None:
        if self.fail:
            raise OSError("smtp down")
        self.sent.append(message)


def make_config(**more: Any) -> Config:
    """A config for the fakes; the credentials are placeholders."""
    values: dict[str, Any] = {
        "imap_host": "imap.example.test",
        "imap_user": "receipts@example.test",
        "imap_password": "placeholder",
        "allowed_senders": frozenset({OWNER}),
        "api_url": "http://127.0.0.1:8765",
        "smtp_host": "smtp.example.test",
        "reply_from": "receipts@example.test",
    }
    values.update(more)
    return Config(**values)


@dataclass
class World:
    """One agent environment: a live z-finance app, a fake mailbox, a fake claude, a fake SMTP."""

    api: TestClient
    imap: FakeImap
    claude: FakeClaude
    smtp: FakeSmtp
    log: Log
    business_account: int
    card_account: int

    def deps(self, http_client: httpx.Client | None = None) -> Deps:
        return Deps(
            open_mailbox=lambda config: Mailbox.open(config, lambda host, port: self.imap),
            http_client=http_client or self.api,
            runner=self.claude,
            resolve_claude=lambda name: "/fake/claude",
            send=self.smtp,
        )

    def bookings(self) -> list[dict[str, Any]]:
        response = self.api.get("/api/transactions", params={"limit": 500})
        response.raise_for_status()
        bookings: list[dict[str, Any]] = response.json()
        return bookings


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    """z-finance on a temporary data directory with a private and a business giro account."""
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html>", encoding="utf-8")
    app = create_app(Settings(data_dir=tmp_path / "data", web_dir=web))
    with TestClient(app, base_url="http://127.0.0.1:8765") as api:
        opened = (date.today() - timedelta(days=365)).isoformat()
        ids = {}
        for name, area in (("Test giro", "private"), ("Test business giro", "business")):
            response = api.post(
                "/api/accounts",
                json={"name": name, "area": area, "type": "giro", "opening_balance_cents": 0, "opening_date": opened},
            )
            response.raise_for_status()
            ids[area] = response.json()["id"]
        yield World(
            api, FakeImap(), FakeClaude(), FakeSmtp(), Log(stream=io.StringIO()), ids["business"], ids["private"]
        )
