"""The project documentation is final and honest (task -028; UC NF7, NF10; SPEC AK18, AK19).

README leads with the lay installation, ARCHITECTURE describes the system as built and matches the
code where it can be checked, the changelog has its 1.0.0 entry, nothing in the public documents
points at a private machine or person, and every link in them leads somewhere.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from zfinance_agent.run import ExitCode

import public_export
from check_manual_coverage import ROOT

MIGRATIONS = ROOT / "backend" / "zfinance" / "migrations"
DOMAIN = ROOT / "backend" / "zfinance" / "domain"

PROJECT_DOCS = [
    "README.md",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "agent/README.md",
    "packaging/README.md",
]
# The German requirement documents (docs/UC-z-finance.md, SPEC-z-finance.md, docs/handoff/) are the
# author's source texts and named as such by NF7; they are not part of the public reading path.
PUBLIC_DOCS = [
    *PROJECT_DOCS,
    *sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "docs" / "manual").glob("*/*.md")),
    "docs/install-test.md",
]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body of the `## heading` section, up to the next `## `."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"no section {heading!r}"
    return match[1]


# --- README (AK6) --------------------------------------------------------------------------------


def test_readme_starts_with_the_lay_installation() -> None:
    """Oracle: spec-anchored — SPEC "Distribution (NF10)" and task -028: the README begins with the
    installation in three steps with screenshots, then what the tool does, then developer setup,
    contributing and licence, in this order."""
    readme = _read("README.md")
    headings = re.findall(r"^## (.+)$", readme, re.MULTILINE)
    assert headings[0] == "Install (for everyone)"
    positions = [headings.index(h) for h in ("Install (for everyone)", "What it does", "Developer setup", "License")]
    assert positions == sorted(positions)
    install = _section(readme, "Install (for everyone)")
    steps = re.findall(r"^([1-9])\. \*\*([^*]+)\*\*", install, re.MULTILINE)
    assert [number for number, _ in steps] == ["1", "2", "3"], steps
    assert [name for _, name in steps] == ["Download.", "Set up.", "First start."]
    assert "assets/screenshots/en/first-run-account.png" in install
    assert "early development" not in readme


# --- links and hygiene (AK7) -----------------------------------------------------------------------


@pytest.mark.parametrize("relative", PUBLIC_DOCS)
def test_every_relative_link_leads_to_a_file(relative: str) -> None:
    """Oracle: spec-anchored — SPEC AK19 "README/ARCHITECTURE/CHANGELOG/CONTRIBUTING vorhanden" and
    UC NF11 (the manual links its chapters): a relative link or image of a public document must resolve."""
    path = ROOT / relative
    for target in _relative_links(path.read_text(encoding="utf-8")):
        assert (path.parent / target).resolve().exists(), f"{relative}: {target}"


def _relative_links(markdown: str) -> list[str]:
    """The relative link and image targets of a Markdown text, without their `#anchor`.

    Links inside code blocks and code spans are examples, not links, and are left out.
    """
    text = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", "", text)
    targets = [target.split("#")[0] for target in re.findall(r"\]\(([^)\s]+)\)", text)]
    return [target for target in targets if target and not re.match(r"(https?:|mailto:)", target)]


@pytest.mark.parametrize("relative", PUBLIC_DOCS)
def test_no_private_machine_person_or_secret_in_the_public_documents(relative: str) -> None:
    """Oracle: spec-anchored — UC NF7 "keine persönlichen Daten, Zugangsdaten oder Beispiel-Bons im
    Repo" and task -028 "kein Pfad aus dem Rechner des Autors, kein Klarname, keine Kontonummer, kein Token":
    no user profile path, no drive path except the documented ones, no e-mail address, IBAN, token or
    author name."""
    text = _read(relative)
    assert not re.search(r"[A-Za-z]:\\Users\\(?!<)", text), "a user profile path"
    assert not re.search(r"/(?:home|Users)/(?!<)[a-z]", text), "a home directory"
    for drive_path in re.findall(r"\b[A-Za-z]:\\[^\s`'\"]+", text):
        assert drive_path.startswith(("C:\\Program", "C:\\Users\\<", "C:\\Projects\\z-finance")), drive_path
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "an e-mail address"
    assert not re.search(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b", text), "an IBAN"
    assert not re.search(r"ghp_\w{20,}|sk-[\w-]{20,}|xox[bp]-|BEGIN [A-Z ]*PRIVATE KEY", text), "a token"
    # The owner's name is only known to the private pattern file; the public repository has none.
    person_name = PRIVATE_PATTERNS.get("PERSON_NAME")
    assert person_name is None or not person_name.search(text), "a person's name"


def test_the_agent_example_config_holds_no_credential() -> None:
    """Oracle: spec-anchored — UC NF7 (no credentials in the repository): the values of every password
    and secret setting in `.env.example` are empty."""
    for line in _read(".env.example").splitlines():
        name, _, value = line.partition("=")
        if re.search(r"PASSWORD|SECRET|TOKEN", name) and not line.startswith("#"):
            assert value.strip() == "", name


# --- ARCHITECTURE (AK8) ----------------------------------------------------------------------------


def test_architecture_describes_the_system_as_built() -> None:
    """Oracle: spec-anchored — task -028 "ARCHITECTURE final: Domänenmodell, Migrationsordnung, die
    Kernregel (free_amount netto der levy_gap) als eigener Abschnitt, die Datenflüsse Import/Bon-Agent/
    Zuteilung und die Engine-Abhängigkeiten als Diagramm"; every migration file and every table that
    exists is named."""
    text = _read("ARCHITECTURE.md")
    headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
    for wanted in ("Domain model", "The core rule", "Engine dependencies", "Data flows", "Backend conventions"):
        assert wanted in headings, wanted
    assert "Domain model (planned)" not in text

    core = _section(text, "The core rule")
    assert "free_amount" in core and "levy_gap" in core and "7,000" in core

    flows = _section(text, "Data flows")
    for flow in ("CSV import", "Receipt agent", "Allocation"):
        assert f"**{flow}**" in flows, flow
    assert text.count("```mermaid") >= 6

    for migration in sorted(MIGRATIONS.glob("*.sql")):
        assert migration.name in text, migration.name
    model = _section(text, "Domain model")
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        for table in re.findall(
            r'CREATE TABLE\s+(?:IF NOT EXISTS\s+)?"?(\w+)"?', migration.read_text(encoding="utf-8")
        ):
            assert f"`{table}`" in model, f"table {table} is not in the domain model"


def _real_engine_dependencies() -> dict[str, set[str]]:
    """Which `domain/<x>` package imports which other, read from the source."""
    graph: dict[str, set[str]] = {}
    for package in sorted(p for p in DOMAIN.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for file in package.glob("*.py"):
            text = file.read_text(encoding="utf-8")
            found = re.findall(r"^\s*(?:from|import)\s+zfinance\.domain\.(\w+)", text, re.MULTILINE)
            found += re.findall(r"^\s*from\s+\.\.(\w+)[\w.]*\s+import", text, re.MULTILINE)
            for group in re.findall(r"^\s*from\s+zfinance\.domain\s+import\s+([\w, ]+)", text, re.MULTILINE):
                found += [name.strip() for name in group.split(",")]
            graph.setdefault(package.name, set()).update(f for f in found if f and f != package.name)
    return graph


def _drawn_engine_dependencies() -> dict[str, set[str]]:
    """The arrows of the flowchart in ARCHITECTURE "Engine dependencies"."""
    section = _section(_read("ARCHITECTURE.md"), "Engine dependencies")
    block = re.search(r"```mermaid\nflowchart TD\n(.*?)```", section, re.DOTALL)
    assert block, "no flowchart"
    drawn: dict[str, set[str]] = {}
    for line in block[1].splitlines():
        source, arrow, targets = line.strip().partition("-->")
        if arrow:
            drawn.setdefault(source.strip(), set()).update(t.strip() for t in targets.split("&"))
    return drawn


def test_the_engine_diagram_matches_the_imports_and_has_no_cycle() -> None:
    """Oracle: spec-anchored — ARCHITECTURE "Engine dependencies" claims "no cycle" and "every arrow
    is a real import"; both are decided from the source, not from the diagram."""
    real, drawn = _real_engine_dependencies(), _drawn_engine_dependencies()
    assert drawn, "the diagram has no arrows"
    for source, targets in drawn.items():
        assert targets <= real.get(source, set()), (
            f"{source} does not import {sorted(targets - real.get(source, set()))}"
        )

    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        assert node not in visiting, f"import cycle through {node}"
        if node in done:
            return
        visiting.add(node)
        for dependency in real.get(node, set()):
            visit(dependency)
        visiting.discard(node)
        done.add(node)

    for node in real:
        visit(node)


