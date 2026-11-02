# Packaging

The Windows program a lay user installs (UC NF10, SPEC AK20). Five files:

| File                   | What it is                                                                     |
| ---------------------- | ------------------------------------------------------------------------------ |
| `zfinance_launcher.py` | The program the shortcut runs: free port, single instance, tray icon, browser. |
| `bundle.py`            | What goes into the frozen app — the imports and data PyInstaller cannot see.   |
| `z-finance.spec`       | The PyInstaller spec (one folder, no console window).                          |
| `z-finance.iss`        | The Inno Setup script (per-user install, Start menu, optional desktop icon).   |
| `build_installer.py`   | Freeze, measure against the limits, compile the installer.                     |

Everything a build writes goes to `build/` (git-ignored): `build/dist/z-finance/` is the frozen
program, `build/z-finance.ico` the icon drawn from code, `build/z-finance-setup-<version>.exe` the
installer.

## Build it locally

```sh
uv sync --group packaging          # PyInstaller, pystray, pillow
just build                         # the SPA bundle; without it the app has no user interface
uv run python packaging/build_installer.py --version 0.1.0
```

Inno Setup 6 must be installed (<https://jrsoftware.org/isdl.php>), for all users or for the
current user only; the script also looks on `PATH`. Without it, run with
`--skip-installer` to get the frozen folder only. `--skip-freeze` reuses an existing bundle and
`--skip-measure` leaves out the cold-start measurement.

The script fails when the bundle is larger than 300 MB or the cold start takes more than 10
seconds. That is a stop condition, not a warning: report the numbers instead of shipping past them.

## How the program behaves

Started, it takes the first free port from 8765 upwards, writes its address to
`<data dir>/runtime/launcher.json`, opens the browser and shows an icon in the notification area
with **Open z-finance**, **Back up now**, **Open data folder** and **Quit** (in the language stored
in the settings). A second start finds the lock file next to that state, opens the browser at the
running instance and ends — one z-finance per data directory.

The data directory is the one the app always uses (`%LOCALAPPDATA%\z-finance`, UC NF2). The
installer never writes or deletes anything below it, so an update and an uninstall both leave the
data alone; `z-finance.iss` has no `[UninstallDelete]` section, and it must stay that way.

## Releases

`.github/workflows/release.yml` runs the same steps on a `v*` tag and attaches the installer to the
GitHub release. There is no code signing certificate, so Windows SmartScreen warns on the first
start of a new version — `docs/manual/en/install.md` tells the user what to click.
