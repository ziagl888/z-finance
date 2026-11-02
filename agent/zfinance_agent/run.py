"""One agent run and the ``zfinance-agent`` command line (UC-ZF-013 main scenario 2-6).

Order: find ``claude`` → start an agent run in the API → log in to the mailbox → per candidate
message (oldest first): allowlist, photos, extraction, hand-over → ``\\Seen`` once every photo of the
message was accepted → one reply per sender → finish the agent run with the counts.

Invariants:

* A message is marked ``\\Seen`` only after the API answered 2xx for every photo in it (AK6).
* When the API is unreachable the run stops at once; the current and all later messages keep
  their flags, so the next run picks them up. A photo stored before the stop comes back as a
  duplicate of itself (same photo hash in the API), never as a second booking.
* At most ``max_receipts`` photos are read per run, each with at most two ``claude`` calls.

Exit codes are ``ExitCode``; every step is logged as one JSON line (``log.Log``).
"""

from __future__ import annotations

import argparse
import smtplib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

import httpx

from zfinance_agent import extract as extractor
from zfinance_agent import reply
from zfinance_agent.config import Config, ConfigError, load_config
from zfinance_agent.extract import ExtractorUnavailable
from zfinance_agent.log import Log
from zfinance_agent.mailbox import Mailbox, MailboxError, OAuthRequired
from zfinance_agent.submit import ApiRefused, ApiUnavailable, ReceiptsApi, fields_for


class ExitCode(IntEnum):
    """Process exit status of a run."""

    OK = 0
    CONFIG = 2
    MAILBOX = 3
    API = 4
    EXTRACTOR = 5
    OAUTH = 6


@dataclass
class Deps:
    """The outside world of a run; tests replace each part."""

    open_mailbox: Callable[[Config], Mailbox] = Mailbox.open
    http_client: httpx.Client | None = None
    runner: extractor.Runner = extractor.run_claude
    resolve_claude: Callable[[str], str] = extractor.resolve_claude
    send: reply.Sender = reply.send_smtp


@dataclass
class _State:
    counts: Counter[str] = field(default_factory=Counter)
    summaries: dict[str, reply.Summary] = field(default_factory=dict)


def _process(
    mailbox: Mailbox,
    api: ReceiptsApi,
    executable: str,
    config: Config,
    deps: Deps,
    run_id: int,
    state: _State,
    log: Log,
) -> None:
    budget = config.max_receipts
    for uid in mailbox.candidate_uids():
        if budget == 0:
            state.counts["deferred"] += 1
            continue
        message = mailbox.read(uid)
        if message is None:
            state.counts["ignored"] += 1
            log("message_ignored", uid=uid)
            continue
        summary = state.summaries.setdefault(message.sender, reply.Summary())
        summary.lines.extend(f"- left out: {note}" for note in message.skipped)
        if len(message.photos) > config.max_receipts:
            mailbox.flag_failed(uid)
            state.counts["failed"] += 1
            summary.lines.append(f"- not read: more than {config.max_receipts} photos in one message (flagged)")
            log("message_too_many_photos", level="warning", uid=uid, photos=len(message.photos))
            continue
        if len(message.photos) > budget:
            state.counts["deferred"] += 1
            log("message_deferred", uid=uid, photos=len(message.photos), budget=budget)
            continue
        state.counts["messages"] += 1
        if not message.photos:
            summary.lines.append("- no receipt photo found in a message")
            mailbox.mark_processed(uid)
            log("message_without_photo", uid=uid)
            continue
        refused = False
        for photo in message.photos:
            budget -= 1
            state.counts["receipts"] += 1
            outcome = extractor.extract(executable, photo.data, photo.extension, config, deps.runner)
            if outcome.attempts > 1:
                state.counts["retried"] += 1
            if outcome.extraction is None:
                log("extraction_failed", level="warning", uid=uid, error=outcome.error)
            data = outcome.extraction
            line_values = (
                (data.amount_cents, data.date.isoformat() if data.date else None, data.merchant)
                if data
                else (None, None, None)
            )
            try:
                result = api.submit(
                    photo.data, photo.extension, fields_for(data, message.hints, message.message_ref, run_id)
                )
            except ApiRefused as exc:
                refused = True
                state.counts["failed"] += 1
                summary.add("failed", reply.describe("failed", None, *line_values))
                log("receipt_refused", level="warning", uid=uid, status=exc.status, detail=exc.detail)
                continue
            state.counts[result.status] += 1
            summary.add(result.status, reply.describe(result.status, result.reason, *line_values))
            log(
                "receipt_submitted",
                uid=uid,
                status=result.status,
                reason=result.reason,
                receipt_id=result.receipt_id,
                transaction_id=result.transaction_id,
                attempts=outcome.attempts,
            )
        if refused:
            mailbox.flag_failed(uid)
        else:
            mailbox.mark_processed(uid)
        log("message_done", uid=uid, flagged=refused)