# --- changelog, agent README, docstrings (AK9, AK10) -------------------------------------------------------


def test_the_changelog_has_its_1_0_0_entry() -> None:
    """Oracle: spec-anchored — task -028 "CHANGELOG 1.0.0": a `## 1.0.0` section with the
    Keep-a-Changelog subsections exists below an (empty) `## Unreleased`, and names this task."""
    text = _read("CHANGELOG.md")
    unreleased, release = text.find("\n## Unreleased\n"), text.find("\n## 1.0.0")
    assert 0 < unreleased < release
    body = text[release:]
    assert "\n### Added\n" in body and "\n### Changed\n" in body
    assert "T-2026-CU-9130-028" in body
    if re.match(r"## 1\.0\.0 - \d{4}-\d{2}-\d{2}\n", text[release + 1 :]) is None:
        assert text[unreleased:release].strip() == "## Unreleased"


def test_the_agent_readme_names_only_real_settings_and_exit_codes() -> None:
    """Oracle: spec-anchored — task -028 "agent/README.md geprüft": every `ZF_AGENT_*` setting the README
    names is in `.env.example`, every exit code it lists is one the agent returns, and the entry point
    it starts exists."""
    readme = _read("agent/README.md")
    example = _read(".env.example")
    for setting in set(re.findall(r"ZF_AGENT_[A-Z_]+", readme)):
        assert f"{setting}=" in example, setting
    codes = {int(code) for code in re.findall(r"[Ee]xit code (\d+)", readme)}
    assert codes <= {int(code) for code in ExitCode}
    scripts = tomllib.loads(_read("agent/pyproject.toml"))["project"]["scripts"]
    assert "zfinance-agent" in scripts and "zfinance-agent" in readme


