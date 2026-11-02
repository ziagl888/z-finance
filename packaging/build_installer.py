"""Build the Windows installer and measure it against the limits of the task (UC NF10).

    uv sync --group packaging
    just build                                  # the SPA bundle the app serves
    uv run python packaging/build_installer.py --version 0.1.0

Three steps, each skippable for a partial run: freeze the app with PyInstaller, measure it, and
compile the Inno Setup script. The measurement is the point of the script and not decoration — the
task stops at a bundle above 300 MB or a cold start above 10 seconds, and both are numbers nobody
can guess. Everything is written below ``packaging/build/`` (git-ignored).

The cold start is measured the way a user experiences it: start the frozen program, wait until
``/api/health`` answers, stop it again. It runs against a throwaway data directory, never the real
one.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import shutil
import subprocess  # nosec B404 - this build script runs PyInstaller and Inno Setup by design
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from bundle import APP_NAME, PACKAGING_DIR, missing_data_sources

logger = logging.getLogger("build_installer")

BUILD_DIR = PACKAGING_DIR / "build"
DIST_DIR = BUILD_DIR / "dist"
BUNDLE_DIR = DIST_DIR / APP_NAME
ICON_PATH = BUILD_DIR / f"{APP_NAME}.ico"
SPEC_PATH = PACKAGING_DIR / f"{APP_NAME}.spec"
ISS_PATH = PACKAGING_DIR / f"{APP_NAME}.iss"
#: Where the measured program writes while it is being timed (see ``measure_cold_start``).
MEASURE_LOG = BUILD_DIR / "measure.log"

#: Stop conditions of T-2026-CU-9130-027: above either of these the approach has to change
#: (Nuitka, a Tauri sidecar) instead of being shipped.
MAX_BUNDLE_BYTES = 300 * 1024 * 1024
MAX_START_SECONDS = 10.0
#: How long the measurement waits before it calls the start a failure rather than a slow start.
START_TIMEOUT_SECONDS = 60.0
_POLL_SECONDS = 0.2

#: Sizes a Windows .ico carries so the shortcut is crisp in every view.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

_ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)
#: Where Inno Setup lands when it is installed "for me only", below ``%LOCALAPPDATA%``.
_ISCC_PER_USER = Path("Programs") / "Inno Setup 6" / "ISCC.exe"


def write_icon(target: Path = ICON_PATH) -> Path:
    """Draw the application icon into ``target``.

    Drawn from code (the same mark the notification area shows) so the repository carries no binary
    image that nobody can review in a diff.
    """
    from zfinance_launcher import tray_icon_image

    target.parent.mkdir(parents=True, exist_ok=True)
    image = tray_icon_image(max(ICON_SIZES))
    image.save(target, format="ICO", sizes=[(size, size) for size in ICON_SIZES])
    return target


def bundle_size_bytes(directory: Path = BUNDLE_DIR) -> int:
    """Total size of the frozen program folder."""
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def check_limits(size_bytes: int, start_seconds: float | None) -> list[str]:
    """The stop conditions of the task as messages; an empty list means the bundle may ship."""
    problems = []
    if size_bytes > MAX_BUNDLE_BYTES:
        problems.append(
            f"bundle is {size_bytes / 1024 / 1024:.0f} MB, above the limit of {MAX_BUNDLE_BYTES // 1024 // 1024} MB"
        )
    if start_seconds is not None and start_seconds > MAX_START_SECONDS:
        problems.append(f"cold start took {start_seconds:.1f} s, above the limit of {MAX_START_SECONDS:.0f} s")
    return problems


def _health_answers(port: int) -> bool:
    """True once ``GET /api/health`` on the loopback address returns 200."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1.0)
    try:
        connection.request("GET", "/api/health")
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _running_port(data_dir: Path) -> int | None:
    """The port the started instance wrote into its runtime state, if it already did."""
    try:
        state = json.loads((data_dir / "runtime" / "launcher.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    port = state.get("port")
    return int(port) if isinstance(port, int) else None


def measure_cold_start(command: Sequence[str], timeout: float = START_TIMEOUT_SECONDS) -> float:
    """Seconds from starting ``command`` until the API answers. Raises when it never does.

    The program gets its own log file rather than this script's output. It is built windowed
    (``console=False``), and a windowed process that inherits a pipe as its standard output blocks
    on the first write as soon as nobody drains that pipe promptly — measured here: through a pipe
    the frozen program never got past its first log line, into a file it answered in about a
    second. A measurement must not depend on who is reading this script's output.
    """
    MEASURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="z-finance-measure-") as temporary,
        MEASURE_LOG.open("w", encoding="utf-8") as log,
    ):
        data_dir = Path(temporary)
        environment = {**os.environ, "ZFINANCE_DATA_DIR": str(data_dir)}
        started = time.monotonic()
        process = subprocess.Popen(  # noqa: S603 # nosec B603 - the frozen program this script built
            [*command, "--no-browser", "--no-tray"],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = started + timeout
            while time.monotonic() < deadline:
                port = _running_port(data_dir)
                if port is not None and _health_answers(port):
                    return time.monotonic() - started
                if process.poll() is not None:
                    raise RuntimeError(
                        f"the program stopped before answering (exit code {process.returncode}); "
                        f"its output is in {MEASURE_LOG}"
                    )
                time.sleep(_POLL_SECONDS)
            raise RuntimeError(f"the program did not answer within {timeout:.0f} s; its output is in {MEASURE_LOG}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - only a hung process gets here
                process.kill()


def run_pyinstaller() -> None:
    """Freeze the application into ``packaging/build/dist/z-finance/``."""
    missing = missing_data_sources()
    if missing:
        raise SystemExit(
            "missing build input: "
            + ", ".join(missing)
            + "\nRun `just build` first — without the SPA bundle the program has no user interface."
        )
    write_icon()
    if BUNDLE_DIR.exists():
        shutil.rmtree(BUNDLE_DIR)
    subprocess.run(  # noqa: S603 # nosec B603 - this interpreter, running PyInstaller
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR / "work"),
            str(SPEC_PATH),
        ],
        check=True,
        cwd=PACKAGING_DIR,
    )


