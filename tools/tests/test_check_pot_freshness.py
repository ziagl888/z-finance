"""SPEC-T-2026-CU-9130-004 AK2: the catalog freshness guard (tools/check_pot_freshness.py)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po

import check_pot_freshness as tool

HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=utf-8\\n"\n\n'


def po(body: str, locale: str | None = None) -> Catalog:
    """A catalog from PO text (header added)."""
    return read_po(io.BytesIO((HEADER + body).encode()), locale=locale)


TEMPLATE = po('msgid "Save"\nmsgstr ""\n\nmsgid "Cancel"\nmsgstr ""\n')
GERMAN = po('msgid "Save"\nmsgstr "Speichern"\n\nmsgid "Cancel"\nmsgstr "Abbrechen"\n', "de")


def test_clean_catalogs_have_no_problems() -> None:
    """Oracle: spec-anchored — AK2: source == template and every entry translated → no problem."""
    assert tool.find_problems("frontend", TEMPLATE, TEMPLATE, {"de": GERMAN}) == []


def test_source_string_missing_from_template() -> None:
    """Oracle: spec-anchored — AK2a: a source string the .pot lacks is reported by its text."""
    source = po('msgid "Save"\nmsgstr ""\n\nmsgid "Cancel"\nmsgstr ""\n\nmsgid "Delete"\nmsgstr ""\n')

    problems = tool.find_problems("frontend", source, TEMPLATE, {"de": GERMAN})

    assert problems == ["frontend: missing from messages.pot: 'Delete'"]


def test_template_entry_without_source_is_obsolete() -> None:
    """Oracle: spec-anchored — AK2b: a .pot entry no source string produces is reported."""
    source = po('msgid "Save"\nmsgstr ""\n')

    problems = tool.find_problems("backend", source, TEMPLATE, {"de": GERMAN})

    assert problems == ["backend: obsolete in messages.pot: 'Cancel'"]


@pytest.mark.parametrize(
    "german_cancel",
    [
        'msgid "Cancel"\nmsgstr ""\n',  # empty translation
        '#, fuzzy\nmsgid "Cancel"\nmsgstr "Abbrechen"\n',  # unreviewed
        "",  # entry absent
    ],
    ids=["empty", "fuzzy", "absent"],
)
def test_untranslated_entry_is_reported(german_cancel: str) -> None:
    """Oracle: spec-anchored — AK2c / NF6 "Katalog ohne fehlende Einträge": empty, fuzzy or absent → reported."""
    german = po('msgid "Save"\nmsgstr "Speichern"\n\n' + german_cancel, "de")

    problems = tool.find_problems("frontend", TEMPLATE, TEMPLATE, {"de": german})

    assert problems == ["frontend: untranslated in de.po: 'Cancel'"]


def test_translation_identical_to_the_msgid_counts_as_translated() -> None:
    """Oracle: spec-anchored — ticket T-2026-CU-9130-031: German "Backup" is a real translation of "Backup"."""
    template = po('msgid "Backup"\nmsgstr ""\n')
    german = po('msgid "Backup"\nmsgstr "Backup"\n', "de")

    assert tool.find_problems("frontend", template, template, {"de": german}) == []


def test_plural_needs_every_form() -> None:
    """Oracle: spec-anchored — AK2c: a plural entry with one empty form is untranslated."""
    template = po('msgid "%(n)s file"\nmsgid_plural "%(n)s files"\nmsgstr[0] ""\nmsgstr[1] ""\n')
    german = po('msgid "%(n)s file"\nmsgid_plural "%(n)s files"\nmsgstr[0] "%(n)s Datei"\nmsgstr[1] ""\n', "de")

    problems = tool.find_problems("backend", template, template, {"de": german})

    assert problems == ["backend: untranslated in de.po: '%(n)s file / %(n)s files'"]


def test_same_text_in_other_context_is_a_different_message() -> None:
    """Oracle: spec-anchored — gettext semantics: msgctxt is part of the message key."""
    source = po('msgctxt "verb"\nmsgid "Save"\nmsgstr ""\n\nmsgid "Cancel"\nmsgstr ""\n')

    problems = tool.find_problems("frontend", source, TEMPLATE, {})

    assert problems == [
        "frontend: missing from messages.pot: 'Save' (context 'verb')",
        "frontend: obsolete in messages.pot: 'Save'",
    ]


def test_backend_extraction_finds_marked_strings_only(tmp_path: Path) -> None:
    """Oracle: spec-anchored — zfinance.i18n docstring: N_ and gettext are the markers; plain strings are not."""
    (tmp_path / "feature.py").write_text(
        "from zfinance.i18n import N_, gettext\n"
        'A = N_("Account not found")\n'
        'B = gettext("Saved", "de")\n'
        'C = "not marked"\n',
        encoding="utf-8",
    )

    catalog = tool.extract_backend(tmp_path)

    assert tool.message_keys(catalog) == {(None, "Account not found"), (None, "Saved")}


def test_frontend_check_restores_committed_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — AK2 / module invariant: a check run leaves the committed .pot byte-identical."""
    pot = tmp_path / "messages.pot"
    committed = (HEADER + 'msgid "Old"\nmsgstr ""\n').encode()
    pot.write_bytes(committed)

    def fake_extract(_frontend_dir: Path, *args: str) -> None:
        assert args == ("extract-template",)
        pot.write_text(HEADER + 'msgid "New"\nmsgstr ""\n', encoding="utf-8")

    monkeypatch.setattr(tool, "_run_lingui", fake_extract)

    extracted = tool.extract_frontend(tmp_path, pot)

    assert tool.message_keys(extracted) == {(None, "New")}
    assert pot.read_bytes() == committed


