"""Commit-subject gate: conventional commits with an optional scope (SPEC "Repo & Engineering-Konventionen")."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from check_commit_subject import TYPES, main, subject_errors, subject_of

SCRIPT = Path(__file__).resolve().parents[1] / "check_commit_subject.py"


@pytest.mark.parametrize(
    "subject",
    [
        "feat(scaffold): repo skeleton — FastAPI/SQLite migrations, Vite SPA, justfile (T-2026-CU-9130-002)",
        "docs: v1.2 neutralize personal statements in use cases before going public",
        "fix(import-csv): accept semicolon delimiter",
        "refactor(api/health)!: rename schema field",
        "ci: add pre-commit job",
    ],
)
def test_conventional_subjects_pass(subject: str) -> None:
    """Oracle: spec-anchored — task -003 "type(scope): msg, Scope optional"; first two are real commits on main."""
    assert subject_errors(subject) == []


@pytest.mark.parametrize("commit_type", TYPES)
def test_every_listed_type_is_accepted(commit_type: str) -> None:
    """Oracle: spec-anchored — task -003 type list feat/fix/docs/chore/refactor/test/ci/build/perf/style/revert."""
    assert subject_errors(f"{commit_type}: message") == []


@pytest.mark.parametrize(
    "subject",
    [
        "[FEAT] P4934 promote module",  # a bracketed type and ticket number is not this repo's format
        "Feat(accounts): capitalised type",
        "feature(accounts): unknown type",
        "feat(accounts) missing colon",
        "feat(accounts):missing space",
        "feat(): empty scope",
        "feat(Accounts): uppercase scope",
        "update stuff",
        "",
    ],
)
def test_non_conventional_subjects_fail(subject: str) -> None:
    """Oracle: spec-anchored — Conventional Commits 1.0 grammar as restricted by task -003."""
    assert subject_errors(subject) != []


@pytest.mark.parametrize(
    "subject",
    ["Merge branch 'main' into feat/x", 'Revert "feat(x): y"', "fixup! feat(x): y", "squash! docs: z"],
)
def test_git_generated_subjects_are_exempt(subject: str) -> None:
    """Oracle: spec-anchored — git's own merge/revert subjects and autosquash markers cannot be chosen freely."""
    assert subject_errors(subject) == []


def test_subject_skips_comments_and_blank_lines() -> None:
    """Oracle: spec-anchored — git strips '#' lines from COMMIT_EDITMSG; the subject is the first real line."""
    assert subject_of("\n# Please enter the commit message\n\ndocs: x\n\nbody\n") == "docs: x"


def test_commit_msg_file_blocks_bad_subject(tmp_path: Path) -> None:
    """Oracle: spec-anchored — hook contract: commit-msg file in argv[1], non-zero exit blocks the commit."""
    good = tmp_path / "good"
    good.write_text("feat(x): y\n\nbody\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.write_text("wip\n", encoding="utf-8")
    run = [sys.executable, str(SCRIPT)]
    assert subprocess.run([*run, str(good)], check=False).returncode == 0  # noqa: S603
    result = subprocess.run([*run, str(bad)], check=False, capture_output=True, text=True)  # noqa: S603
    assert result.returncode == 1
    assert "invalid commit subject: 'wip'" in result.stderr


def test_text_mode_checks_a_pull_request_title() -> None:
    """Oracle: spec-anchored — CI checks the PR title via --text; exit 0 valid, 1 invalid, 2 usage."""
    assert main(["--text", "feat(ci): gates"]) == 0
    assert main(["--text", "Gates"]) == 1
    assert main([]) == 2
