#!/usr/bin/env python3
"""Keep the translation catalogs in step with the source (UC NF6, SPEC AK18).

The defect this guards against: a change adds a UI string or an API error message, the catalog
is never re-extracted, and the German UI shows English while every test stays green. Three checks
per side (frontend = Lingui, backend = Babel/gettext):

* **missing** — a string in the source that the committed ``messages.pot`` does not contain;
* **obsolete** — a ``messages.pot`` entry that no source string produces any more;
* **untranslated** — a ``messages.pot`` entry without a non-fuzzy translation in a ``<lang>.po``
  (NF6: a catalog without missing entries is part of the commit gate).

Unlike a static approximation, both sides run the real extractor: ``lingui extract-template`` for
the frontend (Lingui 6 has no ``extract --check``, so the committed ``.pot`` is snapshotted and
restored around the run) and Babel's ``extract_from_dir`` for the backend. Entries are compared
by (context, msgid); headers, locations and ordering are ignored, so a clean check never depends
on formatting or on ``just i18n`` having run.

Invariants:
    * A check run leaves the committed catalogs byte-identical (``--write`` is the only mode that
      writes).

Usage::

    python tools/check_pot_freshness.py             # check both sides
    python tools/check_pot_freshness.py --frontend  # the CI "lingui extract --check" step
    python tools/check_pot_freshness.py --write     # re-extract and merge into the .po files
    python tools/check_pot_freshness.py frontend/src/app/Layout.tsx  # pre-commit: sides from paths

Exit codes: 0 = catalogs current, 1 = problems found (each printed), 2 = extractor unavailable.
Tests: tools/tests/test_check_pot_freshness.py.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess  # nosec B404 - runs only the project's own lingui CLI (see _run_lingui)
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.pofile import read_po, write_po

ROOT = Path(__file__).resolve().parents[1]

#: Languages with a translation catalog; English is the source language and has none.
TRANSLATED_LANGUAGES = ("de",)

BACKEND_SRC = ROOT / "backend" / "zfinance"
BACKEND_I18N = BACKEND_SRC / "i18n"
#: The marker names documented in ``zfinance.i18n``.
BACKEND_KEYWORDS: dict[str, tuple[int, ...] | None] = {"N_": None, "gettext": None, "ngettext": (1, 2)}

FRONTEND_DIR = ROOT / "frontend"
FRONTEND_LOCALES = FRONTEND_DIR / "src" / "locales"

# (msgctxt, msgid); a plural msgid is a tuple of singular and plural.
MessageKey = tuple[str | None, Any]


@dataclass(frozen=True)
class Side:
    """One catalog family: where its template and translations live."""

    name: str
    pot: Path
    po_paths: dict[str, Path]


BACKEND = Side(
    "backend",
    BACKEND_I18N / "messages.pot",
    {lang: BACKEND_I18N / lang / "LC_MESSAGES" / "messages.po" for lang in TRANSLATED_LANGUAGES},
)
FRONTEND = Side(
    "frontend",
    FRONTEND_LOCALES / "messages.pot",
    {lang: FRONTEND_LOCALES / f"{lang}.po" for lang in TRANSLATED_LANGUAGES},
)


class ExtractorUnavailable(RuntimeError):
    """The extractor could not run (e.g. frontend dependencies not installed)."""


# --- pure checks -------------------------------------------------------------------------------


def message_keys(catalog: Catalog) -> set[MessageKey]:
    """(context, msgid) of every entry except the header; plural msgids are tuples."""
    return {(message.context, message.id) for message in catalog if message.id}


def _render(key: MessageKey) -> str:
    context, msgid = key
    text = " / ".join(msgid) if isinstance(msgid, tuple) else msgid
    return f"{text!r}" + (f" (context {context!r})" if context else "")


def _is_translated(string: str | tuple[str, ...] | list[str] | None) -> bool:
    """A plural entry counts as translated only when every plural form is filled.

    Only emptiness matters: a translation equal to the msgid ("Backup" → "Backup") is a translation
    (T-2026-CU-9130-031); the pre-commit hook `check-po` (tools/check_po.py) lets it pass.
    """
    return all(string) if isinstance(string, tuple | list) else bool(string)


def find_problems(side: str, source: Catalog, template: Catalog, translations: dict[str, Catalog]) -> list[str]:
    """Compare extracted ``source`` with the committed ``template`` and each language catalog."""
    problems: list[str] = []
    source_keys = message_keys(source)
    template_keys = message_keys(template)
    missing = sorted(source_keys - template_keys, key=str)
    obsolete = sorted(template_keys - source_keys, key=str)
    problems += [f"{side}: missing from messages.pot: {_render(k)}" for k in missing]
    problems += [f"{side}: obsolete in messages.pot: {_render(k)}" for k in obsolete]
    for language, catalog in sorted(translations.items()):
        for key in sorted(template_keys, key=str):
            context, msgid = key
            entry = catalog.get(msgid, context=context)
            if entry is None or entry.fuzzy or not _is_translated(entry.string):
                problems.append(f"{side}: untranslated in {language}.po: {_render(key)}")
    return problems


# --- extraction --------------------------------------------------------------------------------


# Lingui writes date headers with an empty value ("PO-Revision-Date: \n"), which Babel cannot
# parse. Headers are not compared, so empty ones are dropped before parsing.
_EMPTY_DATE_HEADER = re.compile(rb'^"(?:POT-Creation-Date|PO-Revision-Date):\s*\\n"\r?\n', re.MULTILINE)


def read_catalog(path: Path, locale: str | None = None) -> Catalog:
    """Read a ``.po``/``.pot`` file; a missing file reads as an empty catalog."""
    if not path.is_file():
        return Catalog(locale=locale)
    content = _EMPTY_DATE_HEADER.sub(b"", path.read_bytes())
    return read_po(io.BytesIO(content), locale=locale)


def catalog_header() -> dict[str, Any]:
    """Header of written backend catalogs (Babel's defaults are placeholders like "PROJECT").

    Only ``--write`` needs it: checks ignore headers, and the pre-commit hook runs in an
    environment where the z-finance package itself is not installed.
    """
    return {
        "project": "z-finance",
        "version": version("z-finance"),
        "copyright_holder": "z-finance contributors",
        "msgid_bugs_address": "https://github.com/ziagl888/z-finance/issues",
        "last_translator": "z-finance contributors",
        "header_comment": "# z-finance backend messages (API errors).\n# Licensed under the Apache License 2.0.\n#",
    }


def extract_backend(src_dir: Path = BACKEND_SRC, **header: Any) -> Catalog:
    """Extract the marked strings from the backend package's Python files (``header``: Catalog kwargs)."""
    catalog = Catalog(fuzzy=False, **header)
    for filename, lineno, message, comments, context in extract_from_dir(
        src_dir, method_map=[("**.py", "python")], keywords=BACKEND_KEYWORDS
    ):
        catalog.add(message, None, [(filename.replace("\\", "/"), lineno)], auto_comments=comments, context=context)
    return catalog


def _find_lingui(frontend_dir: Path) -> str | None:
    return shutil.which("lingui", path=str(frontend_dir / "node_modules" / ".bin"))


def _lingui(frontend_dir: Path) -> str:
    executable = _find_lingui(frontend_dir)
    if executable is None:
        raise ExtractorUnavailable("lingui CLI not found; run `npm ci` in frontend/ first")
    return executable


def _run_lingui(frontend_dir: Path, *args: str) -> None:
    # Fixed argv, no shell; the executable is the project's own node_modules/.bin/lingui.
    result = subprocess.run(  # noqa: S603  # nosec B603
        [_lingui(frontend_dir), *args], cwd=frontend_dir, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ExtractorUnavailable(f"lingui {' '.join(args)} failed:\n{result.stdout}{result.stderr}")


def extract_frontend(frontend_dir: Path = FRONTEND_DIR, pot: Path = FRONTEND.pot) -> Catalog:
    """Run ``lingui extract-template`` and return its result, restoring the committed ``.pot``."""
    committed = pot.read_bytes() if pot.is_file() else None
    try:
        _run_lingui(frontend_dir, "extract-template")
        return read_catalog(pot)
    finally:
        if committed is None:
            pot.unlink(missing_ok=True)
        else:
            pot.write_bytes(committed)


def check_side(side: Side, source: Catalog) -> list[str]:
    """Problems of one side against its committed catalogs."""
    translations = {lang: read_catalog(path, locale=lang) for lang, path in side.po_paths.items()}
    return find_problems(side.name, source, read_catalog(side.pot), translations)


# --- write mode --------------------------------------------------------------------------------


def _write(path: Path, catalog: Catalog) -> None:
    buffer = io.BytesIO()
    # Sorted, no line numbers, 78 columns: the layout the catalogs keep.
    write_po(buffer, catalog, width=78, sort_output=True, include_lineno=False, ignore_obsolete=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())


def write_backend(side: Side = BACKEND, src_dir: Path = BACKEND_SRC) -> None:
    """Re-extract the backend template and merge it into each language catalog."""
    header = catalog_header()
    template = extract_backend(src_dir, **header)
    _write(side.pot, template)
    for language, path in side.po_paths.items():
        if path.is_file():
            catalog = read_catalog(path, locale=language)
        else:
            catalog = Catalog(
                locale=language, fuzzy=False, language_team=language, revision_date=datetime.now(UTC), **header
            )
        catalog.update(template, no_fuzzy_matching=True)
        _write(path, catalog)


def write_frontend(frontend_dir: Path = FRONTEND_DIR) -> None:
    """Re-extract the frontend template and each language catalog (obsolete entries dropped)."""
    _run_lingui(frontend_dir, "extract-template")
    for language in TRANSLATED_LANGUAGES:
        _run_lingui(frontend_dir, "extract", "--locale", language, "--clean")


# --- CLI ---------------------------------------------------------------------------------------


def sides_for(paths: Iterable[str]) -> tuple[bool, bool]:
    """(frontend, backend) — which sides the given changed paths can affect."""
    normalized = [p.replace("\\", "/") for p in paths]
    return (
        any(p.startswith("frontend/") for p in normalized),
        any(p.startswith("backend/") for p in normalized),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point: check the catalogs (0 = fresh, 1 = stale) or, with ``--write``, update them."""
    parser = argparse.ArgumentParser(description="Check (or --write) the translation catalogs against the source.")
    parser.add_argument("files", nargs="*", help="changed files (pre-commit); selects the sides to check")
    parser.add_argument("--frontend", action="store_true", help="only the frontend (Lingui) catalogs")
    parser.add_argument("--backend", action="store_true", help="only the backend (gettext) catalogs")
    parser.add_argument("--write", action="store_true", help="re-extract and merge instead of checking")
    parser.add_argument(
        "--frontend-optional",
        action="store_true",
        help="skip the frontend side when its dependencies are not installed (pre-commit in CI runs "
        "before `npm ci`; the CI i18n step checks it afterwards)",
    )
    args = parser.parse_args(argv)

    if args.frontend or args.backend:
        frontend, backend = args.frontend, args.backend
    elif args.files:
        frontend, backend = sides_for(args.files)
    else:
        frontend = backend = True
    if frontend and args.frontend_optional and _find_lingui(FRONTEND_DIR) is None:
        print("frontend catalogs not checked: frontend dependencies not installed (`npm ci`)", file=sys.stderr)
        frontend = False

    try:
        if args.write:
            if frontend:
                write_frontend()
            if backend:
                write_backend()
            print("catalogs re-extracted; translate the new empty msgstr entries in the .po files")
            return 0
        problems: list[str] = []
        if frontend:
            problems += check_side(FRONTEND, extract_frontend())
        if backend:
            problems += check_side(BACKEND, extract_backend())
    except ExtractorUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2

    for problem in problems:
        print(problem)
    if problems:
        print("fix: `just i18n` (re-extract), then translate the empty entries in the .po files", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
