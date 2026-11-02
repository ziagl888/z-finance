"""Commit-subject gate: the subject line must be a Conventional Commit.

Accepted form: ``type(scope): message`` with the scope optional and an optional ``!`` for a
breaking change, e.g. ``feat(accounts): opening balance`` or ``docs: fix a typo``.

Usage:
    check_commit_subject.py <commit-msg-file>     pre-commit hook, stage commit-msg
    check_commit_subject.py --text "<subject>"    check a string, e.g. a pull-request title in CI

Git-generated subjects (merge commits, ``Revert "..."``) and autosquash markers
(``fixup!``/``squash!``/``amend!``) are exempt. Exit status 0 = valid, 1 = invalid, 2 = usage.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TYPES = ("feat", "fix", "docs", "chore", "refactor", "test", "ci", "build", "perf", "style", "revert")

SUBJECT_RE = re.compile(rf"^(?:{'|'.join(TYPES)})(?:\([a-z0-9][a-z0-9._/-]*\))?!?: \S")

EXEMPT_PREFIXES = ("Merge ", 'Revert "', "fixup! ", "squash! ", "amend! ")


def subject_of(message: str) -> str:
    """Return the first line that is neither empty nor a git comment, without trailing whitespace."""
    for line in message.splitlines():
        if line.strip() and not line.startswith("#"):
            return line.rstrip()
    return ""


def subject_errors(subject: str) -> list[str]:
    """Return why ``subject`` is not acceptable; an empty list means it passes."""
    if subject.startswith(EXEMPT_PREFIXES):
        return []
    if SUBJECT_RE.match(subject):
        return []
    return [
        f"invalid commit subject: {subject!r}",
        "expected: type(scope): message   (scope optional, e.g. 'feat(accounts): add reserve floor')",
        f"types: {', '.join(TYPES)}",
    ]


def main(argv: list[str]) -> int:
    """Command line entry point: ``--text SUBJECT`` or the path of a commit message file; 0 = valid."""
    if len(argv) == 2 and argv[0] == "--text":
        subject = subject_of(argv[1])
    elif len(argv) == 1 and not argv[0].startswith("-"):
        subject = subject_of(Path(argv[0]).read_text(encoding="utf-8"))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    errors = subject_errors(subject)
    for line in errors:
        print(line, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
