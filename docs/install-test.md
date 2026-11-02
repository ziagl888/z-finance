# Install test on a clean Windows machine

This page shows the results of two test installs of the Windows installer. Each test used a new,
empty Windows Sandbox. The sandbox had no Python, no Node and no earlier z-finance. A script did
the clicks on the visible controls of each screen. No step used a silent install.

The results are for two pre-releases of 1.0.0: `1.0.0-rc.2` (run 1) and `1.0.0-rc.4` (run 2).
Version 1.0.0 contains the fixes that run 2 shows.

## Test machine

| Field   | Value                                                                      |
| ------- | -------------------------------------------------------------------------- |
| Date    | 2026-11-02 (both runs)                                                     |
| Machine | Windows Sandbox on Windows 11 Pro, a new instance for each run             |
| Windows | build 22621, display language English (`en-US`), user `WDAGUtilityAccount` |
| Checked | no `python.exe` and no `node.exe` on the path, no earlier z-finance folder |
| Files   | `z-finance-setup-1.0.0-rc.2.exe` and `z-finance-setup-1.0.0-rc.4.exe`      |

## Results

"Pass" means: the check of the script held, and a picture or a log line shows it.

| #   | Step                                              | Expected                                                                   | Run 1 (rc.2) | Run 2 (rc.4) |
| --- | ------------------------------------------------- | -------------------------------------------------------------------------- | ------------ | ------------ |
| 1   | Download the setup file from the releases page    | The download works                                                         | not run      | not run      |
| 2   | Double-click it                                   | SmartScreen warns; "More info" and then "Run anyway" start it              | not run      | not run      |
| 3   | Click through the wizard                          | Setup asks for no administrator password and completes                     | pass         | pass         |
| 4   | Look at the Start menu and the desktop            | A **z-finance** entry, and a desktop icon if the box stays ticked          | pass         | pass         |
| 5   | Start z-finance                                   | The browser opens on `http://127.0.0.1:8765` or the next free port         | pass         | pass         |
| 6   | Look at the notification area                     | A **z** icon with Open, Back up now, Open data folder and Quit             | pass         | pass         |
| 7   | Do the first-run wizard                           | Four steps; the start page then shows the new account                      | pass         | pass         |
| 8   | Start z-finance a second time                     | No second server; the browser tab comes back                               | pass         | pass         |
| 9   | Click "Open data folder"                          | Explorer shows `%LOCALAPPDATA%\z-finance` with the database and `backups\` | pass         | pass         |
| 10  | Click "Back up now"                               | A new `…-manual` folder in `backups\`                                      | pass         | pass         |
| 11  | Record one booking, then click "Quit"             | The icon goes away; the browser tab cannot reach z-finance                 | **fail**     | pass         |
| 12  | Start again                                       | The booking is still there                                                 | **fail**     | pass         |
| 13  | Install the same version over the old one         | Setup uses the same folder; the booking is still there                     | **fail**     | pass         |
| 13b | Install over the **running** program (run 2 only) | Setup asks you to close z-finance, then continues                          | —            | pass         |
| 14  | Settings → "Look for new versions" → "Check now"  | A version name, or "could not reach GitHub"; no download                   | pass         | pass         |
| 15  | Uninstall the program                             | The program goes away; `%LOCALAPPDATA%\z-finance` stays                    | pass (note)  | pass (note)  |

## Deviations

- **Steps 1 and 2** did not run. The repository was private at that time, and the sandbox had no
  GitHub login. Windows Sandbox also has no SmartScreen app. The setup file in the sandbox was the
  release file, byte for byte. A test on a real machine with a public release must still do these
  two steps.
- **Steps 11 to 13 in run 1.** In `1.0.0-rc.2`, "Quit" stopped the server, but the program
  process did not stop. The next start then opened a dead address, and an update over it
  stopped with an error. Run 2 shows the fix: "Quit" stops the process. If z-finance runs during an
  update, Setup asks you to close it.
- **Step 3 in run 1.** Setup of `1.0.0-rc.2` asked "Select Setup Install Mode" first. Run 2 does
  not show this question.
- **Step 15.** The "Uninstall" entry in Windows Settings did not react to the automated click.
  The uninstaller `unins000.exe` in the program folder did the same work. After it, the program
  and the Start menu entry were gone. The data folder still held the database and the backups.
- **Language.** The sandbox runs in English. Setup, tray and browser were in English.

## Pictures

All pictures come from the sandbox, on a new installation without personal data. The only user
name in them is `WDAGUtilityAccount`.

Run 2 (`1.0.0-rc.4`):

| Picture                                                                            | Step | What it shows                                  |
| ---------------------------------------------------------------------------------- | ---- | ---------------------------------------------- |
| [`03-language.png`](assets/qa/install/rc4/03-language.png)                         | 3    | Setup language, the first page                 |
| [`03-license.png`](assets/qa/install/rc4/03-license.png)                           | 3    | License agreement                              |
| [`03-folder.png`](assets/qa/install/rc4/03-folder.png)                             | 3    | Destination folder in `AppData\Local\Programs` |
| [`03-tasks.png`](assets/qa/install/rc4/03-tasks.png)                               | 3    | "Create a desktop shortcut", ticked            |
| [`03-ready.png`](assets/qa/install/rc4/03-ready.png)                               | 3    | Ready to install                               |
| [`03-finish.png`](assets/qa/install/rc4/03-finish.png)                             | 3, 5 | Last page with "Launch z-finance"              |
| [`06-tray-menu.png`](assets/qa/install/rc4/06-tray-menu.png)                       | 6    | The menu of the **z** icon                     |
| [`11-after-quit.png`](assets/qa/install/rc4/11-after-quit.png)                     | 11   | After "Quit" the page cannot reach z-finance   |
| [`13b-setup-asks-to-close.png`](assets/qa/install/rc4/13b-setup-asks-to-close.png) | 13b  | Setup asks you to close the running z-finance  |
| [`15-uninstall-question.png`](assets/qa/install/rc4/15-uninstall-question.png)     | 15   | The uninstaller asks                           |
| [`15-uninstall-done.png`](assets/qa/install/rc4/15-uninstall-done.png)             | 15   | The uninstaller is done                        |

Run 1 (`1.0.0-rc.2`):

| Picture                                                                        | Step | What it shows                                      |
| ------------------------------------------------------------------------------ | ---- | -------------------------------------------------- |
| [`02-store-question.png`](assets/qa/install/02-store-question.png)             | 2    | What Windows Sandbox shows instead of SmartScreen  |
| [`03-install-mode.png`](assets/qa/install/03-install-mode.png)                 | 3    | "Select Setup Install Mode" (only in `1.0.0-rc.2`) |
| [`13-update-while-running.png`](assets/qa/install/13-update-while-running.png) | 13   | Setup cannot close the program that still runs     |
