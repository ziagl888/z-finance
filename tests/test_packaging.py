"""What the installer is made of (SPEC AK20, UC NF10/NF2/NF7).

The PyInstaller and Inno Setup runs themselves need the toolchain and belong to the build machine.
What can be checked without it is what those runs are told to do: the bundle contents, the two
promises of the installer script (per-user, hands off the data directory), the release workflow,
the limits, and the guide a lay user reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import build_installer
import bundle
import zfinance_launcher

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGING = REPO_ROOT / "packaging"
MANUAL = REPO_ROOT / "docs" / "manual"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

#: Hosts the installed program may name. Anything else would be an outbound path nobody asked for.
ALLOWED_HOSTS = frozenset({"api.github.com", "github.com", "127.0.0.1"})
#: The files that end up on the user's machine (build_installer.py stays on the build machine).
SHIPPED_SOURCES = (
    PACKAGING / "zfinance_launcher.py",
    PACKAGING / "bundle.py",
    REPO_ROOT / "backend" / "zfinance" / "domain" / "update_check" / "service.py",
    REPO_ROOT / "backend" / "zfinance" / "domain" / "update_check" / "api.py",
)

_URL = re.compile(r"https?://([A-Za-z0-9.\-]+)")


def iss_text() -> str:
    return (PACKAGING / "z-finance.iss").read_text(encoding="utf-8")


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


# --- bundle contents (AK1) -----------------------------------------------------------------------


def test_hidden_imports_cover_every_domain_package() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK1 + ARCHITECTURE "Known risks":
    routers are found with pkgutil at runtime, so every feature package must be a hidden import."""
    on_disk = {
        child.name
        for child in (REPO_ROOT / "backend" / "zfinance" / "domain").iterdir()
        if (child / "__init__.py").is_file()
    }
    hidden = set(bundle.hidden_imports())
    assert on_disk  # the tree really was read
    assert {f"zfinance.domain.{name}" for name in on_disk} <= hidden
    assert "zfinance.domain.update_check.api" in hidden


def test_hidden_imports_cover_uvicorn_and_the_tray() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK1: uvicorn and pystray resolve by name."""
    hidden = set(bundle.hidden_imports(platform="win32"))
    assert {"uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.lifespan.on"} <= hidden
    assert "pystray._win32" in hidden
    assert bundle.tray_imports("solaris") == []


def test_data_files_cover_web_migrations_and_catalogs() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK1: everything read from disk is bundled."""
    destinations = {destination for _, destination in bundle.data_files()}
    assert "zfinance/web" in destinations
    assert "zfinance/migrations" in destinations
    assert "zfinance/i18n/de/LC_MESSAGES" in destinations


def test_the_spa_bundle_is_reported_as_missing_before_it_is_built(tmp_path: Path) -> None:
    """Oracle: spec-anchored — bundle.missing_data_sources contract: a build without `just build`
    must fail loudly, because the frozen app would answer 503 instead of showing an interface."""
    package = tmp_path / "zfinance"
    (package / "migrations").mkdir(parents=True)
    (package / "i18n").mkdir()
    (package / "domain").mkdir()
    assert bundle.missing_data_sources(package) == [str(package / "web")]


# --- installer script (AK5a, AK5b, AK5c) ---------------------------------------------------------


def test_inno_script_never_touches_the_data_directory() -> None:
    """Oracle: spec-anchored — UC NF2 + SPEC-T-2026-CU-9130-027 AK5a: update and uninstall keep the data."""
    # Comments are where the rule is explained, so they are not what the rule is checked against.
    directives = [line for line in iss_text().lower().splitlines() if not line.strip().startswith(";")]
    assert "[uninstalldelete]" not in "\n".join(directives)
    for line in directives:
        assert "{localappdata}" not in line
        assert "{userappdata}" not in line


