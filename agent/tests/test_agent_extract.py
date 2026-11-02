"""Receipt agent parts without the API: the claude call, the schema, config and sender checks.

Expected values come from docs/specs/SPEC-T-2026-CU-9130-020.md and the orchestrator notes of
wave 6. All addresses and photos are synthetic; ``claude`` is never started.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from agent_fakes import OWNER, FakeClaude, envelope, extraction, jpeg, make_config
from zfinance_agent import extract
from zfinance_agent.config import ConfigError, load_config
from zfinance_agent.extract import SCHEMA, Extraction, parse_output
from zfinance_agent.mailbox import sender_of
from zfinance_agent.run import ExitCode, main


def test_claude_invocation_is_bounded() -> None:
    """One call: argument list with fixed prompt and schema, only the Read tool, budget, timeout,
    the photo alone in a temp directory that is gone afterwards (AK7).

    Oracle: spec-anchored — orchestrator note "Subprozess mit Argument-Liste (kein shell=True),
    Prompt fest im Code, Anhang als Datei-Pfad in ein temporäres Verzeichnis, danach löschen";
    flags from `claude --help` 2.1.278.
    """
    claude = FakeClaude()
    config = make_config(claude_timeout=42.0, claude_max_budget_usd=0.25, claude_model="sonnet")
    outcome = extract.extract("/fake/claude", jpeg("250"), "jpg", config, claude)

    assert outcome.extraction is not None and outcome.extraction.amount_cents == 250
    [(args, cwd, timeout)] = claude.calls
    assert timeout == 42.0
    assert not cwd.exists()
    assert claude.photos_seen == [jpeg("250")]
    assert args[:3] == ["/fake/claude", "-p", extract.PROMPT.format(file="receipt.jpg")]
    options = dict(zip(args[3::2], args[4::2], strict=False))
    assert options["--output-format"] == "json"
    assert json.loads(options["--json-schema"]) == SCHEMA
    assert options["--tools"] == "Read" and options["--allowedTools"] == "Read"
    assert options["--max-budget-usd"] == "0.25" and options["--model"] == "sonnet"
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args


def test_extraction_schema_rejects_bad_output() -> None:
    """Extra fields, wrong types, amounts out of range, an error envelope → rejected (AK8).

    Oracle: spec-anchored — orchestrator note "Ausgabe gegen ein festes JSON-Schema validieren
    (pydantic)"; limits from the API (`merchant` ≤ 200, `hints` ≤ 1000) in -016.
    """
    assert parse_output(envelope(extraction())).amount_cents == 1_234
    bad = [
        extraction(path="../../etc"),
        extraction(amount_cents="1234"),
        extraction(amount_cents=0),
        extraction(amount_cents=100_000_001),
        extraction(confidence=1.5),
        extraction(currency="euro"),
        extraction(merchant="x" * 201),
        extraction(payment_method="crypto"),
        extraction(line_items=[{"text": "x"}] * 51),
    ]
    for structured in bad:
        with pytest.raises(extract.AttemptFailed):
            parse_output(envelope(structured))
    with pytest.raises(extract.AttemptFailed):
        parse_output(json.dumps({"is_error": True, "structured_output": extraction()}))
    assert set(SCHEMA["properties"]) == set(Extraction.model_fields)


def test_timeout_and_exit_code_fail_the_attempt() -> None:
    """Timeout or a non-zero exit → failed attempt, retried once, then no extraction (AK7).

    Oracle: spec-anchored — orchestrator note "claude -p mit Timeout, genau 1 Retry".
    """

    calls: list[int] = []

    def failing(args: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args, timeout)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    outcome = extract.extract("/fake/claude", jpeg("x"), "jpg", make_config(), failing)
    assert outcome.extraction is None and outcome.attempts == 2 and len(calls) == 2


def test_claude_must_be_an_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing binary or a .cmd script → ExtractorUnavailable (AK7).

    Oracle: spec-anchored — SPEC-T-2026-CU-9130-020 "resolved with shutil.which, not a .cmd/.bat".
    """
    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    with pytest.raises(extract.ExtractorUnavailable):
        extract.resolve_claude("claude")
    monkeypatch.setattr(extract.shutil, "which", lambda name: str(tmp_path / "claude.cmd"))
    with pytest.raises(extract.ExtractorUnavailable):
        extract.resolve_claude("claude")


def test_sender_normalisation() -> None:
    """Exactly one address, compared lower-cased; groups and several addresses are refused (AK5).

    Oracle: spec-anchored — orchestrator note "Vergleich auf die normalisierte Adresse".
    """
    assert sender_of(f"Owner <{OWNER.upper()}>") == OWNER
    assert sender_of(f"  {OWNER} ") == OWNER
    assert sender_of(f"{OWNER}, other@example.test") is None
    assert sender_of("undisclosed-recipients:;") is None
    assert sender_of("") is None


def test_missing_config_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing variable → exit 2 naming it; an env file fills gaps, the environment wins (AK10).

    Oracle: spec-anchored — SPEC-T-2026-CU-9130-020 exit codes; orchestrator note "Zugangsdaten NUR
    aus Umgebung/.env ... nie im Log".
    """
    for name in list(os.environ):
        if name.startswith("ZF_AGENT_"):
            monkeypatch.delenv(name)
    env = tmp_path / ".env"
    env.write_text(
        "ZF_AGENT_IMAP_HOST=imap.example.test\nZF_AGENT_IMAP_USER=receipts@example.test\n"
        'ZF_AGENT_IMAP_PASSWORD="placeholder-secret"\nZF_AGENT_SUBJECT_SECRET= Sesame42 \n',
        encoding="utf-8",
    )
    assert main(["--env-file", str(env)]) == ExitCode.CONFIG
    err = capsys.readouterr().err
    assert "ZF_AGENT_ALLOWED_SENDERS" in err and "placeholder-secret" not in err and "Sesame42" not in err

    config = load_config({"ZF_AGENT_ALLOWED_SENDERS": f" {OWNER.upper()} ,", "ZF_AGENT_IMAP_HOST": "other"}, env)
    assert config.subject_secret == "Sesame42"
    assert config.allowed_senders == frozenset({OWNER})
    assert config.imap_host == "other" and config.imap_password == "placeholder-secret"
    assert config.smtp_password == "placeholder-secret" and config.max_receipts == 10
    with pytest.raises(ConfigError, match="MAX_RECEIPTS"):
        load_config({"ZF_AGENT_ALLOWED_SENDERS": OWNER, "ZF_AGENT_MAX_RECEIPTS": "0"}, env)