def test_the_docstring_rules_are_part_of_the_lint_with_two_documented_exceptions() -> None:
    """Oracle: spec-anchored — SPEC "Doku-Set: Docstrings auf allen public Symbols" and task -028
    "Docstring-Audit … Ausnahmen begründet": ruff's D1 rules are selected and the only global
    exceptions are the constructor and the dunder methods, each with its reason in `pyproject.toml`."""
    config = tomllib.loads(_read("pyproject.toml"))["tool"]["ruff"]["lint"]
    assert "D1" in config["select"]
    assert sorted(config["ignore"]) == ["D105", "D107"]
    assert "D1" in config["per-file-ignores"]["tests/**"]


# --- the public export (packaging/public_export.toml, tools/public_export.py) ----------------------
# What must not reach the public repository. This file is exported and scanned like every other, so
# it holds only GENERIC patterns. The ones that name the owner, the employer, a customer, the ERP
# product and the owner's machines live in docs/qa/private_patterns.toml, which the export excludes;
# they are loaded below when that file exists (the private repository) and absent in the public one.

PRIVATE_PATTERNS_RELATIVE = "docs/qa/private_patterns.toml"
PRIVATE_PATTERNS_FILE = ROOT / PRIVATE_PATTERNS_RELATIVE
PRIVATE_REPOSITORY_MARKER = "SPEC-z-finance.md"  # a file only the private repository has
# What the private file has to cover: owner, work mail, employer, customer repository, customer,
# ERP product and the owner's machines.
REQUIRED_IDENTIFYING = frozenset(
    {"PERSON_NAME", "WORK_MAIL", "EMPLOYER", "CUSTOMER_REPOSITORY", "CUSTOMER", "ERP_PRODUCT", "OWNER_MACHINE"}
)


@dataclass(frozen=True)
class IdentifyingPattern:
    """One pattern of the private file with its positive and negative examples."""

    name: str
    regex: re.Pattern[str]
    positive: tuple[str, ...]
    negative: tuple[str, ...]