def test_inno_script_is_a_per_user_install_with_shortcuts() -> None:
    """Oracle: spec-anchored — UC NF10: no administrator password, Start menu entry, desktop icon."""
    text = iss_text()
    assert "PrivilegesRequired=lowest" in text
    assert "PrivilegesRequiredOverridesAllowed" not in text
    assert "{autoprograms}\\{#AppName}" in text
    assert "{autodesktop}\\{#AppName}" in text
    assert "Tasks: desktopicon" in text


def test_inno_script_reinstalls_over_the_same_folder() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK5c: an update is one program, not two."""
    assert "UsePreviousAppDir=yes" in iss_text()


def test_inno_script_waits_for_the_running_program_by_its_mutex() -> None:
    """Oracle: spec-anchored — T-2026-HAR-9130-022 acceptance (B): Setup asks to quit a running
    z-finance. One name in two files: the .iss must name the mutex the launcher holds."""
    text = iss_text()
    defined = re.search(r'^#define AppMutexName "([^"]+)"$', text, re.MULTILINE)
    assert defined is not None
    assert defined.group(1) == zfinance_launcher.APP_MUTEX_NAME
    assert re.search(r"^AppMutex=\{#AppMutexName\}$", text, re.MULTILINE)
    # Inno Setup splits AppMutex at commas; the name must stay one mutex.
    assert "," not in zfinance_launcher.APP_MUTEX_NAME


def test_inno_script_installs_the_frozen_bundle() -> None:
    """Oracle: spec-anchored — packaging/README.md: the installer ships what PyInstaller produced."""
    assert 'Source: "{#SourceDir}\\*"; DestDir: "{app}"' in iss_text()


# --- release workflow (AK10, AK13) ---------------------------------------------------------------


def test_release_workflow_builds_on_windows_for_a_tag() -> None:
    """Oracle: spec-anchored — SPEC "Distribution": a tag builds the Windows installer."""
    text = workflow_text()
    assert 'tags: ["v*"]' in text
    assert "runs-on: windows-latest" in text
    assert "packaging/build_installer.py" in text


def test_release_workflow_attaches_the_installer() -> None:
    """Oracle: spec-anchored — SPEC "Distribution": the installer hangs on the GitHub release."""
    text = workflow_text()
    assert "gh release upload" in text
    assert "contents: write" in text


def run_scripts() -> list[str]:
    """The text of every `run:` step in the workflow (block scalars and one-liners)."""
    lines = workflow_text().splitlines()
    scripts: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)run:\s*(.*)$", line)
        if not match:
            continue
        indent, inline = len(match.group(1)), match.group(2)
        if inline != "|":
            scripts.append(inline)
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            body.append(following)
        scripts.append("\n".join(body))
    return scripts


def version_step() -> str:
    text = workflow_text()
    start = text.index("- name: Version from the tag")
    return text[start : text.index("- name:", start + 10)]


def test_release_tag_must_match_pyproject_version() -> None:
    """Oracle: spec-anchored — the program reports pyproject's version; a differing tag loops the update check."""
    step = version_step()
    assert "uv version --short" in step
    assert "differs from pyproject.toml" in step


def test_release_tag_must_be_a_plain_version() -> None:
    """Oracle: spec-anchored — the tag name is untrusted text and is validated before use."""
    assert r"'^v([0-9]+\.[0-9]+\.[0-9]+)(-[0-9A-Za-z.]+)?$'" in version_step()


def test_release_tag_commit_must_be_on_main() -> None:
    """Oracle: spec-anchored — only commits that went through main's gates can be released."""
    step = version_step()
    assert "merge-base --is-ancestor" in step
    assert "origin/main" in step
    assert "fetch-depth: 0" in workflow_text()


def test_release_candidates_are_marked_prerelease() -> None:
    """Oracle: spec-anchored — an -rc tag must not become /releases/latest for the update check."""
    text = workflow_text()
    assert "--prerelease" in text
    assert "$tag.Contains('-')" in text


