"""UC-ZF-013 receipt agent end to end: fake mailbox and fake claude against the real z-finance API.

Expected values are hand-computed from UC-ZF-013 (AK1-AK6, 6a) and
docs/specs/SPEC-T-2026-CU-9130-020.md. All mails, addresses and photos are synthetic.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
from pathlib import Path

import httpx
import pytest
from agent_fakes import OWNER, STRANGER, World, envelope, extraction, jpeg, mail, make_config, world
from zfinance_agent import config as agent_config
from zfinance_agent import mailbox as mailbox_module
from zfinance_agent.config import ConfigError, load_config
from zfinance_agent.mailbox import Mailbox, MailboxError
from zfinance_agent.run import ExitCode, main, run

from zfinance.config import resolve_data_dir

__all__ = ["world"]  # the fixture, imported for pytest


def _receipts(w: World, *statuses: str) -> list[dict]:
    response = w.api.get("/api/receipts", params=[("status", s) for s in statuses])
    response.raise_for_status()
    return response.json()


def test_run_books_receipt_and_replies(world: World) -> None:
    """A readable photo from the owner → one booking with the receipt, message seen, reply, run counted (AK1).

    Oracle: spec-anchored — UC-ZF-013 AK1 ("genau eine Buchung mit diesem Betrag und Datum und einem
    Beleg-Link") and steps 2-6; amount -12.34 EUR is the model's 1234 cents as an expense.
    """
    uid = world.imap.add(mail(photos=(jpeg("a"),)))
    assert run(make_config(), world.deps(), world.log) == ExitCode.OK

    [booking] = world.bookings()
    assert booking["amount_cents"] == -1_234
    assert booking["date"] == extraction()["date"]
    [receipt] = _receipts(world, "booked")
    assert receipt["transaction_id"] == booking["id"]
    assert world.imap.flags[uid] == {"\\Seen"}
    [answer] = world.smtp.sent
    assert answer["To"] == OWNER
    assert "1 booked" in answer["Subject"]
    [agent_run] = world.api.get("/api/receipts/runs").json()
    assert agent_run["booked"] == 1
    assert agent_run["finished_at"] is not None
    assert agent_run["counts"]["messages"] == 1


def test_same_photo_again_is_reported_as_duplicate(world: World) -> None:
    """The same photo sent twice → one booking; the second reply says "already recorded" (AK2).

    Oracle: spec-anchored — UC-ZF-013 AK2 ("keine zweite Buchung, Rückmeldung schon erfasst").
    """
    world.imap.add(mail(subject="first", photos=(jpeg("same"),)))
    run(make_config(), world.deps(), world.log)
    world.imap.add(mail(subject="again", photos=(jpeg("same"),)))
    assert run(make_config(), world.deps(), world.log) == ExitCode.OK

    assert len(world.bookings()) == 1
    second = world.smtp.sent[-1]
    assert "1 already recorded" in second["Subject"]
    assert "- already recorded:" in second.get_content()


def test_invalid_output_twice_becomes_review_item(world: World) -> None:
    """Invalid model output twice → exactly two calls, then a review item "unreadable", no booking (AK3).

    Oracle: spec-anchored — task stop condition ("kein valides JSON trotz Schema → Retry 1x, dann
    Prüfposten") and UC-ZF-013 3a.
    """
    world.claude.answers = ["not json", envelope({"amount_cents": "twelve"})]
    world.imap.add(mail(photos=(jpeg("x"),)))
    assert run(make_config(), world.deps(), world.log) == ExitCode.OK

    assert len(world.claude.calls) == 2
    assert world.bookings() == []
    [item] = _receipts(world, "review")
    assert item["reason"] == "unreadable"
    assert "1 to check" in world.smtp.sent[0]["Subject"]


def test_invalid_output_once_is_retried(world: World) -> None:
    """One invalid answer, then a valid one → booked after the retry (AK3).

    Oracle: spec-anchored — task stop condition ("Retry 1x").
    """
    world.claude.answers = [envelope({"unexpected": True})]
    world.imap.add(mail(photos=(jpeg("r"),)))
    run(make_config(), world.deps(), world.log)
    assert len(world.claude.calls) == 2
    assert len(world.bookings()) == 1


def test_message_text_goes_to_api_as_hints(world: World) -> None:
    """Message text "business" → booking on the business account (AK4).

    Oracle: spec-anchored — UC-ZF-013 AK4 ("Text business → Buchung im Bereich business auf dem
    Business-Konto").
    """
    world.imap.add(mail(text="business", photos=(jpeg("b"),)))
    run(make_config(), world.deps(), world.log)
    [booking] = world.bookings()
    assert booking["account_id"] == world.business_account


def test_foreign_sender_is_ignored(world: World) -> None:
    """A stranger's message → no model call, no API receipt, no flag change, no reply (AK5).

    Oracle: spec-anchored — UC-ZF-013 AK5 ("wird nicht verarbeitet und erzeugt nichts").
    """
    uid = world.imap.add(mail(sender=STRANGER, photos=(jpeg("s"),)))
    spoofed = world.imap.add(mail(sender=f"{OWNER}, {STRANGER}", photos=(jpeg("t"),)))
    assert run(make_config(), world.deps(), world.log) == ExitCode.OK

    assert world.claude.calls == []
    assert world.bookings() == []
    assert _receipts(world, "booked", "review", "duplicate") == []
    assert world.imap.flags[uid] == set() and world.imap.flags[spoofed] == set()
    assert all("BODY.PEEK[]" not in item for _, item in world.imap.fetched)
    assert world.smtp.sent == []


def test_subject_secret_required_when_configured(world: World) -> None:
    """With a subject secret, the owner's message without it is ignored; with it, booked (AK5 second hurdle).

    Oracle: spec-anchored — orchestrator note "zweite Hürde, optionales Geheimwort im Betreff".
    """
    config = make_config(subject_secret="Sesame42")
    plain = world.imap.add(mail(subject="receipt", photos=(jpeg("p"),)))
    secret = world.imap.add(mail(subject="receipt sesame42 business", photos=(jpeg("q"),)))
    run(config, world.deps(), world.log)

    assert world.imap.flags[plain] == set()
    assert world.imap.flags[secret] == {"\\Seen"}
    [booking] = world.bookings()
    assert booking["account_id"] == world.business_account
    [receipt] = _receipts(world, "booked")
    assert "sesame" not in receipt["extracted"]["hints"].casefold()


@pytest.mark.parametrize("secret_line", ["", "ZF_AGENT_SUBJECT_SECRET=\n", "ZF_AGENT_SUBJECT_SECRET=   \n"])
def test_empty_subject_secret_stops_before_any_mail_is_read(
    secret_line: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No subject secret → exit 2 naming it; no mailbox is opened, so a forged mail cannot be booked.

    Oracle: spec-anchored — KB task T-2026-CU-9130-035 ("leeres Geheimnis + gefälschter Absender darf
    nicht stillschweigend durchgehen"), agent/README.md set-up step 2 (the secret word is part of the
    set-up) and its exit-code table (a missing setting → exit 2, named, never its value).
    """
    for name in list(os.environ):
        if name.startswith("ZF_AGENT_"):
            monkeypatch.delenv(name)
    opened: list[str] = []

    def no_imap(host: str, *args: object, **kwargs: object) -> object:
        opened.append(host)
        raise OSError("the mailbox must not be opened")

    monkeypatch.setattr(mailbox_module.imaplib, "IMAP4_SSL", no_imap)
    env = tmp_path / ".env"
    env.write_text(
        "ZF_AGENT_IMAP_HOST=imap.example.test\nZF_AGENT_IMAP_USER=receipts@example.test\n"
        f"ZF_AGENT_IMAP_PASSWORD=placeholder-secret\nZF_AGENT_ALLOWED_SENDERS={OWNER}\n{secret_line}",
        encoding="utf-8",
    )

    assert main(["--env-file", str(env)]) == ExitCode.CONFIG
    [line] = capsys.readouterr().err.splitlines()
    record = json.loads(line)
    assert (record["event"], record["code"]) == ("run_aborted", 2)
    assert "ZF_AGENT_SUBJECT_SECRET" in record["error"] and "placeholder-secret" not in line
    assert opened == []
    with pytest.raises(ConfigError, match="ZF_AGENT_SUBJECT_SECRET"):
        load_config(env_file=env)


def test_api_outage_leaves_message_unread_and_next_run_books_once(world: World) -> None:
    """API down while submitting → message stays unread, exit 4; next run books exactly once (AK6).

    Oracle: spec-anchored — UC-ZF-013 AK6 ("bleiben unverarbeitet und werden beim nächsten Lauf
    gebucht; nichts fehlt, nichts ist doppelt").
    """
    first = world.imap.add(mail(subject="two photos", photos=(jpeg("101"), jpeg("102"))))
    second = world.imap.add(mail(subject="later", photos=(jpeg("103"),)))
    uploads = 0

    def outage(request: httpx.Request) -> None:
        nonlocal uploads
        if request.method == "POST" and request.url.path == "/api/receipts":
            uploads += 1
            if uploads == 2:
                raise httpx.ConnectError("down", request=request)

    world.api.event_hooks = {"request": [outage], "response": []}
    assert run(make_config(), world.deps(), world.log) == ExitCode.API
    world.api.event_hooks = {"request": [], "response": []}
    assert world.imap.flags[first] == set() and world.imap.flags[second] == set()
    assert len(world.bookings()) == 1  # the first photo was stored before the outage

    assert run(make_config(), world.deps(), world.log) == ExitCode.OK
    assert world.imap.flags[first] == {"\\Seen"} and world.imap.flags[second] == {"\\Seen"}
    assert sorted(b["amount_cents"] for b in world.bookings()) == [-103, -102, -101]
    assert len(_receipts(world, "booked")) == 3


def test_api_down_at_start_touches_nothing(world: World) -> None:
    """API unreachable before the run starts → exit 4, no mailbox login, no model call (AK6).

    Oracle: spec-anchored — UC-ZF-013 interface details ("API nicht erreichbar → Fotos bleiben
    ungelesen").
    """
    world.imap.add(mail(photos=(jpeg("d"),)))

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://127.0.0.1:8765")
    assert run(make_config(), world.deps(client), world.log) == ExitCode.API
    assert not world.imap.logged_in
    assert world.claude.calls == []


def test_budget_caps_photos_per_run(world: World) -> None:
    """Budget 2: a 2-photo message is read, the next message waits for the next run (AK7).

    Oracle: spec-anchored — orchestrator note "max Bons pro Lauf" and UC "Budget-Deckel".
    """
    first = world.imap.add(mail(subject="a", photos=(jpeg("1"), jpeg("2"))))
    second = world.imap.add(mail(subject="b", photos=(jpeg("3"),)))
    too_many = world.imap.add(mail(subject="c", photos=(jpeg("4"), jpeg("5"), jpeg("6"))))
    run(make_config(max_receipts=2), world.deps(), world.log)

    assert len(world.claude.calls) == 2
    assert world.imap.flags[first] == {"\\Seen"}
    assert world.imap.flags[second] == set()
    assert world.imap.flags[too_many] == set()  # not even looked at: the budget was used up
    run(make_config(max_receipts=2), world.deps(), world.log)
    assert world.imap.flags[second] == {"\\Seen"}
    assert world.imap.flags[too_many] == {"\\Flagged"}  # more photos than one run may read
    assert len(world.claude.calls) == 3


def test_timeout_is_a_failed_attempt(world: World) -> None:
    """A claude timeout counts as a failed attempt; two of them → review item (AK7, AK3).

    Oracle: spec-anchored — orchestrator note "claude -p mit Timeout, genau 1 Retry".
    """
    world.claude.answers = [subprocess.TimeoutExpired("claude", 1), subprocess.TimeoutExpired("claude", 1)]
    world.imap.add(mail(photos=(jpeg("t"),)))
    assert run(make_config(), world.deps(), world.log) == ExitCode.OK
    assert [item["reason"] for item in _receipts(world, "review")] == ["unreadable"]


def test_reply_failure_does_not_fail_run(world: World) -> None:
    """SMTP down → the booking stands, the failure is logged, exit 0 (AK9).

    Oracle: spec-anchored — UC-ZF-013 6a ("Lauf gilt trotzdem als erfolgreich").
    """
    world.smtp.fail = True
    world.imap.add(mail(photos=(jpeg("f"),)))
    assert run(make_config(), world.deps(), world.log) == ExitCode.OK
    assert len(world.bookings()) == 1
    assert any(event["event"] == "reply_failed" for event in world.log.events)


def test_oauth_only_mailbox_stops(world: World) -> None:
    """LOGINDISABLED or an OAuth refusal → exit 6, nothing read (stop condition).

    Oracle: spec-anchored — task stop condition ("IMAP-Zugang benötigt OAuth statt Passwort → STOP").
    """
    world.imap.capabilities = ("IMAP4REV1", "AUTH=XOAUTH2", "LOGINDISABLED")
    world.imap.add(mail(photos=(jpeg("o"),)))
    assert run(make_config(), world.deps(), world.log) == ExitCode.OAUTH
    assert world.claude.calls == []

    world.imap.capabilities = ("IMAP4REV1", "AUTH=XOAUTH2")
    world.imap.login_error = "[ALERT] Application-specific password required"
    assert run(make_config(), world.deps(), world.log) == ExitCode.OAUTH
    world.imap.login_error = "[AUTHENTICATIONFAILED] Invalid credentials"
    assert run(make_config(), world.deps(), world.log) == ExitCode.MAILBOX
    assert world.bookings() == []
    runs = world.api.get("/api/receipts/runs").json()
    assert all(r["finished_at"] for r in runs) and "OAuth" in runs[-1]["note"]


def test_attachment_filter(world: World) -> None:
    """Non-photo attachments are not sent; a message without a photo is marked and answered (AK11).

    Oracle: spec-anchored — SPEC-T-2026-CU-9130-020 design "attachments by magic bytes, ≤ 10 MB".
    """
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = OWNER
    message["Subject"] = "no photo"
    message.set_content("see attachment")
    message.add_attachment(b"%PDF-1.4 synthetic", maintype="application", subtype="pdf", filename="a.pdf")
    message.add_attachment(b"GIF89a synthetic", maintype="image", subtype="gif", filename="b.gif")
    uid = world.imap.add(message.as_bytes())
    assert run(make_config(), world.deps(), world.log) == ExitCode.OK

    assert world.claude.calls == []
    assert world.imap.flags[uid] == {"\\Seen"}
    body = world.smtp.sent[0].get_content()
    assert "unsupported image type" in body and "no receipt photo" in body


def test_imap_connection_verifies_the_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — SPEC-T security boundary (credentials never readable on the way) / CWE-295:
    the default IMAP connection checks certificate and host name."""
    seen: dict[str, object] = {}

    def fake_ssl(host: str, port: int, **kwargs: object) -> object:
        seen.update(kwargs, host=host, port=port)
        raise OSError("stop after connect")

    monkeypatch.setattr(mailbox_module.imaplib, "IMAP4_SSL", fake_ssl)
    with pytest.raises(MailboxError):
        Mailbox.open(
            load_config(
                {
                    "ZF_AGENT_IMAP_HOST": "imap.example.com",
                    "ZF_AGENT_IMAP_USER": "u@example.com",
                    "ZF_AGENT_IMAP_PASSWORD": "x",
                    "ZF_AGENT_ALLOWED_SENDERS": "me@example.com",
                    "ZF_AGENT_SUBJECT_SECRET": "placeholder-word",
                }
            )
        )
    context = seen["ssl_context"]
    assert isinstance(context, ssl.SSLContext)
    assert (context.verify_mode, context.check_hostname) == (ssl.CERT_REQUIRED, True)
    assert (seen["host"], seen["port"]) == ("imap.example.com", 993)


def _base_env(**more: str) -> dict[str, str]:
    values = {
        "ZF_AGENT_IMAP_HOST": "imap.example.test",
        "ZF_AGENT_IMAP_USER": "receipts@example.test",
        "ZF_AGENT_IMAP_PASSWORD": "placeholder",
        "ZF_AGENT_ALLOWED_SENDERS": OWNER,
        "ZF_AGENT_SUBJECT_SECRET": "placeholder-word",
    }
    values.update(more)
    return values


def _launcher_state(data: Path, text: str) -> None:
    (data / "runtime").mkdir(parents=True)
    (data / "runtime" / "launcher.json").write_text(text, encoding="utf-8")


def test_api_url_follows_the_launcher_when_unset(tmp_path: Path) -> None:
    """No ZF_AGENT_API_URL → the address in <data dir>/runtime/launcher.json; an explicit value wins.

    Oracle: hand-written from the launcher's contract (port search from 8765, address written to
    runtime/launcher.json): app on 8766 → agent must call 8766.
    """
    _launcher_state(tmp_path, json.dumps({"url": "http://127.0.0.1:8766", "port": 8766, "pid": 1}))
    env = _base_env(ZFINANCE_DATA_DIR=str(tmp_path))

    assert load_config(env).api_url == "http://127.0.0.1:8766"
    assert load_config({**env, "ZF_AGENT_API_URL": "http://127.0.0.1:9000/"}).api_url == "http://127.0.0.1:9000"


@pytest.mark.parametrize("state", [None, "not json", "[]", '{"url": 5}', '{"url": "  "}'])
def test_api_url_falls_back_to_the_default_without_a_usable_launcher_file(tmp_path: Path, state: str | None) -> None:
    """Missing, unreadable or malformed launcher.json → the old default 127.0.0.1:8765."""
    if state is not None:
        _launcher_state(tmp_path, state)

    assert load_config(_base_env(ZFINANCE_DATA_DIR=str(tmp_path))).api_url == "http://127.0.0.1:8765"


def test_launcher_url_must_still_be_http(tmp_path: Path) -> None:
    """A launcher.json with another scheme is refused like a bad ZF_AGENT_API_URL, naming the setting."""
    _launcher_state(tmp_path, '{"url": "file:///etc/passwd"}')

    with pytest.raises(ConfigError, match="ZF_AGENT_API_URL"):
        load_config(_base_env(ZFINANCE_DATA_DIR=str(tmp_path)))


def test_data_dir_matches_the_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The agent finds the data folder the same way the app does: the override and the per-user default."""
    override = {"ZFINANCE_DATA_DIR": str(tmp_path)}
    assert agent_config.data_dir(override) == resolve_data_dir(override)
    monkeypatch.delenv("ZFINANCE_DATA_DIR", raising=False)
    assert agent_config.data_dir({}) == resolve_data_dir({})
