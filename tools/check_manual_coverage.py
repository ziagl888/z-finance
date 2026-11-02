"""Manual coverage gate: every screen has its help chapter in every language (SPEC AK21, UC NF11).

The screens are the start page plus one route per ``frontend/src/features/<x>/route.tsx``. The
route list is read statically from those files: each must contain exactly one string literal
``path: "<route>"``. A route file the tool cannot read that way is an error (exit 2), never a
skipped route — the gate refuses to shrink.

A route maps to its chapter name (``chapter_for_path``): the first path segment, ``""`` (the start
page) -> ``index``. For every chapter of a route and for every chapter file that exists in any
language, ``docs/manual/<lang>/<chapter>.md`` must exist for every language in ``LANGUAGES``.
Each chapter file must follow the chapter format:

    ---            optional front matter, only the key `order` (integer, sort key in the manual)
    order: 20
    ---
    # Title        first non-blank line after the front matter: a level-1 heading

The check looks at the whole tree, not at a diff: nothing else in a commit can satisfy it.
Exit status 0 = covered, 1 = chapters missing or malformed, 2 = route list not determinable.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEATURES_DIR = Path("frontend/src/features")
MANUAL_DIR = Path("docs/manual")
# UI languages; keep in step with LOCALES in frontend/src/i18n/locale.ts.
LANGUAGES = ("en", "de")
START_PAGE_CHAPTER = "index"

_PATH_LITERAL = re.compile(r"""\bpath\s*:\s*(["'])(?P<path>[^"'`$]*)\1""")
_PATH_KEY = re.compile(r"\bpath\s*:")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONT_MATTER = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", re.DOTALL)
_ORDER = re.compile(r"^order:\s*-?\d+\s*$")


class RouteListError(Exception):
    """A route file whose path cannot be determined statically."""


@dataclass(frozen=True)
class Route:
    """A screen: the feature folder it comes from and its URL path ("" = start page)."""

    feature: str
    path: str

    @property
    def chapter(self) -> str:
        """The manual chapter that documents this screen."""
        return chapter_for_path(self.path)


def chapter_for_path(path: str) -> str:
    """Chapter name of a route path: its first segment, the start page ``""`` -> ``index``.

    Mirrors ``chapterForPath`` in frontend/src/help/manual.ts. Raises ``RouteListError`` for a
    first segment that is not a lowercase slug (a parameter like ``:id`` cannot name a chapter).
    """
    first = path.strip("/").split("/", 1)[0]
    if first == "":
        return START_PAGE_CHAPTER
    if not _SLUG.match(first):
        raise RouteListError(f"route path {path!r}: first segment {first!r} is not a lowercase slug")
    return first


def read_route_path(route_file: Path) -> str:
    """The ``path`` string literal of one ``route.tsx``; exactly one ``path:`` key is allowed."""
    source = route_file.read_text(encoding="utf-8")
    keys = _PATH_KEY.findall(source)
    literal = _PATH_LITERAL.search(source)
    if len(keys) != 1 or literal is None:
        raise RouteListError(
            f'{route_file.as_posix()}: need exactly one `path: "<route>"` string literal '
            f"(found {len(keys)} `path:` keys); the route list must be readable without running the code"
        )
    return literal["path"]


def discover_routes(root: Path = ROOT) -> list[Route]:
    """Start page plus every ``features/<x>/route.tsx``, sorted by feature folder."""
    routes = [Route(feature="", path="")]
    for route_file in sorted((root / FEATURES_DIR).glob("*/route.tsx")):
        path = read_route_path(route_file)
        try:
            chapter_for_path(path)
        except RouteListError as exc:
            raise RouteListError(f"{route_file.as_posix()}: {exc}") from None
        routes.append(Route(feature=route_file.parent.name, path=path))
    return routes


def feature_chapter(feature: str, root: Path | None = None) -> str:
    """Chapter a feature folder's screen is documented in; the folder name if it has no route file
    (a feature being deleted). Used by the docs gate (tools/check_docs_gate.py)."""
    route_file = (root or ROOT) / FEATURES_DIR / feature / "route.tsx"
    if not route_file.is_file():
        return feature
    return chapter_for_path(read_route_path(route_file))


def chapter_format_problems(text: str) -> list[str]:
    """Format violations of one chapter file (see the module docstring)."""
    problems: list[str] = []
    body = text
    if text.startswith("---"):
        match = _FRONT_MATTER.match(text)
        if match is None:
            return ["front matter opened with '---' but not closed"]
        for line in match["body"].splitlines():
            if line.strip() and not _ORDER.match(line.strip()):
                problems.append(f"front matter line {line.strip()!r}: only `order: <integer>` is allowed")
        body = text[match.end() :]
    first = next((line for line in body.splitlines() if line.strip()), "")
    if not re.match(r"^# \S", first):
        problems.append("the first line must be a level-1 heading (`# Title`)")
    return problems


def coverage_problems(root: Path = ROOT) -> list[str]:
    """Every missing or malformed chapter file, one message each."""
    routes = discover_routes(root)
    manual = root / MANUAL_DIR
    existing = {p.stem for lang in LANGUAGES for p in (manual / lang).glob("*.md")}
    problems: list[str] = []
    for chapter in sorted({r.chapter for r in routes} | existing):
        users = ["/" + r.path for r in routes if r.chapter == chapter]
        reason = f"route {', '.join(users)}" if users else "an existing chapter"
        for lang in LANGUAGES:
            file = manual / lang / f"{chapter}.md"
            rel = file.relative_to(root).as_posix()
            if not file.is_file():
                problems.append(f"{rel} missing ({reason} needs it in every language: {', '.join(LANGUAGES)})")
                continue
            problems.extend(f"{rel}: {p}" for p in chapter_format_problems(file.read_text(encoding="utf-8")))
    return problems


def main(argv: list[str]) -> int:
    """Command line entry point: optional repository root; 0 = covered, 1 = missing or malformed, 2 = routes unknown."""
    root = Path(argv[0]) if argv else ROOT
    try:
        problems = coverage_problems(root)
    except RouteListError as exc:
        print(f"manual coverage: route list not determinable: {exc}", file=sys.stderr)
        return 2
    for line in problems:
        print(line, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