def test_release_scripts_take_untrusted_values_only_through_env() -> None:
    """Oracle: spec-anchored — no `${{ }}` expression is expanded inside a run script (injection)."""
    scripts = run_scripts()
    assert len(scripts) >= 5
    for script in scripts:
        assert "${{" not in script, script
    assert 'build_installer.py --version "$env:VERSION"' in workflow_text()


def test_release_workflow_uses_only_the_builtin_token() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK13 / UC NF7: no secret of our own."""
    assert set(re.findall(r"secrets\.([A-Za-z_]+)", workflow_text())) == {"GITHUB_TOKEN"}


def test_no_foreign_host_is_contacted() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK13 / UC NF2: the program names no other host."""
    for source in SHIPPED_SOURCES:
        hosts = set(_URL.findall(source.read_text(encoding="utf-8")))
        assert hosts <= ALLOWED_HOSTS, f"{source.name} names {sorted(hosts - ALLOWED_HOSTS)}"


# --- limits (AK12) -------------------------------------------------------------------------------


def test_measurement_passes_inside_the_limits() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK12: inside 300 MB and 10 s there is no problem."""
    assert build_installer.check_limits(build_installer.MAX_BUNDLE_BYTES, build_installer.MAX_START_SECONDS) == []


def test_measurement_fails_above_the_limits() -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK12: the stop conditions of the task, in numbers."""
    too_big = build_installer.check_limits(build_installer.MAX_BUNDLE_BYTES + 1, 1.0)
    too_slow = build_installer.check_limits(1, build_installer.MAX_START_SECONDS + 0.1)
    assert len(too_big) == 1 and "bundle" in too_big[0]
    assert len(too_slow) == 1 and "cold start" in too_slow[0]
    assert build_installer.MAX_BUNDLE_BYTES == 300 * 1024 * 1024
    assert build_installer.MAX_START_SECONDS == 10.0


def test_a_missing_measurement_is_not_a_failure() -> None:
    """Oracle: spec-anchored — build_installer.check_limits contract: `--skip-measure` is allowed."""
    assert build_installer.check_limits(1, None) == []


# --- the guide a lay user reads (AK11, AK14) -----------------------------------------------------


@pytest.mark.parametrize("language", ["en", "de"])
def test_install_chapter_has_three_steps_and_names_smartscreen(language: str) -> None:
    """Oracle: spec-anchored — SPEC "Distribution" ("3 Schritte") + the task's SmartScreen condition."""
    text = (MANUAL / language / "install.md").read_text(encoding="utf-8")
    steps = [line for line in text.splitlines() if re.match(r"^[123]\. ", line)]
    assert len(steps) == 3, f"{language}/install.md has {len(steps)} numbered steps"
    assert "SmartScreen" in text or "Windows geschützt" in text or "Windows protected" in text
    assert "https://github.com/ziagl888/z-finance/releases" in text


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_first_run_chapter_exists(language: str) -> None:
    """Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK11 + ARCHITECTURE "Help and manual"."""
    assert (MANUAL / language / "first-run.md").is_file()


def test_readme_lay_guide_comes_before_the_developer_setup() -> None:
    """Oracle: spec-anchored — SPEC "Distribution": "README beginnt mit der Laien-Anleitung …
    Entwickler-Setup weiter unten"."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install = text.index("## Install (for everyone)")
    developer = text.index("## Developer setup")
    assert install < developer
    guide = text[install:developer]
    assert len([line for line in guide.splitlines() if re.match(r"^[123]\. ", line)]) == 3
    assert "docs/manual/en/install.md" in guide


def test_iscc_is_found_in_a_per_user_inno_setup_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compiler = tmp_path / "Programs" / "Inno Setup 6" / "ISCC.exe"
    compiler.parent.mkdir(parents=True)
    compiler.touch()
    monkeypatch.setattr(build_installer.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert build_installer.find_iscc() == compiler
