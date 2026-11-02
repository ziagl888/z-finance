# Install protocol in Windows Sandbox

These scripts do the click protocol of `docs/qa/install-protocol.md`, section 3, on a clean
Windows instance. Windows Sandbox gives that instance: no Python, no Node, no old z-finance. The
scripts click like a lay user, by the visible name of each button (UI Automation). They do not use
a silent install.

## Files

| File                        | Where it runs | What it does                                                                              |
| --------------------------- | ------------- | ----------------------------------------------------------------------------------------- |
| `run_protocol.py`           | host          | Writes the `.wsb` file, starts the sandbox, waits for `done.json`, prints the table rows. |
| `install_protocol.ps1`      | sandbox       | Steps 1–10 and the main loop. Writes `protocol.json` after each step.                     |
| `install_protocol_late.ps1` | sandbox       | Steps 11–15.                                                                              |
| `sandbox_apps.ps1`          | sandbox       | Setup wizard, tray menu, browser and Start menu helpers.                                  |
| `sandbox_uia.ps1`           | sandbox       | UI Automation, mouse, keyboard and screenshot helpers.                                    |

## Run it

1. Turn on the Windows feature `Containers-DisposableClientVM` (administrator, then restart).
2. Put the setup file in an empty folder, for example with
   `gh release download <tag> --repo ziagl888/z-finance --pattern "*.exe" --dir <folder>`.
   Do not put it in the repository.
3. From the repository root:

   ```
   uv run python tools/sandbox/run_protocol.py --installer-dir <folder> --results-dir <new empty folder> [--steps 1-3]
   ```

A sandbox window opens on your screen. Do not use mouse or keyboard while it runs: the script
moves the mouse inside the sandbox. A full run takes about 15 minutes. The sandbox shuts itself
down at the end. The results folder then holds `protocol.json`, `transcript.txt` and one
`install-NN-<name>.png` for each screen.

`--render <results folder>/protocol.json` prints the table rows of an earlier run again.

`--update-while-running` changes step 13: Setup starts while z-finance still runs. Setup must ask
to close z-finance. The script then chooses "Quit" in the tray and clicks OK in Setup, as the
manual says. The result is an extra row `13b` after row 13. Without the switch, step 13 quits
z-finance first, as the protocol describes.

## What the sandbox does not show

- **The download (step 1).** The repository is private and the sandbox has no GitHub login. The
  host downloads the file; the sandbox reads it from a read-only folder.
- **SmartScreen (step 2).** Windows Sandbox has no SmartScreen app. A double-click on a file with a
  Mark of the Web opens "Search for app in the Store?" instead of the warning. The script records
  this as "not run", removes the mark and starts the setup again for step 3.
- **The language.** The sandbox runs in English (`en-US`), also on a German host. The script finds
  buttons in English and German.
- **The Edge first-run pages.** The script sets Edge policies in the sandbox so that the Edge
  welcome pages do not cover z-finance. This changes the browser, not z-finance.
- **A real user.** The user is `WDAGUtilityAccount`. A real machine has a user name in the paths.