def test_frontend_check_restores_even_when_extraction_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — module invariant holds on the error path; exit code 2 = extractor unavailable."""
    pot = tmp_path / "messages.pot"
    pot.write_bytes(b"committed")

    def broken_extract(_frontend_dir: Path, *_args: str) -> None:
        pot.write_bytes(b"half-written")
        raise tool.ExtractorUnavailable("lingui failed")

    monkeypatch.setattr(tool, "_run_lingui", broken_extract)

    with pytest.raises(tool.ExtractorUnavailable):
        tool.extract_frontend(tmp_path, pot)
    assert pot.read_bytes() == b"committed"


def test_unavailable_extractor_exits_2(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Oracle: spec-anchored — module docstring: exit 2 = extractor unavailable, reason on stderr."""

    def missing(_frontend_dir: Path) -> str:
        raise tool.ExtractorUnavailable("lingui CLI not found; run `npm ci` in frontend/ first")

    monkeypatch.setattr(tool, "_lingui", missing)

    assert tool.main(["--frontend"]) == 2
    assert "npm ci" in capsys.readouterr().err


def test_frontend_optional_skips_only_missing_dependencies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Oracle: spec-anchored — --frontend-optional help text: without `npm ci` the frontend side is skipped, loudly."""
    monkeypatch.setattr(tool, "_find_lingui", lambda _frontend_dir: None)

    def must_not_run(*_args: object) -> None:
        raise AssertionError("frontend extraction must be skipped")

    monkeypatch.setattr(tool, "extract_frontend", must_not_run)

    assert tool.main(["--frontend", "--frontend-optional"]) == 0
    assert "not checked" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["frontend/src/app/Layout.tsx"], (True, False)),
        (["backend\\zfinance\\i18n\\api.py"], (False, True)),
        (["frontend/src/locales/de.po", "backend/zfinance/app.py"], (True, True)),
        (["README.md"], (False, False)),
    ],
)
def test_changed_paths_select_sides(paths: list[str], expected: tuple[bool, bool]) -> None:
    """Oracle: spec-anchored — module docstring "pre-commit: sides from paths" (POSIX and Windows separators)."""
    assert tool.sides_for(paths) == expected


def test_lingui_empty_date_headers_are_readable(tmp_path: Path) -> None:
    """Oracle: state-anchored — captured `lingui extract-template` 6.7 header with empty date values."""
    pot = tmp_path / "messages.pot"
    pot.write_text(
        'msgid ""\nmsgstr ""\n"POT-Creation-Date: \\n"\n"PO-Revision-Date: \\n"\n"X-Generator: @lingui/cli\\n"\n\n'
        'msgid "Save"\nmsgstr ""\n',
        encoding="utf-8",
    )

    assert tool.message_keys(tool.read_catalog(pot)) == {(None, "Save")}


def test_repository_backend_catalogs_are_current() -> None:
    """Oracle: spec-anchored — AK3/AK2 on HEAD: the committed backend catalogs match the source."""
    assert tool.main(["--backend"]) == 0


@pytest.mark.skipif(
    not (tool.FRONTEND_DIR / "node_modules" / ".bin").is_dir(), reason="frontend dependencies not installed"
)
def test_repository_frontend_catalogs_are_current() -> None:
    """Oracle: spec-anchored — AK1/AK2 on HEAD: frontend catalogs current, messages.pot left byte-identical."""
    before = tool.FRONTEND.pot.read_bytes()

    assert tool.main(["--frontend"]) == 0
    assert tool.FRONTEND.pot.read_bytes() == before
