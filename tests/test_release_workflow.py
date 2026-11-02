"""The "Version from the tag" step of the release workflow must run on a private repo (T-2026-HAR-9130-023).

The checkout keeps no credential (`persist-credentials: false`). A plain `git fetch` in that step
asked for a password and the first tagged build after the main-ancestry check failed. These tests
pin the step to what works without a credential.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def version_step() -> str:
    text = workflow_text()
    start = text.index("- name: Version from the tag")
    return text[start : text.index("- name:", start + 10)]


def script_lines(step: str) -> list[str]:
    """The step's script without comment lines, so a comment cannot hide or fake a command."""
    body = step[step.index("run: |") :]
    return [line.strip() for line in body.splitlines()[1:] if not line.strip().startswith("#")]


def test_version_step_runs_no_git_command_that_talks_to_the_remote() -> None:
    """Oracle: measured — run 41446555275 failed at `git fetch --no-tags origin main` with no credential."""
    remote_commands = re.compile(r"\bgit\s+(fetch|pull|ls-remote|clone|push)\b")
    assert [line for line in script_lines(version_step()) if remote_commands.search(line)] == []


def test_version_step_forbids_a_git_credential_prompt() -> None:
    """Oracle: spec-anchored — a missing credential must fail loudly, never hang on a prompt."""
    assert re.search(r'^\s+GIT_TERMINAL_PROMPT:\s*"0"\s*$', version_step(), re.MULTILINE)


def test_version_step_keeps_the_ancestor_check_against_the_checkout() -> None:
    """Oracle: spec-anchored — the tagged commit must sit on main, judged from the cloned history."""
    lines = script_lines(version_step())
    assert any("git merge-base --is-ancestor $env:COMMIT_SHA origin/main" in line for line in lines)
    assert any("rev-parse --verify --quiet refs/remotes/origin/main" in line for line in lines)


def test_checkout_clones_every_branch_and_keeps_no_credential() -> None:
    """Oracle: spec-anchored — origin/main is in the clone (depth 0) and the safer setting stays."""
    text = workflow_text()
    checkout = text[text.index("actions/checkout@") :]
    checkout = checkout[: checkout.index("- uses: astral-sh/setup-uv")]
    assert "fetch-depth: 0" in checkout
    assert "persist-credentials: false" in checkout


def test_version_step_takes_untrusted_text_only_through_env() -> None:
    """Oracle: spec-anchored — the tag name never appears in a `${{ }}` inside the script text."""
    assert "${{" not in "\n".join(script_lines(version_step()))
