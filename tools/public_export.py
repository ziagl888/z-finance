"""Copy the public part of this repository into a target directory; a dry run unless told otherwise.

    uv run python tools/public_export.py                                  # list what would go out
    uv run python tools/public_export.py --target ../z-finance-public     # also check that folder
    uv run python tools/public_export.py --target ../z-finance-public --execute

The manifest ``packaging/public_export.toml`` says which tracked files (``git ls-files``) go into
the public repository: those an ``include`` pattern matches and no ``exclude`` pattern matches.
The script copies them and nothing else. It never pushes, never creates a repository and never
writes outside ``--target``; a target inside this repository is refused.

At the end it checks the target folder itself: a file there that the manifest excludes, whether
copied or left over from earlier, ends the run with exit code 1. The ``.git`` folder of a target
that is already a repository is not looked at.

Exit codes: 0 clean, 1 an excluded file is in the target, 2 a usage or manifest error.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess  # nosec B404 - runs only `git ls-files` with a fixed argument list
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "packaging" / "public_export.toml"

EXIT_CLEAN = 0
EXIT_EXCLUDED_IN_TARGET = 1
EXIT_USAGE = 2

GIT_DIR = ".git"


@dataclass(frozen=True)
class Manifest:
    """The include and exclude patterns of ``[export]``; exclude wins over include."""

    include: tuple[str, ...]
    exclude: tuple[str, ...]


def load_manifest(path: Path) -> Manifest:
    """Read ``[export]`` from the manifest; raises ``ValueError`` when a list is missing or empty."""
    section = tomllib.loads(path.read_text(encoding="utf-8")).get("export", {})
    lists = {}
    for name in ("include", "exclude"):
        patterns = section.get(name)
        if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) and p for p in patterns):
            raise ValueError(f"{path}: [export].{name} must be a non-empty list of patterns")
        lists[name] = tuple(patterns)
    return Manifest(include=lists["include"], exclude=lists["exclude"])


def matches(path: str, pattern: str) -> bool:
    """Whether a repository-relative ``/`` path matches a manifest pattern.

    ``dir/`` matches everything below that directory; any other pattern is an ``fnmatch`` glob over
    the whole path, case-sensitive, in which ``*`` also crosses ``/``.
    """
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return fnmatch.fnmatchcase(path, pattern)


def is_excluded(path: str, manifest: Manifest) -> bool:
    """Whether any exclude pattern matches the path."""
    return any(matches(path, pattern) for pattern in manifest.exclude)


def select(paths: list[str], manifest: Manifest) -> list[str]:
    """The paths an include pattern matches and no exclude pattern matches, sorted."""
    return sorted(
        path
        for path in paths
        if any(matches(path, pattern) for pattern in manifest.include) and not is_excluded(path, manifest)
    )


def tracked_files(source: Path) -> list[str]:
    """The files git tracks in ``source``; raises ``CalledProcessError`` outside a repository."""
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("git is not on PATH")
    result = subprocess.run(  # noqa: S603  # nosec B603 - fixed argument list
        [git, "ls-files", "-z"], cwd=source, capture_output=True, check=True
    )
    return [path for path in result.stdout.decode("utf-8").split("\0") if path]


def exportable_files(source: Path, manifest: Manifest) -> list[str]:
    """The tracked files of ``source`` the manifest exports and that exist in the working tree."""
    return [path for path in select(tracked_files(source), manifest) if (source / path).is_file()]


def copy_files(source: Path, files: list[str], target: Path) -> None:
    """Copy each file from ``source`` to the same relative path below ``target``."""
    for relative in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)


def excluded_in_target(target: Path, manifest: Manifest) -> list[str]:
    """The files below ``target`` (its ``.git`` folder skipped) that the manifest excludes."""
    found = []
    for folder, subfolders, files in os.walk(target):
        if Path(folder) == target and GIT_DIR in subfolders:
            subfolders.remove(GIT_DIR)
        for name in files:
            relative = (Path(folder) / name).relative_to(target).as_posix()
            if is_excluded(relative, manifest):
                found.append(relative)
    return sorted(found)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=Path, help="folder that receives the export")
    parser.add_argument("--execute", action="store_true", help="copy the files (default: dry run)")
    parser.add_argument("--source", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _usage_error(message: str) -> int:
    print(f"public_export: {message}", file=sys.stderr)
    return EXIT_USAGE


def _target_problem(args: argparse.Namespace, source: Path, target: Path | None) -> str | None:
    """Why the target arguments cannot be used, or ``None`` when they can."""
    if args.execute and target is None:
        return "--execute needs --target"
    if target is not None and target.is_relative_to(source):
        return f"the target {target} lies inside the source repository"
    return None


def _emit(source: Path, files: list[str], target: Path | None, execute: bool) -> None:
    """Copy the files when executing, otherwise list them (the dry run)."""
    if execute and target is not None:
        copy_files(source, files, target)
        return
    for path in files:
        print(path)


def _report_leftovers(target: Path | None, manifest: Manifest) -> int:
    """Print every excluded file left in the target and return their number."""
    leftovers = excluded_in_target(target, manifest) if target is not None and target.is_dir() else []
    for path in leftovers:
        print(f"excluded file in the target: {path}", file=sys.stderr)
    return len(leftovers)


def main(argv: list[str] | None = None) -> int:
    """Run the export (or the dry run) and the target check; returns the exit code."""
    args = _parse(argv)
    source = args.source.resolve()
    target = args.target.resolve() if args.target else None
    problem = _target_problem(args, source, target)
    if problem is not None:
        return _usage_error(problem)
    try:
        manifest = load_manifest(args.manifest)
        files = exportable_files(source, manifest)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return _usage_error(str(error))

    _emit(source, files, target, args.execute)
    leftover_count = _report_leftovers(target, manifest)
    print(f"{'exported' if args.execute else 'dry run, would export'} {len(files)} files")
    return EXIT_EXCLUDED_IN_TARGET if leftover_count else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
