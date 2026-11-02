"""The host side of the Windows Sandbox install protocol (tools/sandbox/run_protocol.py).

What can be checked without a sandbox: the .wsb file, the logon command and the rendering of the
sandbox's protocol.json into the rows of docs/qa/install-protocol.md, section 3.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET  # nosec B405 - parses XML this test wrote itself
from pathlib import Path

import pytest

from sandbox import run_protocol as tool


def test_mappings_keep_installer_and_scripts_read_only(tmp_path: Path) -> None:
    """Oracle: spec-anchored — task: installer folder read-only, results folder read-write."""
    mappings = tool.sandbox_mappings(tmp_path / "in", tmp_path / "out", tmp_path / "scripts")

    by_target = {m.sandbox: m.read_only for m in mappings}

    assert by_target == {r"C:\zf\installer": True, r"C:\zf\scripts": True, r"C:\zf\results": False}


def test_wsb_is_valid_xml_with_the_logon_command(tmp_path: Path) -> None:
    """Oracle: spec-anchored — task: MappedFolder per folder plus a LogonCommand that starts the script."""
    host = tmp_path / "a&b"
    mappings = tool.sandbox_mappings(host, tmp_path / "out", tmp_path / "s")

    root = ET.fromstring(tool.build_wsb(mappings, tool.logon_command("1-3", shutdown=True)))  # noqa: S314 # nosec B314

    folders = root.findall("./MappedFolders/MappedFolder")
    assert folders[0].findtext("HostFolder") == str(host.resolve())
    assert [f.findtext("ReadOnly") for f in folders] == ["true", "true", "false"]
    command = root.findtext("./LogonCommand/Command") or ""
    expected = r"install_protocol.ps1 -InstallerDir C:\zf\installer -ResultsDir C:\zf\results -Steps 1-3 -Shutdown"
    assert command.endswith(expected)


@pytest.mark.parametrize("steps", ["1-3; calc", "a", "1 -Shutdown"])
def test_logon_command_refuses_anything_but_step_numbers(steps: str) -> None:
    """Oracle: spec-anchored — the step list ends up in a command line; only digits, '-' and ',' pass."""
    with pytest.raises(ValueError, match="steps must look like"):
        tool.logon_command(steps, shutdown=False)


def test_client_query_is_bound_to_this_runs_sandbox_process() -> None:
    """Oracle: spec-anchored — task: stop only the WindowsSandbox process of this run (and its clients)."""
    command = tool.client_query(4242)

    assert command[:3] == ["powershell.exe", "-NoProfile", "-Command"]
    assert "Name='WindowsSandboxClient.exe' AND ParentProcessId=4242" in command[3]


@pytest.mark.parametrize("pid", [0, -1])
def test_client_query_refuses_a_pid_that_would_match_other_processes(pid: int) -> None:
    """Oracle: spec-anchored — a non-positive parent id could select foreign sandboxes."""
    with pytest.raises(ValueError, match="parent pid must be positive"):
        tool.client_query(pid)


def test_rows_cover_all_fifteen_steps_and_mark_missing_ones_not_run() -> None:
    """Oracle: spec-anchored — install-protocol.md: never pass without evidence; a step not reached is 'not run'."""
    protocol = {
        "steps": [
            {"number": 2, "result": "pass", "evidence": ["SmartScreen warned"], "notes": []},
            {"number": 1, "result": "not run", "evidence": [], "notes": ["private repository"]},
        ]
    }

    rows = tool.protocol_rows(protocol)

    assert len(rows) == 15
    assert rows[0] == "| 1 | not run — private repository |"
    assert rows[1] == "| 2 | pass — SmartScreen warned |"
    assert rows[2] == "| 3 | not run (the sandbox run did not reach this step) |"


def test_rows_never_break_the_table_on_a_pipe_in_the_evidence() -> None:
    """Oracle: spec-anchored — a Markdown table cell must not contain '|' (menu entries are joined with it)."""
    protocol = {"steps": [{"number": 6, "result": "pass", "evidence": ["Open | Quit"], "notes": []}]}

    assert tool.protocol_rows(protocol)[5] == "| 6 | pass — Open / Quit |"


def test_wait_for_done_sees_the_done_file(tmp_path: Path) -> None:
    """Oracle: spec-anchored — task: the host waits for a completion file in the results folder."""
    (tmp_path / tool.DONE_FILE).write_text("{}", "utf-8")

    assert tool.wait_for_done(tmp_path, timeout=0.1, poll=0.01) is True


def test_wait_for_done_gives_up_after_the_timeout(tmp_path: Path) -> None:
    """Oracle: spec-anchored — task: wait with a timeout."""
    assert tool.wait_for_done(tmp_path, timeout=0.05, poll=0.01) is False


def test_wait_for_done_stops_when_the_sandbox_process_is_gone(tmp_path: Path) -> None:
    """Oracle: spec-anchored — a crashed or closed sandbox must not make the host sit out the timeout."""

    class Gone:
        def poll(self) -> int:
            return 1

    started = time.monotonic()
    assert tool.wait_for_done(tmp_path, timeout=5, poll=0.01, process=Gone()) is False  # type: ignore[arg-type]
    assert time.monotonic() - started < 1


def test_render_prints_rows_of_an_existing_protocol(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Oracle: spec-anchored — the sandbox writes protocol.json with a BOM (PowerShell 5.1 UTF8)."""
    path = tmp_path / "protocol.json"
    path.write_text('{"steps": [{"number": 1, "result": "pass", "evidence": ["x"], "notes": []}]}', "utf-8-sig")

    assert tool.main(["--render", str(path)]) == 0

    assert "| 1 | pass — x |" in capsys.readouterr().out


