"""Docs gate: a change to a feature must bring a documentation change with it (UC NF8).

Called with the changed paths (pre-commit passes the staged files; CI passes the files a pull
request changes). For every feature touched under a watched source tree, the feature's doc must be
among the changed paths as well:

- backend feature ``backend/zfinance/domain/<x>/``: a project-wide document (README.md,
  ARCHITECTURE.md, CHANGELOG.md) or the manual chapter ``docs/manual/<lang>/<x>.md``;
- frontend feature ``frontend/src/features/<x>/`` (a screen, SPEC AK21 / UC NF11): the manual
  chapter of its route, ``docs/manual/<lang>/<chapter>.md`` in any language. This rule is
  *strict*: a project-wide document does not satisfy it. The chapter is the route's first path
  segment (the start page ``""`` -> ``index``), read by tools/check_manual_coverage.py. Test
  files (``*.test.ts``, ``*.test.tsx``, ``__tests__/``) are no screen change and need no chapter.

Watched source trees and the chapter each maps to are the rows of ``RULES``; a new kind of
source is one more row, not a code change. That every route has a chapter in every language at
all is checked over the whole tree by tools/check_manual_coverage.py.

Exit status 0 = satisfied, 1 = at least one feature changed without docs.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase

from check_manual_coverage import RouteListError, feature_chapter

PROJECT_DOCS = frozenset({"README.md", "ARCHITECTURE.md", "CHANGELOG.md"})


@dataclass(frozen=True)
class Rule:
    """A watched source tree: ``source`` captures the feature as group ``name``; changing a file
    there is documented by any path matching one of ``chapters`` (fnmatch patterns, ``{name}``
    replaced by ``chapter_of(feature)``) or, unless ``strict``, by a project-wide doc."""

    label: str
    source: re.Pattern[str]
    chapters: tuple[str, ...]
    strict: bool = False
    chapter_of: Callable[[str], str] = str
    ignore: re.Pattern[str] | None = None


# A test file under a feature changes no screen, so it needs no manual chapter.
FRONTEND_TEST_FILE = re.compile(r"\.test\.tsx?$|/__tests__/")

RULES: tuple[Rule, ...] = (
    Rule(
        "backend feature",
        re.compile(r"^backend/zfinance/domain/(?P<name>[^/]+)/"),
        ("docs/manual/*/{name}.md",),
    ),
    Rule(
        "frontend feature",
        re.compile(r"^frontend/src/features/(?P<name>[^/]+)/"),
        ("docs/manual/*/{name}.md",),
        strict=True,
        chapter_of=feature_chapter,
        ignore=FRONTEND_TEST_FILE,
    ),
)


def _changed_features(rule: Rule, changed: set[str]) -> list[str]:
    """The features of ``rule`` that the changed paths touch, ignored files left out, sorted."""
    sources = {p for p in changed if not (rule.ignore and rule.ignore.search(p))}
    return sorted({m["name"] for p in sources if (m := rule.source.match(p))})


def _missing_doc(rule: Rule, feature: str, changed: set[str]) -> str | None:
    """The message for a feature changed without its doc, or ``None`` when a doc changed too."""
    patterns = [c.format(name=rule.chapter_of(feature)) for c in rule.chapters]
    if any(fnmatchcase(p, pat) for p in changed for pat in patterns):
        return None
    accepted = " / ".join(patterns)
    if not rule.strict:
        accepted = f"{', '.join(sorted(PROJECT_DOCS))} or {accepted}"
    return f"{rule.label} '{feature}' changed without docs: also change {accepted}"


def undocumented(paths: list[str], rules: tuple[Rule, ...] = RULES) -> list[str]:
    """Return one message per (rule, feature) changed in ``paths`` without a matching doc change."""
    changed = {p.replace("\\", "/").removeprefix("./") for p in paths}
    project_doc_changed = bool(changed & PROJECT_DOCS)
    problems: list[str] = []
    for rule in rules:
        if project_doc_changed and not rule.strict:
            continue
        for feature in _changed_features(rule, changed):
            message = _missing_doc(rule, feature, changed)
            if message is not None:
                problems.append(message)
    return problems


def main(argv: list[str]) -> int:
    """Command line entry point: the changed paths as arguments; 0 = documented, 1 = a doc change is missing."""
    try:
        problems = undocumented(argv)
    except RouteListError as exc:
        print(f"docs gate: {exc}", file=sys.stderr)
        return 1
    for line in problems:
        print(line, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
