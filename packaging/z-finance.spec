# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows program (UC NF10, SPEC AK20).

One folder, not one file: a single executable unpacks itself into a temporary directory on every
start, which costs seconds the use case does not allow ("Doppelklick, Weiter, Fertig" and it runs).
What the frozen app needs but PyInstaller cannot see is listed in `bundle.py`.

Built by `packaging/build_installer.py`, which runs `npm run build` first — without the SPA bundle
the app starts and answers 503 with a build hint instead of showing a user interface.
"""

import sys
from pathlib import Path

sys.path.insert(0, SPECPATH)  # noqa: F821 - PyInstaller injects SPECPATH

from bundle import APP_NAME, ENTRY_SCRIPT, PACKAGE_DIR, data_files, hidden_imports  # noqa: E402

ICON_FILE = Path(SPECPATH) / "build" / f"{APP_NAME}.ico"  # noqa: F821 - written by build_installer.py

a = Analysis(  # noqa: F821 - PyInstaller injects Analysis
    [str(Path(SPECPATH) / ENTRY_SCRIPT)],  # noqa: F821
    pathex=[str(PACKAGE_DIR.parent)],
    binaries=[],
    datas=data_files(),
    hiddenimports=hidden_imports(),
    hookspath=[],
    runtime_hooks=[],
    # Nothing here is used by a local finance tool; leaving them out keeps the bundle small enough
    # for the 300 MB limit of the task.
    excludes=["tkinter", "unittest", "pydoc_data", "doctest", "pdb", "lib2to3"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console window: this is the program a lay user double-clicks, and it talks through the
    # browser and the notification area, never through a terminal.
    console=False,
    icon=str(ICON_FILE) if ICON_FILE.is_file() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