def _send_replies(config: Config, deps: Deps, state: _State, log: Log) -> None:
    for recipient, summary in state.summaries.items():
        if not summary.lines:
            continue
        if not config.reply_enabled:
            log("reply_skipped", reason="no SMTP host configured")
            continue
        try:
            deps.send(config, reply.build(config, recipient, summary))
            log("reply_sent", booked=summary.booked, review=summary.review, duplicate=summary.duplicate)
        except (OSError, smtplib.SMTPException) as exc:
            log("reply_failed", level="warning", error=type(exc).__name__)


def run(config: Config, deps: Deps | None = None, log: Log | None = None) -> ExitCode:
    """Process the mailbox once; the result says how it ended (see module doc)."""
    deps = deps or Deps()
    log = log or Log(file=config.log_file)
    log("run_started", max_receipts=config.max_receipts)
    try:
        executable = deps.resolve_claude(config.claude_bin)
    except ExtractorUnavailable as exc:
        log("run_aborted", level="error", code=int(ExitCode.EXTRACTOR), error=str(exc))
        return ExitCode.EXTRACTOR
    if deps.http_client is not None:
        return _run(config, deps, log, executable, ReceiptsApi(deps.http_client))
    with httpx.Client(base_url=config.api_url, timeout=60) as client:
        return _run(config, deps, log, executable, ReceiptsApi(client))


def _run(config: Config, deps: Deps, log: Log, executable: str, api: ReceiptsApi) -> ExitCode:
    try:
        run_id = api.start_run("receipt agent")
    except (ApiUnavailable, ApiRefused) as exc:
        log("run_aborted", level="error", code=int(ExitCode.API), error=f"z-finance API: {exc}")
        return ExitCode.API

    state = _State()
    code, note = ExitCode.OK, None
    try:
        mailbox = deps.open_mailbox(config)
    except OAuthRequired as exc:
        code, note = ExitCode.OAUTH, f"mailbox needs OAuth: {exc}"
    except MailboxError as exc:
        code, note = ExitCode.MAILBOX, f"mailbox unreachable: {exc}"
    else:
        try:
            _process(mailbox, api, executable, config, deps, run_id, state, log)
        except ApiUnavailable as exc:
            code, note = ExitCode.API, f"z-finance API unreachable: {exc}"
        except ExtractorUnavailable as exc:
            code, note = ExitCode.EXTRACTOR, str(exc)
        except MailboxError as exc:
            code, note = ExitCode.MAILBOX, f"mailbox error: {exc}"
        finally:
            mailbox.close()
        _send_replies(config, deps, state, log)

    counts = {key: state.counts[key] for key in sorted(state.counts)}
    try:
        api.finish_run(run_id, counts, note)
    except (ApiUnavailable, ApiRefused) as exc:
        log("run_finish_not_recorded", level="warning", error=str(exc))
    log(
        "run_finished" if code == ExitCode.OK else "run_aborted",
        level="info" if code == ExitCode.OK else "error",
        code=int(code),
        run_id=run_id,
        counts=counts,
        note=note,
    )
    return code


def main(argv: list[str] | None = None) -> int:
    """``zfinance-agent [--env-file PATH]``: one run; the exit code tells the scheduler how it went."""
    parser = argparse.ArgumentParser(prog="zfinance-agent", description="Book receipt photos from a mailbox.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="settings file (default: ./.env)")
    args = parser.parse_args(argv)
    try:
        config = load_config(env_file=args.env_file)
    except ConfigError as exc:
        Log()("run_aborted", level="error", code=int(ExitCode.CONFIG), error=str(exc))
        return int(ExitCode.CONFIG)
    return int(run(config))


if __name__ == "__main__":
    raise SystemExit(main())