def find_iscc() -> Path | None:
    """The Inno Setup compiler, from ``PATH`` or one of its usual places."""
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    local_app_data = os.environ.get("LOCALAPPDATA")
    per_user = (Path(local_app_data) / _ISCC_PER_USER,) if local_app_data else ()
    return next((candidate for candidate in (*_ISCC_CANDIDATES, *per_user) if candidate.is_file()), None)


def run_inno(version: str) -> Path:
    """Compile the installer and return the ``.exe`` it wrote."""
    iscc = find_iscc()
    if iscc is None:
        raise SystemExit(
            "Inno Setup 6 not found. Install it (https://jrsoftware.org/isdl.php) or run with --skip-installer."
        )
    subprocess.run(  # noqa: S603 # nosec B603 - the Inno Setup compiler found on this machine
        [str(iscc), f"/DAppVersion={version}", str(ISS_PATH)],
        check=True,
        cwd=PACKAGING_DIR,
    )
    return BUILD_DIR / f"{APP_NAME}-setup-{version}.exe"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(prog="build_installer", description=__doc__)
    parser.add_argument("--version", default="0.0.0", help="version written into the installer name")
    parser.add_argument("--skip-freeze", action="store_true", help="reuse an existing frozen bundle")
    parser.add_argument("--skip-measure", action="store_true", help="do not measure the cold start")
    parser.add_argument("--skip-installer", action="store_true", help="stop after the frozen bundle")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Build, measure, package. Returns 0 when the result is inside the limits of the task."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not args.skip_freeze:
        run_pyinstaller()
    if not BUNDLE_DIR.is_dir():
        raise SystemExit(f"no frozen bundle at {BUNDLE_DIR}")

    size = bundle_size_bytes()
    logger.info("bundle: %.0f MB (%s)", size / 1024 / 1024, BUNDLE_DIR)

    start_seconds: float | None = None
    if not args.skip_measure:
        start_seconds = measure_cold_start([str(BUNDLE_DIR / f"{APP_NAME}.exe")])
        logger.info("cold start: %.1f s", start_seconds)

    problems = check_limits(size, start_seconds)
    for problem in problems:
        logger.error("stop condition: %s", problem)
    if problems:
        logger.error("Report this to the task owner with the numbers above; the alternatives are Nuitka")
        logger.error("or a Tauri sidecar. Do not ship a bundle past the limits.")
        return 1

    if not args.skip_installer:
        installer = run_inno(args.version)
        logger.info("installer: %s (%.0f MB)", installer, installer.stat().st_size / 1024 / 1024)

    print(json.dumps({"bundle_bytes": size, "cold_start_seconds": start_seconds}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
