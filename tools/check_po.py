#!/usr/bin/env python3
"""Check gettext catalogs (.po/.pot) without changing them: syntax, duplicate msgids, placeholders.

    python tools/check_po.py frontend/src/locales/de.po backend/zfinance/i18n/messages.pot

Rules:
    * syntax: every line is blank, a comment, a keyword (``msgctxt``, ``msgid``, ``msgid_plural``,
      ``msgstr``, ``msgstr[n]``) with a quoted string, or a quoted continuation; every entry has a
      ``msgid`` and its ``msgstr`` (``msgstr[n]`` after a ``msgid_plural``); the file is UTF-8.
    * duplicates: a ``msgid`` appears once per file, whatever its ``msgctxt``.
    * placeholders: a non-empty translation keeps the ``{name}`` placeholders of its source text, and,
      in an entry flagged ``python-format`` or ``c-format``, its printf placeholders (``%s``, ``%(name)s``).

A translation equal to its source text ("Backup" -> "Backup") is a translation and passes. Obsolete
entries (``#~``) are comments. The files are only read: layout and order stay as the extractors wrote
them. Exit codes: 0 every file passes, 1 a file has a problem (each one printed as ``path:line: text``).
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

EXIT_CLEAN = 0
EXIT_PROBLEMS = 1

KEYWORD = re.compile(r'^(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+(".*)$')
QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"$')
VALID_ESCAPE = re.compile(r'\\(?:[\\"abfnrtv]|[0-7]{1,3}|x[0-9A-Fa-f]+)')
BRACE_PLACEHOLDER = re.compile(r"\{(\w+)\}")
# No space flag: Babel flags "100 % of the income" as python-format, and "% o" is prose there.
PRINTF_PLACEHOLDER = re.compile(r"%(?:\([^)]+\))?[-#0+]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[diouxXeEfFgGcrsa]")
PRINTF_FLAGS = frozenset({"python-format", "c-format"})


class PoSyntaxError(ValueError):
    """A line of the catalog that gettext cannot read."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"{line}: {message}")
        self.line = line
        self.message = message


@dataclass
class Entry:
    """One catalog entry; ``msgstr`` maps the plural index (0 without a plural) to its text."""

    line: int
    flags: frozenset[str] = frozenset()
    fields: dict[str, str] = field(default_factory=dict)
    msgstr: dict[int, str] = field(default_factory=dict)
    indexed: bool = False
    last: tuple[str, int] | None = None

    @property
    def msgid(self) -> str | None:
        """The source text, or ``None`` when the entry has none."""
        return self.fields.get("msgid")


def _unquote(text: str, line: int) -> str:
    """The content of one quoted PO string; raises ``PoSyntaxError`` for a bad quote or escape."""
    match = QUOTED.match(text.strip())
    if match is None:
        raise PoSyntaxError(line, f"not a quoted string: {text.strip()[:40]}")
    inner = match[1]
    if VALID_ESCAPE.sub("", inner).count("\\"):
        raise PoSyntaxError(line, "unknown escape sequence")
    return inner


def _append(entry: Entry, text: str) -> None:
    """Add a continuation string to the keyword the entry saw last."""
    if entry.last is None:
        raise PoSyntaxError(entry.line, "a string without a keyword")
    name, index = entry.last
    if name == "msgstr":
        entry.msgstr[index] += text
    else:
        entry.fields[name] += text


def _set_keyword(entry: Entry, keyword: str, plural_index: str | None, text: str, line: int) -> None:
    """Store a keyword line in the entry; raises ``PoSyntaxError`` when it comes twice."""
    if keyword.startswith("msgstr"):
        index = int(plural_index) if plural_index is not None else 0
        if index in entry.msgstr or (entry.msgstr and entry.indexed != (plural_index is not None)):
            raise PoSyntaxError(line, f"{keyword} given twice")
        entry.indexed = plural_index is not None
        entry.msgstr[index] = text
        entry.last = ("msgstr", index)
        return
    if keyword in entry.fields or entry.msgstr:
        raise PoSyntaxError(line, f"{keyword} out of place")
    entry.fields[keyword] = text
    entry.last = (keyword, 0)


def _finish(entry: Entry) -> None:
    """Raise ``PoSyntaxError`` when the entry lacks its msgid or the matching msgstr form."""
    if entry.msgid is None:
        raise PoSyntaxError(entry.line, "an entry without msgid")
    if not entry.msgstr:
        raise PoSyntaxError(entry.line, "an entry without msgstr")
    if ("msgid_plural" in entry.fields) != entry.indexed:
        raise PoSyntaxError(entry.line, "msgid_plural needs msgstr[n], and msgstr[n] needs msgid_plural")