def test_run_refuses_a_results_folder_that_is_not_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — evidence of two runs must never mix in one folder."""
    monkeypatch.setattr(tool, "SANDBOX_EXE", tmp_path / "sandbox.exe")
    (tmp_path / "sandbox.exe").write_text("", "utf-8")
    installer = tmp_path / "in"
    installer.mkdir()
    (installer / "z-finance-setup-1.0.0.exe").write_text("", "utf-8")
    results = tmp_path / "out"
    results.mkdir()
    (results / "old.png").write_text("", "utf-8")

    assert tool.run(installer, results, "1-3", timeout=0) == 2


def test_logon_command_passes_the_update_while_running_switch() -> None:
    """Oracle: spec-anchored — task: a switch makes step 13 start Setup over the running program."""
    command = tool.logon_command("1-15", shutdown=True, update_while_running=True)

    assert command.endswith("-Steps 1-15 -UpdateWhileRunning -Shutdown")


def test_logon_command_keeps_step_13_as_in_the_protocol_by_default() -> None:
    """Oracle: spec-anchored — task: the default leaves step 13 as the protocol describes it."""
    assert "-UpdateWhileRunning" not in tool.logon_command("1-15", shutdown=True)


def test_rows_put_13b_right_after_13_when_the_run_wrote_it() -> None:
    """Oracle: spec-anchored — task: the update over the running program is an extra row 13b."""
    protocol = {
        "steps": [
            {"number": 13, "label": "13b", "result": "pass", "evidence": ["Setup asked"], "notes": []},
            {"number": 13, "result": "pass", "evidence": ["same folder"], "notes": []},
        ]
    }

    rows = tool.protocol_rows(protocol)

    assert len(rows) == 16
    assert rows[12:14] == ["| 13 | pass — same folder |", "| 13b | pass — Setup asked |"]
    assert rows[14].startswith("| 14 | not run")


def test_rows_have_no_13b_without_the_switch() -> None:
    """Oracle: spec-anchored — a default run has the fifteen rows of the protocol, no more."""
    rows = tool.protocol_rows({"steps": [{"number": 13, "result": "pass", "evidence": [], "notes": []}]})

    assert len(rows) == 15
    assert not any(row.startswith("| 13b ") for row in rows)
