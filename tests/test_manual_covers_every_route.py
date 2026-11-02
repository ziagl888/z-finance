"""SPEC AK21 / UC NF11: every screen has its manual chapter in both languages (tools/check_manual_coverage.py).

The first test runs the gate on the repository itself; the others run it on synthetic trees in a
temporary directory to show it fails for what it must refuse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from check_manual_coverage import (
    LANGUAGES,
    ROOT,
    RouteListError,
    chapter_for_path,
    coverage_problems,
    discover_routes,
    main,
)

CHAPTER = "---\norder: 10\n---\n\n# Title\n\nText.\n"


def _route(root: Path, feature: str, body: str) -> None:
    file = root / "frontend/src/features" / feature / "route.tsx"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(body, encoding="utf-8")


def _chapter(root: Path, lang: str, name: str, text: str = CHAPTER) -> None:
    file = root / "docs/manual" / lang / f"{name}.md"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A covered tree: start page + route `pots`, both chapters in every language."""
    _route(tmp_path, "pots", 'export const feature = { path: "pots", Component: PotsPage };\n')
    for lang in LANGUAGES:
        _chapter(tmp_path, lang, "index")
        _chapter(tmp_path, lang, "pots")
    return tmp_path


def test_manual_covers_every_route() -> None:
    """Oracle: spec-anchored — SPEC AK21 test name: every route of the repository has both language files."""
    routes = discover_routes(ROOT)
    assert {r.chapter for r in routes} >= {"index", "settings", "backup", "manual"}
    assert coverage_problems(ROOT) == []
    for route in routes:
        for lang in ("en", "de"):
            assert (ROOT / "docs/manual" / lang / f"{route.chapter}.md").is_file(), (route, lang)


def test_covered_tree_passes(tree: Path) -> None:
    """Oracle: spec-anchored — AK21: start page + route with chapters in both languages is covered."""
    assert coverage_problems(tree) == []
    assert main([str(tree)]) == 0


@pytest.mark.parametrize("lang", LANGUAGES)
def test_route_missing_one_language_fails(tree: Path, lang: str) -> None:
    """Oracle: spec-anchored — task -005: a route without both language files fails, whatever else changed."""
    (tree / "docs/manual" / lang / "pots.md").unlink()
    problems = coverage_problems(tree)
    assert len(problems) == 1
    assert f"docs/manual/{lang}/pots.md missing" in problems[0]
    assert "route /pots" in problems[0]
    assert main([str(tree)]) == 1


def test_start_page_needs_index_chapter(tree: Path) -> None:
    """Oracle: spec-anchored — task -005: the start page is a screen too; its chapter is index."""
    (tree / "docs/manual/de/index.md").unlink()
    assert [p.split(" ", 1)[0] for p in coverage_problems(tree)] == ["docs/manual/de/index.md"]


def test_new_route_without_chapter_fails(tree: Path) -> None:
    """Oracle: spec-anchored — AK21: a new screen without its chapters is refused."""
    _route(tree, "debts", "export const feature = {\n  path: 'debts',\n  Component: DebtsPage,\n};\n")
    missing = sorted(p.split(" ", 1)[0] for p in coverage_problems(tree))
    assert missing == ["docs/manual/de/debts.md", "docs/manual/en/debts.md"]


def test_chapter_in_one_language_only_fails(tree: Path) -> None:
    """Oracle: spec-anchored — UC NF11 "zweisprachig": an extra chapter must exist in every language too."""
    _chapter(tree, "en", "faq")
    assert [p.split(" ", 1)[0] for p in coverage_problems(tree)] == ["docs/manual/de/faq.md"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Intro without heading\n", "level-1 heading"),
        ("---\ntitle: Pots\n---\n# Pots\n", "only `order: <integer>`"),
        ("---\norder: soon\n---\n# Pots\n", "only `order: <integer>`"),
        ("---\norder: 1\n# Pots\n", "not closed"),
    ],
)
def test_malformed_chapter_fails(tree: Path, text: str, message: str) -> None:
    """Oracle: spec-anchored — chapter format of task -005 (optional `order` front matter, then `# Title`)."""
    _chapter(tree, "de", "pots", text)
    problems = coverage_problems(tree)
    assert len(problems) == 1
    assert message in problems[0]


@pytest.mark.parametrize(
    "body",
    [
        "export const feature = { path: PATH, Component: X };\n",  # not a literal
        "export const feature = { path: `pots`, Component: X };\n",  # template literal
        "export const feature = { Component: X };\n",  # no path at all
        'const a = { path: "a" }; const b = { path: "b" };\n',  # ambiguous
        'export const feature = { path: ":id", Component: X };\n',  # not a slug
    ],
)
def test_route_list_not_determinable_stops(tree: Path, body: str) -> None:
    """Oracle: spec-anchored — task -005 stop condition: route list not statically determinable -> stop
    (exit 2), never a skipped route."""
    _route(tree, "odd", body)
    with pytest.raises(RouteListError):
        coverage_problems(tree)
    assert main([str(tree)]) == 2


@pytest.mark.parametrize(
    ("path", "chapter"), [("", "index"), ("/", "index"), ("pots", "pots"), ("accounts/:id", "accounts")]
)
def test_chapter_for_path(path: str, chapter: str) -> None:
    """Oracle: spec-anchored — task -005: chapter = first path segment, start page -> index
    (same rule as chapterForPath in frontend/src/help/manual.ts)."""
    assert chapter_for_path(path) == chapter