@dataclass
class _Reader:
    """The parse state: finished entries, the open entry and the flags waiting for the next one."""

    entries: list[Entry] = field(default_factory=list)
    current: Entry | None = None
    flags: set[str] = field(default_factory=set)

    def feed(self, number: int, line: str) -> None:
        """Take one stripped line; raises ``PoSyntaxError`` when gettext cannot read it."""
        if line.startswith("#,"):
            self.flags.update(flag.strip() for flag in line[2:].split(","))
        elif not line or line.startswith("#"):
            return
        elif line.startswith('"'):
            if self.current is None:
                raise PoSyntaxError(number, "a string without a keyword")
            _append(self.current, _unquote(line, number))
        else:
            self._keyword(number, line)

    def _keyword(self, number: int, line: str) -> None:
        match = KEYWORD.match(line)
        if match is None:
            raise PoSyntaxError(number, f"unknown line: {line[:40]}")
        keyword, plural_index, quoted = match.groups()
        if keyword in ("msgctxt", "msgid") and (self.current is None or self.current.msgstr):
            self.close()
            self.current = Entry(line=number, flags=frozenset(self.flags))
            self.flags = set()
        if self.current is None:
            raise PoSyntaxError(number, f"{keyword} before msgid")
        _set_keyword(self.current, keyword, plural_index, _unquote(quoted, number), number)

    def close(self) -> None:
        """Check and keep the open entry, if there is one."""
        if self.current is not None:
            _finish(self.current)
            self.entries.append(self.current)
            self.current = None


def parse(text: str) -> list[Entry]:
    """Read a catalog into its entries; raises ``PoSyntaxError`` at the first unreadable line."""
    reader = _Reader()
    for number, raw in enumerate(text.splitlines(), start=1):
        reader.feed(number, raw.strip())
    reader.close()
    return reader.entries


def _placeholders(text: str, use_printf: bool) -> Counter[str]:
    """The placeholders of a text: ``{name}`` always, printf ones (``%%`` skipped) when asked."""
    found = Counter(BRACE_PLACEHOLDER.findall(text))
    if use_printf:
        found.update(PRINTF_PLACEHOLDER.findall(text.replace("%%", "")))
    return found


def placeholder_problems(entry: Entry) -> list[str]:
    """Why the translations of an entry lose or add placeholders; empty when they keep them."""
    use_printf = bool(entry.flags & PRINTF_FLAGS)
    source = entry.msgid or ""
    plural = entry.fields.get("msgid_plural", source)
    problems = []
    for index, translation in sorted(entry.msgstr.items()):
        expected = _placeholders(source if index == 0 else plural, use_printf)
        if translation and _placeholders(translation, use_printf) != expected:
            wanted = ", ".join(sorted(expected)) or "none"
            problems.append(f"msgstr[{index}] of {source[:40]!r} changes the placeholders (expected: {wanted})")
    return problems


def check_text(text: str) -> list[tuple[int, str]]:
    """The problems of one catalog's text as ``(line, message)`` pairs, in line order."""
    try:
        entries = parse(text)
    except PoSyntaxError as error:
        return [(error.line, error.message)]
    problems: list[tuple[int, str]] = []
    first_line: dict[str, int] = {}
    for entry in entries:
        msgid = entry.msgid or ""
        if msgid in first_line:
            problems.append((entry.line, f"duplicate msgid {msgid[:40]!r} (first at line {first_line[msgid]})"))
        first_line.setdefault(msgid, entry.line)
        problems += [(entry.line, problem) for problem in placeholder_problems(entry)]
    return problems


def check_file(path: Path) -> list[str]:
    """The problems of one catalog file as ``path:line: message`` lines."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"{path}:0: cannot be read as UTF-8 ({error})"]
    return [f"{path}:{line}: {message}" for line, message in check_text(text)]


def main(argv: Sequence[str] | None = None) -> int:
    """Check every catalog named on the command line; returns the exit code."""
    paths = [Path(arg) for arg in (sys.argv[1:] if argv is None else argv)]
    problems = [problem for path in paths for problem in check_file(path)]
    for problem in problems:
        print(problem, file=sys.stderr)
    return EXIT_PROBLEMS if problems else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
