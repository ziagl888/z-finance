"""T-2026-HAR-9130-028: the project's own .po/.pot check, which replaced the third-party hook.

Covers tools/check_po.py (syntax, duplicate msgids, placeholders, identical translations allowed,
files never changed) and its wiring in .pre-commit-config.yaml.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

import check_po as tool

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = [
    "backend/zfinance/i18n/messages.pot",
    "backend/zfinance/i18n/de/LC_MESSAGES/messages.po",
    "frontend/src/locales/messages.pot",
    "frontend/src/locales/de.po",
]
HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=utf-8\\n"\n\n'


def _problems(body: str) -> list[str]:
    return [message for _, message in tool.check_text(HEADER + body)]


def test_the_four_catalogs_pass_and_stay_byte_for_byte_unchanged() -> None:
    """Oracle: spec-anchored — task HAR-9130-028 (2): "keep the current catalogs passing unchanged"."""
    paths = [ROOT / relative for relative in CATALOGS]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]

    assert tool.main([str(path) for path in paths]) == tool.EXIT_CLEAN

    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths] == before
    assert len(tool.parse(paths[3].read_text(encoding="utf-8"))) > 100


def test_an_identical_translation_passes() -> None:
    """Oracle: spec-anchored — task HAR-9130-028 (2): identical translations ALLOWED ("Backup", "Name")."""
    assert _problems('msgid "Backup"\nmsgstr "Backup"\n\nmsgid "Name"\nmsgstr "Name"\n') == []


def test_a_duplicate_msgid_is_reported_with_both_lines() -> None:
    """Oracle: spec-anchored — task HAR-9130-028 (2): duplicate msgids; also with a different msgctxt."""
    problems = tool.check_text(HEADER + 'msgid "Save"\nmsgstr "Speichern"\n\nmsgctxt "x"\nmsgid "Save"\nmsgstr "S"\n')

    assert len(problems) == 1
    line, message = problems[0]
    assert line > 5
    assert "duplicate msgid 'Save'" in message
    assert "first at line 5" in message


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('msgid "Hi {name}"\nmsgstr "Hallo"\n', "changes the placeholders (expected: name)"),
        ('msgid "Hi"\nmsgstr "Hallo {name}"\n', "changes the placeholders (expected: none)"),
        ('#, python-format\nmsgid "%(count)s rows"\nmsgstr "Zeilen"\n', "expected: %(count)s"),
        ('#, c-format\nmsgid "%d rows"\nmsgstr "%s Zeilen"\n', "expected: %d"),
        (
            'msgid "{n} item"\nmsgid_plural "{n} items"\nmsgstr[0] "{n} Eintrag"\nmsgstr[1] "Einträge"\n',
            "msgstr[1]",
        ),
    ],
)
def test_a_lost_or_added_placeholder_is_reported(body: str, expected: str) -> None:
    """Oracle: spec-anchored — task HAR-9130-028 (2): printf/format/{named} placeholders kept in msgstr."""
    problems = _problems(body)

    assert len(problems) == 1
    assert expected in problems[0]


@pytest.mark.parametrize(
    "body",
    [
        'msgid "Hi {name}"\nmsgstr ""\n',  # untranslated: the freshness gate reports it, not this one
        'msgid "{a} and {b}"\nmsgstr "{b} und {a}"\n',  # reordered
        '#, python-format\nmsgid "100 % of the income and %%"\nmsgstr "100 % der Einnahme und %%"\n',
        'msgid "%(n)s without the flag"\nmsgstr "ohne Flag"\n',
    ],
)
def test_placeholders_that_are_kept_or_not_placeholders_pass(body: str) -> None:
    """Oracle: spec-anchored — tools/check_po.py rules: printf only with a format flag, empty msgstr skipped."""
    assert _problems(body) == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('msgid "Hi"\n', "without msgstr"),
        ('msgctxt "menu"\n', "without msgid"),
        ('msgid "Hi"\nmsgstr "Hallo\n', "not a quoted string"),
        ('msgid "Hi"\nmsgstr "Ha"llo"\n', "not a quoted string"),
        ('msgid "Hi"\nmsgstr "Hal\\qlo"\n', "unknown escape sequence"),
        ('msgid "Hi"\nmsgstr "Hallo"\nmsgstr "Servus"\n', "given twice"),
        ('msgid "Hi"\nmsgtxt "Hallo"\n', "unknown line"),
        ('msgid "Hi"\nmsgstr[0] "Hallo"\n', "msgid_plural needs msgstr[n]"),
        ('msgid "a"\nmsgid_plural "b"\nmsgstr "c"\n', "msgid_plural needs msgstr[n]"),
    ],
)
def test_a_syntax_error_is_reported_with_its_line(body: str, expected: str) -> None:
    """Oracle: spec-anchored — task HAR-9130-028 (2): syntax; gettext's PO grammar (keyword + quoted string)."""
    problems = tool.check_text(HEADER + body)

    assert len(problems) == 1
    line, message = problems[0]
    assert line >= 5
    assert expected in message


def test_continuation_lines_obsolete_entries_and_plurals_are_read() -> None:
    """Oracle: spec-anchored — gettext PO format: continuation strings join, ``#~`` is a comment."""
    body = (
        'msgid ""\n"A long "\n"text"\nmsgstr ""\n"Ein langer "\n"Text"\n\n'
        '#~ msgid "Old"\n#~ msgstr "Alt"\n\n'
        'msgid "{n} item"\nmsgid_plural "{n} items"\nmsgstr[0] "{n} Eintrag"\nmsgstr[1] "{n} Einträge"\n'
    )
    entries = tool.parse(HEADER + body)

    assert [entry.msgid for entry in entries] == ["", "A long text", "{n} item"]
    assert entries[1].msgstr == {0: "Ein langer Text"}
    assert entries[2].msgstr == {0: "{n} Eintrag", 1: "{n} Einträge"}
    assert tool.check_text(HEADER + body) == []


def test_main_prints_each_problem_with_path_and_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Oracle: spec-anchored — tools/check_po.py exit codes: 1 and ``path:line: text`` per problem."""
    bad = tmp_path / "de.po"
    bad.write_text(HEADER + 'msgid "Hi {name}"\nmsgstr "Hallo"\n', encoding="utf-8")
    unreadable = tmp_path / "latin.po"
    unreadable.write_bytes(b'msgid "\xe4"\nmsgstr ""\n')

    assert tool.main([str(bad), str(unreadable)]) == tool.EXIT_PROBLEMS

    err = capsys.readouterr().err
    assert f"{bad}:5:" in err
    assert f"{unreadable}:0: cannot be read as UTF-8" in err


def test_pre_commit_runs_the_own_check_without_fix_or_third_party_package() -> None:
    """Oracle: spec-anchored — task HAR-9130-028 (2): the third-party hook is replaced by tools/check_po.py."""
    config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    hook = re.search(r"- id: check-po\n(?P<body>(?:\s{8}.*\n)+)", config)

    assert hook, "hook check-po missing from .pre-commit-config.yaml"
    assert "entry: python tools/check_po.py" in hook["body"]
    assert "additional_dependencies" not in hook["body"]
    assert "--fix" not in hook["body"]
    assert "oca_checks_po" not in config
    assert "oca-checks-po" not in config
    assert not (ROOT / "tools" / "oca_checks_po.py").exists()