def load_identifying_patterns(path: Path) -> list[IdentifyingPattern]:
    """The patterns of the private file; an empty list when it does not exist (the public repository)."""
    if not path.is_file():
        return []
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("pattern", [])
    return [
        IdentifyingPattern(
            name=entry["name"],
            regex=re.compile(entry["regex"], re.IGNORECASE),
            positive=tuple(entry["positive"]),
            negative=tuple(entry["negative"]),
        )
        for entry in entries
    ]


def private_file_problem(root: Path) -> str | None:
    """Why the guard would be switched off, or None: the private repository must have the private file."""
    if (root / PRIVATE_REPOSITORY_MARKER).exists() and not (root / PRIVATE_PATTERNS_RELATIVE).is_file():
        return f"{PRIVATE_PATTERNS_RELATIVE} is missing in the private repository"
    return None


IDENTIFYING_PATTERNS = load_identifying_patterns(PRIVATE_PATTERNS_FILE)
# Windows default host names, which always carry a digit.
MACHINE_NAME = re.compile(r"\b(?:LAPTOP|DESKTOP)-(?=[A-Z0-9]*\d)[A-Z0-9]{5,}\b")
# A Windows profile path with a real user name (`C:\Users\<you>` placeholders pass) or a Unix home.
USER_PATH = re.compile(r"(?i:\b[a-z]:[\\/]+users[\\/]+(?!<|public\b|default\b)\w)|/(?:c/)?(?:home|Users)/(?!<)[a-z]")
# An IBAN, with or without the groups of four.
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b")
# A labelled account number ("Kontonummer: ...", "account no. ...").
ACCOUNT_NUMBER = re.compile(r"(?i)\b(?:kontonummer|konto-?nr\.?|account (?:no\.?|number))\s*[:#]?\s*\d[\d -]{4,}\d")
# Finance terms only where they describe a person: a third person's debts, income or taxes, saying
# one is a small business, and the up-and-down cycle of the requirement documents. The neutral product
# terms ("debts", "Einkommensteuer", "SVS") stay allowed, and so does "my debts": the manual quotes the
# reader's own rules in the first person.
PERSONAL_FINANCE = re.compile(
    r"(?i)\bauf[- ]und[- ]ab\b"
    r"|\b(?:sein|seine|seinem|seinen|seiner|ihre|ihren|ihrem|ihrer|his|her)\s+"
    r"(?:schulden|kredit\w*|einkommen\w*|gehalt|steuer\w*|svs\w*|debts?|loans?|income|salary|credit card|taxes)\b"
    r"|\b(?:ich bin|bin ich|i am|i'm)\s+(?:ein\s+|a\s+)?(?:kleinunternehmer|small business)"
)
PRIVATE_PATTERNS = {
    **{pattern.name: pattern.regex for pattern in IDENTIFYING_PATTERNS},
    "MACHINE_NAME": MACHINE_NAME,
    "USER_PATH": USER_PATH,
    "IBAN": IBAN,
    "ACCOUNT_NUMBER": ACCOUNT_NUMBER,
    "PERSONAL_FINANCE": PERSONAL_FINANCE,
}

# Hand-picked: the private requirement and QA documents, local agent state, runtime data and build
# output must never be selected, whatever else the manifest says.
MUST_STAY_PRIVATE = [
    "SPEC-z-finance.md",
    "docs/UC-z-finance.md",
    "docs/specs/SPEC-T-2026-CU-9130-020.md",
    "docs/handoff/2026-09-19-orchestrator.md",
    "docs/qa/install-protocol.md",
    "docs/qa/private_patterns.toml",
    ".claude/settings.local.json",
    "backend/CLAUDE.md",
    "data/z-finance.sqlite3",
    "backups/z-finance-2026-09-01.zip",
    ".playwright-mcp/page.png",
    "frontend/node_modules/react/index.js",
    "frontend/dist/index.html",
    ".env",
]
MIN_EXPORTED_FILES = 300  # the product alone is ~400 tracked files; far fewer means a broken manifest


def _manifest() -> public_export.Manifest:
    return public_export.load_manifest(public_export.MANIFEST)


def _text_or_none(relative: str) -> str | None:
    """The file as UTF-8 text, or None for a binary file (a NUL byte or not decodable)."""
    data = (ROOT / relative).read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


