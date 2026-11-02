"""What goes into the frozen application (UC NF10).

PyInstaller finds imports by reading the code, and z-finance hides two of them on purpose: feature
routers are discovered at runtime with ``pkgutil.iter_modules`` (ARCHITECTURE.md "Feature routers"),
and uvicorn picks its loop and protocol implementations by name. Neither is visible to a static
analysis, so both are listed here — a feature package that is missing from the bundle silently
loses its API (ARCHITECTURE.md "Known risks").

The lists are plain functions over the source tree rather than constants, so a new feature is in
the bundle the moment its package exists. `packaging/z-finance.spec` reads them, and
`tests/test_packaging.py` checks them against the tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "z-finance"
#: The script the installed shortcut runs.
ENTRY_SCRIPT = "zfinance_launcher.py"

PACKAGING_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGING_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
PACKAGE_DIR = BACKEND_DIR / "zfinance"

#: uvicorn resolves these by name at runtime; PyInstaller cannot see that.
UVICORN_IMPORTS = (
    "uvicorn.lifespan.off",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    "uvicorn.loops.asyncio",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
)

#: pystray chooses its backend from the platform at import time.
_TRAY_BACKENDS = {"win32": "pystray._win32", "darwin": "pystray._darwin", "linux": "pystray._xorg"}


def domain_packages(package_dir: Path = PACKAGE_DIR) -> list[str]:
    """Every feature package under ``domain/``, sorted — the ones the router discovery imports."""
    domain = package_dir / "domain"
    return sorted(child.name for child in domain.iterdir() if (child / "__init__.py").is_file())


def tray_imports(platform: str = sys.platform) -> list[str]:
    """The pystray backend of ``platform``; empty for a platform pystray does not support."""
    backend = _TRAY_BACKENDS.get("linux" if platform.startswith("linux") else platform)
    return [backend] if backend else []


def hidden_imports(package_dir: Path = PACKAGE_DIR, platform: str = sys.platform) -> list[str]:
    """Modules the frozen app imports by name: every feature package, its router, uvicorn, pystray."""
    features = domain_packages(package_dir)
    return [
        *(f"zfinance.domain.{name}" for name in features),
        *(f"zfinance.domain.{name}.api" for name in features if (package_dir / "domain" / name / "api.py").is_file()),
        *UVICORN_IMPORTS,
        *tray_imports(platform),
    ]


def data_files(package_dir: Path = PACKAGE_DIR) -> list[tuple[str, str]]:
    """``(source, destination)`` pairs for everything the app reads from disk at runtime.

    The destinations mirror the package layout, because the code resolves them relative to
    ``__file__`` (``config.DEFAULT_WEB_DIR``, ``migrator.MIGRATIONS_DIR``, ``i18n.CATALOG_DIR``).
    The manual chapters are not here: Vite embeds them into the SPA bundle at build time.
    """
    entries: list[tuple[Path, str]] = [
        (package_dir / "web", "zfinance/web"),
        (package_dir / "migrations", "zfinance/migrations"),
    ]
    entries += [
        (catalog, f"zfinance/i18n/{catalog.parent.name}/LC_MESSAGES")
        for catalog in sorted((package_dir / "i18n").glob("*/LC_MESSAGES"))
    ]
    return [(str(source), destination) for source, destination in entries]


def missing_data_sources(package_dir: Path = PACKAGE_DIR) -> list[str]:
    """Data sources that do not exist yet — the SPA bundle before ``npm run build`` has run."""
    return [source for source, _ in data_files(package_dir) if not Path(source).exists()]
