"""Run the clean-machine install protocol in Windows Sandbox and collect what it saw.

The host side of docs/qa/install-protocol.md, section 3: write a ``.wsb`` configuration, start
``WindowsSandbox.exe`` with it, wait for the sandbox script to leave ``done.json`` in the results
folder, and print the protocol rows as Markdown. Nothing is installed on the host; the installer
runs only inside the disposable sandbox, and the host's own z-finance data folder is never read.

Usage (from the repository root)::

    py tools/sandbox/run_protocol.py --installer-dir <folder with the setup exe> \
        --results-dir <empty folder> [--steps 1-3] [--timeout 1800] [--update-while-running]
    py tools/sandbox/run_protocol.py --render <results folder>/protocol.json

Only the ``WindowsSandbox.exe`` process this script started, and the client windows it spawned,
are ever stopped.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - starts the sandbox executable with a fixed argument list
import sys
import time
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape  # nosec B406 - escape() only, nothing is parsed

SANDBOX_EXE = Path(r"C:\Windows\System32\WindowsSandbox.exe")
SANDBOX_ROOT = r"C:\zf"
SCRIPTS_DIR = Path(__file__).resolve().parent
DONE_FILE = "done.json"
PROTOCOL_FILE = "protocol.json"
DEFAULT_TIMEOUT_SECONDS = 1800
POLL_SECONDS = 5
SHUTDOWN_GRACE_SECONDS = 20
STEP_COUNT = 15
# Row 13b exists only in a run with --update-while-running: Setup started over the running program.
OPTIONAL_ROWS = {13: ("13b",)}


@dataclass(frozen=True)
class Mapping:
    """One host folder made visible inside the sandbox."""

    host: Path
    sandbox: str
    read_only: bool


def sandbox_mappings(installer_dir: Path, results_dir: Path, scripts_dir: Path = SCRIPTS_DIR) -> tuple[Mapping, ...]:
    """The three folders the sandbox sees: installer and scripts read-only, results read-write."""
    return (
        Mapping(installer_dir.resolve(), SANDBOX_ROOT + r"\installer", read_only=True),
        Mapping(scripts_dir.resolve(), SANDBOX_ROOT + r"\scripts", read_only=True),
        Mapping(results_dir.resolve(), SANDBOX_ROOT + r"\results", read_only=False),
    )


def logon_command(steps: str, shutdown: bool, update_while_running: bool = False) -> str:
    """The PowerShell line the sandbox runs at logon.

    ``update_while_running`` makes step 13 start Setup while z-finance still runs (row 13b).
    """
    if not steps.replace("-", "").replace(",", "").isdigit():
        raise ValueError(f"steps must look like '1-15' or '1,2,5', got {steps!r}")
    command = (
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File "
        f"{SANDBOX_ROOT}\\scripts\\install_protocol.ps1 -InstallerDir {SANDBOX_ROOT}\\installer "
        f"-ResultsDir {SANDBOX_ROOT}\\results -Steps {steps}"
    )
    return command + (" -UpdateWhileRunning" if update_while_running else "") + (" -Shutdown" if shutdown else "")


def build_wsb(mappings: Sequence[Mapping], command: str) -> str:
    """The ``.wsb`` XML: mapped folders, networking on (SmartScreen and the update check), no vGPU."""
    folders = "".join(
        "    <MappedFolder>\n"
        f"      <HostFolder>{escape(str(m.host))}</HostFolder>\n"
        f"      <SandboxFolder>{escape(m.sandbox)}</SandboxFolder>\n"
        f"      <ReadOnly>{'true' if m.read_only else 'false'}</ReadOnly>\n"
        "    </MappedFolder>\n"
        for m in mappings
    )
    return (
        "<Configuration>\n"
        "  <VGpu>Disable</VGpu>\n"
        "  <Networking>Enable</Networking>\n"
        "  <ClipboardRedirection>Disable</ClipboardRedirection>\n"
        "  <MappedFolders>\n"
        f"{folders}"
        "  </MappedFolders>\n"
        "  <LogonCommand>\n"
        f"    <Command>{escape(command)}</Command>\n"
        "  </LogonCommand>\n"
        "</Configuration>\n"
    )


def _row_key(step: MappingABC[str, object]) -> str:
    """A step's row: its ``label`` (such as "13b") when it has one, else its number."""
    return str(step.get("label") or int(str(step["number"])))