@pytest.mark.parametrize(
    ("name", "positive", "negative"),
    [
        ("MACHINE_NAME", "host LAPTOP-" + "7QK2M9F", "a laptop-friendly layout"),
        ("MACHINE_NAME", "host DESKTOP-" + "AB12CD3", "https://github.com/ziagl888/z-finance"),
        ("USER_PATH", "C:" + r"\Users\someone\AppData", r"C:\Users\<you>\AppData"),
        ("USER_PATH", "/c/" + "Users/someone/z-finance", "GET /api/users/7"),
        ("IBAN", "AT61 1904" + " 3002 3457 3201", "AT61 is not an IBAN"),
        ("ACCOUNT_NUMBER", "Kontonummer: " + "12345678", "the account number field is empty"),
        ("PERSONAL_FINANCE", "sein" + "e Schulden", "debts with a payoff plan"),
        ("PERSONAL_FINANCE", "his" + " income tax", "20 % go to my debts and 5 % to the tax pot"),
        ("PERSONAL_FINANCE", "der Auf-" + "und-Ab-Zyklus", "Einkommensteuer-Vorauszahlungen und SVS"),
        ("PERSONAL_FINANCE", "ich bin " + "Kleinunternehmer", "the small-business exemption setting"),
    ],
)
def test_each_private_pattern_catches_its_case_and_spares_product_text(name: str, positive: str, negative: str) -> None:
    """Oracle: spec-anchored — task HAR-9130-002 "jedes Muster mit einem Positiv- und einem
    Negativbeispiel" and task HAR-9130-028 (3) for the employer, customer and ERP names: the positive is
    the kind of text the pattern exists for, the negative is product text that has to stay."""
    pattern = PRIVATE_PATTERNS[name]
    assert pattern.search(positive), positive
    assert not pattern.search(negative), negative


def _private_example_cases() -> list[Any]:
    """One case per example of the private file, or a single skipped placeholder without the file."""
    cases = [
        pytest.param(p.name, text, kind == "positive", id=f"{p.name}-{kind}-{number}")
        for p in IDENTIFYING_PATTERNS
        for kind, examples in (("positive", p.positive), ("negative", p.negative))
        for number, text in enumerate(examples)
    ]
    reason = f"{PRIVATE_PATTERNS_RELATIVE} does not exist: the public repository runs only the generic patterns"
    return cases or [pytest.param("-", "-", True, marks=pytest.mark.skip(reason=reason), id="no-private-file")]


@pytest.mark.parametrize(("name", "text", "should_match"), _private_example_cases())
def test_each_identifying_pattern_catches_its_case_and_spares_product_text(
    name: str, text: str, should_match: bool
) -> None:
    """Oracle: spec-anchored — task HAR-9130-030 (2): the identifying patterns of the private file keep
    their positive and negative examples, and the guard loads and merges them into PRIVATE_PATTERNS."""
    assert bool(PRIVATE_PATTERNS[name].search(text)) is should_match, text


def test_the_private_repository_cannot_switch_the_identifying_patterns_off(tmp_path: Path) -> None:
    """Oracle: spec-anchored — task HAR-9130-030 (2): in the private repository (it has the requirement
    document) a missing pattern file is a failure; without that document it is the public repository."""
    assert private_file_problem(ROOT) is None
    if (ROOT / PRIVATE_REPOSITORY_MARKER).exists():
        assert {p.name for p in IDENTIFYING_PATTERNS} >= REQUIRED_IDENTIFYING

    assert private_file_problem(tmp_path) is None
    assert load_identifying_patterns(tmp_path / PRIVATE_PATTERNS_RELATIVE) == []
    (tmp_path / PRIVATE_REPOSITORY_MARKER).write_text("requirements", encoding="utf-8")
    assert private_file_problem(tmp_path) is not None
    (tmp_path / "docs" / "qa").mkdir(parents=True)
    (tmp_path / PRIVATE_PATTERNS_RELATIVE).write_text("", encoding="utf-8")
    assert private_file_problem(tmp_path) is None


def test_every_pattern_of_the_private_file_has_both_kinds_of_example() -> None:
    """Oracle: spec-anchored — task HAR-9130-002 "jedes Muster mit einem Positiv- und einem
    Negativbeispiel", kept for the private file by task HAR-9130-030."""
    for pattern in IDENTIFYING_PATTERNS:
        assert pattern.positive and pattern.negative, pattern.name


