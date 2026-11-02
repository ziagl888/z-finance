"""The summary mail per sender and run (UC step 6); a failed send never fails the run (UC 6a)."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage

from zfinance_agent.config import Config

SUBJECT = "z-finance receipts: {booked} booked, {review} to check, {duplicate} already recorded"

_REASONS = {
    "unreadable": "not readable",
    "missing_fields": "date, total or shop missing",
    "foreign_currency": "foreign currency",
    "low_confidence": "unsure reading",
    "no_account": "no matching account",
    "not_bookable": "account does not allow this date",
}


@dataclass
class Summary:
    """What happened to one sender's receipts in this run."""

    booked: int = 0
    review: int = 0
    duplicate: int = 0
    failed: int = 0
    lines: list[str] = field(default_factory=list)

    def add(self, status: str, line: str) -> None:
        """Count a receipt by its API status (or ``failed``) and keep its line."""
        if status in {"booked", "review", "duplicate"}:
            setattr(self, status, getattr(self, status) + 1)
        else:
            self.failed += 1
        self.lines.append(line)


def describe(status: str, reason: str | None, amount_cents: int | None, day: str | None, merchant: str | None) -> str:
    """One line of the summary; values come from the model, the text around them is fixed."""
    amount = f"{amount_cents // 100},{amount_cents % 100:02d} EUR" if amount_cents else "? EUR"
    what = " ".join(part for part in (day or "", amount, (merchant or "")[:60]) if part)
    if status == "booked":
        return f"- booked: {what}"
    if status == "duplicate":
        return f"- already recorded: {what}"
    if status == "review":
        return f"- to check in z-finance ({_REASONS.get(reason or '', 'unsure')}): {what}"
    return f"- not accepted, message flagged in the mailbox: {what}"


def build(config: Config, recipient: str, summary: Summary) -> EmailMessage:
    """The reply mail to ``recipient`` (an allowlisted address)."""
    message = EmailMessage()
    message["From"] = config.reply_from
    message["To"] = recipient
    message["Subject"] = SUBJECT.format(booked=summary.booked, review=summary.review, duplicate=summary.duplicate)
    body = [
        f"Booked: {summary.booked}",
        f"To check (Receipts page): {summary.review}",
        f"Already recorded: {summary.duplicate}",
    ]
    if summary.failed:
        body.append(f"Not accepted: {summary.failed}")
    message.set_content("\n".join([*body, "", *summary.lines, ""]))
    return message


Sender = Callable[[Config, EmailMessage], None]


def send_smtp(config: Config, message: EmailMessage) -> None:
    """Send over SMTP with TLS (implicit or STARTTLS) and the configured login."""
    context = ssl.create_default_context()
    smtp: smtplib.SMTP
    if config.smtp_security == "ssl":
        smtp = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=60, context=context)
    else:
        smtp = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=60)
    with smtp:
        if config.smtp_security == "starttls":
            smtp.starttls(context=context)
        smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(message)