def _cell(step: MappingABC[str, object]) -> str:
    raw = [*_as_list(step.get("evidence")), *_as_list(step.get("notes"))]
    text = "; ".join(str(item) for item in raw if item)
    result = str(step.get("result", "not run"))
    return (f"{result} — {text}" if text else result).replace("|", "/")


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def protocol_rows(protocol: MappingABC[str, object]) -> list[str]:
    """One Markdown row per step 1..15, plus an optional row such as 13b when the run wrote it.

    Each row holds the verdict, then its evidence; a missing required step is "not run".
    """
    raw_steps = protocol.get("steps", [])
    steps = raw_steps if isinstance(raw_steps, list) else []
    by_key = {_row_key(step): step for step in steps if isinstance(step, dict) and "number" in step}
    rows = []
    for number in range(1, STEP_COUNT + 1):
        step = by_key.get(str(number))
        if step is None:
            rows.append(f"| {number} | not run (the sandbox run did not reach this step) |")
        else:
            rows.append(f"| {number} | {_cell(step)} |")
        rows.extend(
            f"| {label} | {_cell(by_key[label])} |" for label in OPTIONAL_ROWS.get(number, ()) if label in by_key
        )
    return rows


def wait_for_done(
    results_dir: Path, timeout: float, poll: float = POLL_SECONDS, process: subprocess.Popen[bytes] | None = None
) -> bool:
    """True once ``done.json`` exists in the results folder; False after ``timeout`` seconds.

    With ``process`` the wait also ends as soon as that sandbox process has exited (a crash or a closed
    window), instead of sitting out the whole timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (results_dir / DONE_FILE).exists():
            return True
        if process is not None and process.poll() is not None:
            break
        time.sleep(poll)
    return (results_dir / DONE_FILE).exists()


def client_query(parent_pid: int) -> list[str]:
    """PowerShell command listing the sandbox client windows THIS run's sandbox process started."""
    if parent_pid <= 0:
        raise ValueError(f"parent pid must be positive, got {parent_pid}")
    flt = f"Name='WindowsSandboxClient.exe' AND ParentProcessId={parent_pid}"
    return [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        f'Get-CimInstance Win32_Process -Filter "{flt}" | ForEach-Object {{ $_.ProcessId }}',
    ]


def stop_own_sandbox(process: subprocess.Popen[bytes]) -> None:
    """Stop the sandbox this run started and the client windows it spawned — nothing else.

    The client outlives ``WindowsSandbox.exe`` and keeps an empty window open; it is found by its
    parent process id, so another sandbox on the machine is never touched.
    """
    listing = subprocess.run(client_query(process.pid), capture_output=True, text=True, check=False)  # noqa: S603 # nosec B603
    for line in listing.stdout.split():
        if line.isdigit():
            subprocess.run(["taskkill", "/F", "/PID", line], capture_output=True, check=False)  # noqa: S603, S607 # nosec
    if process.poll() is None:
        process.terminate()


def run(installer_dir: Path, results_dir: Path, steps: str, timeout: float, update_while_running: bool = False) -> int:
    """Start the sandbox, wait for the protocol, print the rows. Exit 0 done, 1 timeout, 2 setup error."""
    if not SANDBOX_EXE.exists():
        print("Windows Sandbox is not enabled (Containers-DisposableClientVM).", file=sys.stderr)
        return 2
    if not list(installer_dir.glob("z-finance-setup-*.exe")):
        print(f"no z-finance-setup-*.exe in {installer_dir}", file=sys.stderr)
        return 2
    results_dir.mkdir(parents=True, exist_ok=True)
    if any(results_dir.iterdir()):
        print(f"results folder {results_dir} is not empty", file=sys.stderr)
        return 2
    wsb = results_dir.parent / f"{results_dir.name}.wsb"
    wsb.write_text(
        build_wsb(sandbox_mappings(installer_dir, results_dir), logon_command(steps, True, update_while_running)),
        "utf-8",
    )
    process = subprocess.Popen([str(SANDBOX_EXE), str(wsb)])  # noqa: S603 # nosec B603 - fixed exe
    finished = wait_for_done(results_dir, timeout, process=process)
    if finished:
        # The sandbox shuts itself down after done.json; give it the time before cleaning up.
        time.sleep(SHUTDOWN_GRACE_SECONDS)
    stop_own_sandbox(process)
    protocol_path = results_dir / PROTOCOL_FILE
    if protocol_path.exists():
        print("\n".join(protocol_rows(json.loads(protocol_path.read_text("utf-8-sig")))))
    return 0 if finished else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry: run the sandbox, or only ``--render`` an existing protocol."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--installer-dir", type=Path)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--steps", default=f"1-{STEP_COUNT}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--update-while-running",
        action="store_true",
        help="step 13: start Setup while z-finance runs and record Setup's question as row 13b",
    )
    parser.add_argument("--render", type=Path, help="print the rows of an existing protocol.json and exit")
    args = parser.parse_args(argv)
    if args.render:
        print("\n".join(protocol_rows(json.loads(args.render.read_text("utf-8-sig")))))
        return 0
    if args.installer_dir is None or args.results_dir is None:
        parser.error("--installer-dir and --results-dir are required unless --render is given")
    return run(args.installer_dir, args.results_dir, args.steps, args.timeout, args.update_while_running)


if __name__ == "__main__":
    sys.exit(main())