def test_no_file_of_the_public_export_names_the_owner_or_their_finances() -> None:
    """Oracle: spec-anchored — UC NF7 "keine persönlichen Daten im Repo" and task HAR-9130-002: every
    text file the manifest exports is free of the private patterns. The failure names file, line and
    pattern, never the matched text, so a CI log does not repeat what it guards."""
    exported = public_export.exportable_files(ROOT, _manifest())
    assert len(exported) >= MIN_EXPORTED_FILES
    hits = []
    for relative in exported:
        text = _text_or_none(relative)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            hits += [f"{relative}:{number} {name}" for name, p in PRIVATE_PATTERNS.items() if p.search(line)]
    assert hits == []


def test_no_excluded_file_is_ever_selected() -> None:
    """Oracle: spec-anchored — task HAR-9130-002 "beweist, dass jede Datei der exclude-Liste NICHT im
    Manifest-Ergebnis ist": neither a tracked file an exclude pattern matches nor one of the
    hand-picked private paths is selected."""
    manifest = _manifest()
    tracked = public_export.tracked_files(ROOT)
    excluded = {path for path in tracked if public_export.is_excluded(path, manifest)}
    assert len(excluded) > 0, "the private documents are tracked, so something must be excluded"
    assert excluded.isdisjoint(public_export.exportable_files(ROOT, manifest))
    assert public_export.select(MUST_STAY_PRIVATE, manifest) == []


def test_the_exporter_dry_run_writes_nothing_and_its_self_check_finds_an_excluded_file(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Oracle: spec-anchored — task HAR-9130-002: the default is a dry run that only lists, the run
    prints the number of files, and an excluded file in the target ends it with exit code 1."""
    expected = len(public_export.exportable_files(ROOT, _manifest()))

    assert public_export.main(["--target", str(tmp_path)]) == public_export.EXIT_CLEAN
    assert list(tmp_path.iterdir()) == []
    assert f"would export {expected} files" in capsys.readouterr().out

    planted = tmp_path / "docs" / "specs" / "leftover.md"
    planted.parent.mkdir(parents=True)
    planted.write_text("private", encoding="utf-8")
    assert public_export.main(["--target", str(tmp_path)]) == public_export.EXIT_EXCLUDED_IN_TARGET
    assert "docs/specs/leftover.md" in capsys.readouterr().err


def test_the_exporter_copies_exactly_the_selection_and_refuses_a_target_inside_the_repository(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Oracle: spec-anchored — task HAR-9130-002 "kopiert die Treffer in ein Zielverzeichnis ...
    schreibt nichts ausserhalb von --target": an executed run leaves exactly the selected files in the
    target, and a target inside this repository is refused before anything is written."""
    expected = public_export.exportable_files(ROOT, _manifest())
    target = tmp_path / "public"

    assert public_export.main(["--target", str(target), "--execute"]) == public_export.EXIT_CLEAN
    copied = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert copied == expected
    assert f"exported {len(expected)} files" in capsys.readouterr().out

    inside = ROOT / "public-export-test"
    assert public_export.main(["--target", str(inside), "--execute"]) == public_export.EXIT_USAGE
    assert not inside.exists()
    assert public_export.main(["--execute"]) == public_export.EXIT_USAGE


def test_every_relative_link_of_an_exported_document_stays_inside_the_export() -> None:
    """Oracle: spec-anchored — task HAR-9130-028 (5): "every relative link of every exported .md
    resolves inside the selection", so the public repository has no dead link into an excluded file."""
    exported = public_export.exportable_files(ROOT, _manifest())
    selection = set(exported)
    dead = [
        problem
        for relative in exported
        if relative.endswith(".md")
        for target in _relative_links(_text_or_none(relative) or "")
        if (problem := _dead_link(relative, target, selection)) is not None
    ]
    assert dead == []


def _dead_link(relative: str, target: str, selection: set[str]) -> str | None:
    """The problem with one link of an exported document, or None when it resolves inside the export."""
    resolved = (ROOT / relative).parent.joinpath(target).resolve()
    if not resolved.is_relative_to(ROOT):
        return f"{relative}: {target} (outside the repository)"
    path = resolved.relative_to(ROOT).as_posix()
    if path in selection or any(file.startswith(f"{path}/") for file in selection):
        return None
    return f"{relative}: {target}"
