"""Docs gate: a feature change needs a README/ARCHITECTURE/CHANGELOG or manual-chapter change (UC NF8)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import check_manual_coverage
from check_docs_gate import RULES, Rule, main, undocumented


@pytest.mark.parametrize(
    "paths",
    [
        ["backend/zfinance/domain/accounts/service.py", "frontend/src/features/accounts/route.tsx"],
        ["backend/zfinance/domain/accounts/api.py"],
        ["frontend/src/features/debts/DebtsPage.tsx", "tests/test_debts.py"],
    ],
)
def test_feature_change_without_docs_is_blocked(paths: list[str]) -> None:
    """Oracle: spec-anchored — task -003: change under domain/<x> or features/<x> requires a doc change."""
    assert undocumented(paths) != []
    assert main(paths) == 1


@pytest.mark.parametrize("doc", ["README.md", "ARCHITECTURE.md", "CHANGELOG.md"])
def test_project_doc_satisfies_a_backend_feature(doc: str) -> None:
    """Oracle: spec-anchored — UC NF8 "README/ARCHITECTURE/CHANGELOG bei Modul-Änderung berührt"."""
    assert undocumented(["backend/zfinance/domain/accounts/service.py", doc]) == []


@pytest.mark.parametrize("doc", ["README.md", "ARCHITECTURE.md", "CHANGELOG.md"])
def test_project_doc_does_not_satisfy_a_screen_change(doc: str) -> None:
    """Oracle: spec-anchored — SPEC AK21: a screen change without its manual chapter blocks the commit,
    whatever else is in the change (task -005: not satisfiable through the project-docs exemption)."""
    problems = undocumented(["frontend/src/features/debts/DebtsPage.tsx", doc])
    assert len(problems) == 1
    assert "'debts'" in problems[0]
    assert "docs/manual/*/debts.md" in problems[0]
    assert main(["frontend/src/features/debts/DebtsPage.tsx", doc]) == 1
    assert undocumented(["frontend/src/features/debts/DebtsPage.tsx", doc, "docs/manual/de/debts.md"]) == []


def test_screen_change_maps_to_its_route_chapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — task -005: the chapter is the route's path ("" = start page -> index),
    not the folder name, so a dashboard claiming "" is documented in index.md."""
    route = tmp_path / "frontend/src/features/dashboard/route.tsx"
    route.parent.mkdir(parents=True)
    route.write_text('export const feature = { path: "", Component: X };\n', encoding="utf-8")
    monkeypatch.setattr(check_manual_coverage, "ROOT", tmp_path)
    changed = ["frontend/src/features/dashboard/DashboardPage.tsx"]
    assert undocumented([*changed, "docs/manual/en/dashboard.md"]) != []
    assert undocumented([*changed, "docs/manual/en/index.md"]) == []


def test_manual_chapter_satisfies_only_its_own_feature() -> None:
    """Oracle: spec-anchored — task -003: docs/manual/*/<x>.md documents feature <x>, not another one."""
    assert undocumented(["backend/zfinance/domain/accounts/api.py", "docs/manual/de/accounts.md"]) == []
    assert undocumented(["frontend/src/features/accounts/route.tsx", "docs/manual/en/accounts.md"]) == []
    problems = undocumented(
        [
            "backend/zfinance/domain/accounts/api.py",
            "backend/zfinance/domain/debts/api.py",
            "docs/manual/en/accounts.md",
        ]
    )
    assert len(problems) == 1
    assert "'debts'" in problems[0]


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["backend/zfinance/domain/__init__.py"],  # the package root is not a feature
        ["backend/zfinance/app.py", "frontend/src/app/Layout.tsx", "tests/test_health.py"],
        ["docs/manual/en/accounts.md"],
    ],
)
def test_changes_outside_features_pass(paths: list[str]) -> None:
    """Oracle: spec-anchored — task -003 watches only domain/<x> and features/<x>."""
    assert undocumented(paths) == []


@pytest.mark.parametrize(
    "test_file",
    [
        "frontend/src/features/forecast/forecast.test.tsx",
        "frontend/src/features/import/parse.test.ts",
        "frontend/src/features/import/__tests__/profile.tsx",
    ],
)
def test_a_screen_test_file_alone_needs_no_manual_chapter(test_file: str) -> None:
    """Oracle: spec-anchored — orchestrator decision for T-2026-HAR-9130-002 (option B): a test file under
    features/<x> changes no screen, so it does not fire the gate; a screen file next to it still does."""
    assert undocumented([test_file]) == []
    assert main([test_file]) == 0
    screen = test_file.rsplit("/", 1)[0].removesuffix("/__tests__") + "/route.tsx"
    problems = undocumented([test_file, screen])
    assert len(problems) >= 1
    assert main([test_file, screen]) == 1


def test_windows_separators_are_normalised() -> None:
    """Oracle: spec-anchored — gate contract: paths compare in repo-relative POSIX form whatever the caller passes."""
    assert undocumented(["backend\\zfinance\\domain\\accounts\\api.py"]) != []
    assert undocumented(["backend\\zfinance\\domain\\accounts\\api.py", "docs\\manual\\en\\accounts.md"]) == []


def test_a_new_rule_row_extends_the_gate() -> None:
    """Oracle: spec-anchored — task -003: -005 adds route->chapter as a rule, without changing the checker."""
    routes = Rule("frontend route", re.compile(r"^frontend/src/routes/(?P<name>[^/.]+)"), ("docs/manual/*/{name}.md",))
    rules = (*RULES, routes)
    assert undocumented(["frontend/src/routes/help.tsx"], rules) != []
    assert undocumented(["frontend/src/routes/help.tsx", "docs/manual/de/help.md"], rules) == []
