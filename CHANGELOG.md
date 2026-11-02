# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

Entries under `## Unreleased` are append-only: each change adds its lines at the end of the
matching subsection (`Added`, `Changed`, `Fixed`, `Removed`) and never rewrites earlier lines.

## Unreleased

### Changed

- The install chapter of the manual (English and German) shows the pictures of the installer from the Windows Sandbox run: the pages of Setup, the tray menu, Setup asking to close z-finance before an update, and the uninstaller; the picture test no longer exempts the chapter (T-2026-HAR-9130-025)
- README rewritten as a front page for new users: value line and the problem it solves, a short German paragraph, install in three steps with installer pictures, features as outcomes, a screenshot gallery, the privacy promise as the code keeps it, and the 1.0.0 status (T-2026-HAR-9130-026).
- The public export names no employer, customer or third-party product of the maintainer any more: ADR-0001 describes the protection of a public repository (status check `ci`, review by the maintainer); the project's own `.po`/`.pot` check `tools/check_po.py` (standard library, it only reads) replaces the third-party hook; the privacy test has patterns for these names; the new page `docs/install-test.md` shows the clean-machine install results; a test checks that each relative link of an exported Markdown file stays inside the export (T-2026-HAR-9130-028)
- The export guard `tests/test_docs_final.py` keeps only generic privacy patterns. The patterns that identify the maintainer, the employer, a customer, the ERP product and the maintainer's machines are now in `docs/qa/private_patterns.toml`. The export excludes this file and a test keeps it out. In the private repository the test fails when this file is not there (T-2026-HAR-9130-030)

## 1.0.0 - 2026-11-02

The first complete release. Quick entry, recurring and expected items, the weekly review of due
items, CSV import with bank profiles and duplicate protection, the liquidity forecast, levies and
revenue (the free amount is always net of what the tax office will claim), debts with a payoff plan,
savings pots with an emergency order, allocation of income, the optional receipt agent, backup and
restore, English and German, a Windows installer with a first-start wizard, and a manual with a
weekly routine, questions and answers and a glossary. Everything runs on your own computer. The
entries below are per task; the date is set when the version is tagged.

### Added

- Repository skeleton: FastAPI backend (`backend/zfinance`) with per-user data directory
  (platformdirs, `ZFINANCE_DATA_DIR` override), SQLite via `sqlite3` with a forward-only SQL
  migration runner, integer-cent money helpers, `GET /api/health`, and serving of the built SPA;
  React + Vite + TypeScript shell with router, feature registry, API client and layout;
  `uv run z-finance` on 127.0.0.1:8765 opening the browser; justfile; README, ARCHITECTURE,
  CONTRIBUTING, Apache-2.0 license (T-2026-CU-9130-002).
- Local-only guard: requests with a non-loopback `Host` (DNS rebinding) or a cross-site `Origin`
  on state-changing methods are refused with 403 (T-2026-CU-9130-002).
- Commit and CI gates: pre-commit (ruff, mypy, pyupgrade, bandit, prettier, eslint, gitleaks,
  uv-lock, base checks), conventional-commit subject gate, docs gate, CI job `ci` (hooks, full
  history secret scan, PR title, tests, frontend build), branch-protection ADR-0001
  (T-2026-CU-9130-003).
- Backup and restore: schema-driven JSON export with per-table checksums plus a SQLite copy in
  `backups/`, automatic snapshot at every start (newest 10 kept), `/api/backup` and a Backup page;
  a restore loads an export into an empty database, migrates older exports and refuses newer ones
  (T-2026-CU-9130-006).
- Internationalisation: English UI with German switchable on the new settings page (stored in the
  browser and the backend, `GET/PUT /api/i18n/language`); Lingui PO catalogs for the frontend,
  gettext catalogs for localized API error messages (setting, then `Accept-Language`);
  `just i18n` and `tools/check_pot_freshness.py` keep the catalogs complete (T-2026-CU-9130-004).
- Help and manual: a help button on every screen opens that screen's chapter; a Manual page with
  chapter navigation and accent-insensitive search; English and German chapters from
  `docs/manual/`, embedded in the bundle and following the UI language; the same manual as a
  GitHub Pages site; `tools/check_manual_coverage.py` requires both languages for every route, and
  a screen change needs its chapter change (T-2026-CU-9130-005).
- Accounts and categories: Accounts page with accounts (area private/business, type, opening
  balance and date, reserve floor, archive instead of delete) and their balance, a one-level
  category tree with delete-with-rebooking, and a first-run offer of an editable default category
  set in the UI language; total reserve on the settings page; `/api/accounts`, `/api/categories`
  (T-2026-CU-9130-007).
- Quick entry: record income, expenses and transfers (two linked bookings, also owner
  draw/contribution) from any page with Alt+N, keyboard only; date and last account prefilled,
  Enter saves and keeps the form open; category and counterparty suggested from earlier bookings;
  future dates saved as planned items; editable list with delete confirmation; `transaction` table
  and `/api/transactions`; account balances, delete protection and category moves now use real
  bookings (T-2026-CU-9130-008).
- Recurring items: rent, subscriptions, income and instalments (also as transfers) entered once
  with a weekly/monthly/quarterly/yearly rhythm become planned items for the next 12 months, filled
  up at every start without duplicates (day 31 falls on the month's last day); single items can be
  moved and stay moved, pausing removes unconfirmed items; expected one-off items;
  `/api/recurring` and `replace_planned` for the CSV import (T-2026-CU-9130-009).
- Month: income, expenses and balance of a month for private, business or both, by category with
  the previous month and the six-month average; owner draws and other transfers are neither income
  nor expense; a click on a category lists its bookings; uncategorised bookings can be categorised
  in place, and "make a rule from this booking" (text contains / regular expression, priority)
  shows its hit count first and applies at once without touching categorised bookings; regular
  expressions are checked on save and time-limited, a broken rule is marked and skipped; CSV export
  of the month; `rule` table, `/api/rules`, `/api/reports/month`, new dependency `regex`
  (T-2026-CU-9130-010).
- Debts: each debt is a liability account plus a recurring instalment transfer from the paying
  account; the Debts page shows remaining amount, instalment and projected end (last instalment =
  the rest) with sums per area and in total, books capped special payments, closes a paid-off debt
  and removes its planned instalments, handles debts without a fixed instalment (required monthly
  rate to a wished end) and raised totals; monthly payoff budget; `/api/debts`
  (T-2026-CU-9130-013).
- Receipts: `POST /api/receipts` takes a receipt photo (JPEG/PNG/WebP, checked by its bytes, up to
  10 MB) with the fields the receipt agent read and books it as an expense with the photo attached
  (stored under `receipts/<year>/` in the data directory), attaches a card receipt to an existing
  booking of the same amount within ±3 days, refuses the same photo twice and puts unsure receipts
  (low confidence, missing fields, foreign currency, no account) on the "Check receipts" list,
  where they are corrected and booked or discarded; message hints choose business/private, cash/card
  and the category; agent runs and settings (threshold, account per payment method); Receipts page;
  `receipt`/`agent_run` tables, new dependency `python-multipart`
  (T-2026-CU-9130-016).
- Due items: weekly review of planned items up to 14 days ahead, overdue first; confirm with Enter
  (amount and booking date editable in the row, the recurring item stays unchanged, transfers book
  both legs), skip (it counts nowhere, the recurring item goes on) or postpone; the last confirmed
  instalment closes a debt; items more than 30 days overdue are marked in words and with an edge;
  `/api/due` and `transactions.skip_planned` (T-2026-CU-9130-011).
- Taxes & levies: enter the income tax prepayment and the SVS contribution once (business account,
  amount per due date, editable due dates, defaults 15.02./15.05./15.08./15.11. and end of
  Feb/May/Aug/Nov) and they appear as four planned items per kind; a new assessment notice changes
  the amount from the next open due date on while confirmed quarters keep theirs; hint while
  nothing is entered; no VAT (small business); `/api/settings/tax`, `prepayments_ytd` for the levy
  reserve (T-2026-CU-9130-014).
- Savings pots engine and API: pots as virtual parts of one account with target amount and date,
  monthly rate, plan status (on track / behind / reached), free balance per account (deposits above
  it are refused), moving between pots, expenses paid from a pot, savings plan as a recurring
  transfer, target amount as planned expense, emergency order with locked pots and an emergency
  proposal that empties rank 1 before rank 2; `/api/pots` (T-2026-CU-9130-015).
- CSV import engine (API, the screen follows): bank profiles (column mapping, date format, decimal
  separator, skipped lines, `;`/`,`/tab and UTF-8/Windows-1252 detected or set), a preview that
  marks every line as new, already imported, matching a planned item (±5 %, ±5 days, changeable)
  or possibly already booked by hand, rule categories in the preview, corrections per line, and a
  commit that writes all lines or none; importing the same file again adds nothing; a run log with
  the counts; `import_profile`/`import_run` tables, `/api/imports` (T-2026-CU-9130-012).
- Receipt agent (`agent/`, optional add-on): fetches receipt photos from an IMAP mailbox (allowlisted
  senders, optional subject secret), reads them with `claude -p` into a fixed JSON schema (one
  retry, then a review item), hands them to `/api/receipts`, marks a message read only after every
  photo was accepted, answers with a summary; budget per run, exit codes, JSON-lines log,
  `.env.example`, Windows Task Scheduler guide in `agent/README.md` (T-2026-CU-9130-020).
- Receipt photos in the backup: every snapshot holds the photos its receipts name (hard links to the
  live files, so the start backup copies no bytes), `GET /api/backup/{name}/archive` downloads data
  and photos as one ZIP, and the restore accepts that ZIP next to `export.json`; archive entries are
  matched against a strict name pattern, checked against the receipts' hashes and size-limited
  (Zip-Slip and zip-bomb guards); a backup without photos still restores (T-2026-CU-9130-033).
- Levy reserve engine and API (the screen follows): levy rate on business income or profit,
  reserve target, what is set aside (levy pot + prepayments) and the gap per year, the expected
  back-payment as a planned item on 1 July of the following year, the monthly revenue needed with
  its split, chart data for income against the need and expenses against liabilities, and
  `free_amount` — the one "free" number, net of the levy gap from the moment an income is booked;
  `/api/levy` (T-2026-CU-9130-019).
- Liquidity forecast engine and API (the screen follows): day-by-day balance for 3, 6 or 12 months
  per account, area or in total, a second line "available after levies", minimum with date, first
  day below the account or total reserve, planned income and expenses, overdue items, what-if
  items that are never stored, the items of one day and the pots that would cover a shortfall in
  emergency order; `/api/forecast` (T-2026-CU-9130-022).
- Payoff plan page: the amount available per month net of fixed costs, instalments, pot savings
  plans and levies (3-month average income, overridable), budget on top of the instalments,
  smallest first / most expensive first / own order (drag or buttons), the debt-free month and each
  debt's end, one planned special payment per month that moves on to the next debt once one is paid
  off, planned against paid with the catch-up amount, and a what-if slider; `/api/payoff`
  (T-2026-CU-9130-021).
- Levy & revenue screen (Planning → "Levy & revenue"): target, set aside and gap of the year with a
  "Levies not covered" warning, the expected back-payment per open year (archive it when the
  assessment arrives), the free amount net of the levy gap, the required revenue per month split
  by block, and charts of income against required revenue (months below it marked), expenses by
  category against liabilities, and remaining debt; the levy rate, base, due date, pot and account
  are set on the same page and everything recalculates on save. The charts are a reusable,
  dependency-free component in `frontend/src/components/charts/` with keyboard readout and a table
  view; manual chapter in English and German (T-2026-CU-9130-023).
- Savings pots screen: per account the balance, the money in pots, the free balance and what an
  emergency could take; a card per pot with progress bar and percentage, status (on track, behind
  plan with the new monthly rate, reached), expected date and emergency rank; create, edit, close
  and delete pots (target and date optional, locked, plan the expense, levy-pot marker); assign
  money from the free balance or another account (a refused amount shows the backend message at
  the field), take out (a locked pot asks first), move between pots, assign an expense (shows the
  split), savings plan, movement history; the emergency order changes by dragging a pot or with the
  arrow buttons; the emergency withdrawal proposes in rank order with new balances and moved target
  dates and can be edited by hand; manual chapter in English and German (T-2026-CU-9130-018).
- Import assistant: the Import page walks through a bank CSV in four steps — account and file
  with the recognised encoding and separator (choosable by hand, with a warning for garbled
  umlauts), a bank profile with the columns picked from the file's header, a preview of every line
  (new, duplicate in grey, match with the planned item beside it, possible duplicate) where the
  category can be corrected, a match removed or moved and a corrected category saved as a rule, and
  the protocol of new / matched / duplicates / uncategorised; "Apply" is locked while the import
  runs, and a manual chapter in English and German explains it (T-2026-CU-9130-017).
- Allocation: rules that split a booked income into fixed shares — the levy share from the levy rate
  into the levy pot, a share for the first open debt of the payoff order, shares into pots or the
  total reserve — and propose them for one-click confirmation; the levy share is already set aside
  from the booking on, so an open proposal never lowers "free until the end of the month" a second
  time and cannot be confirmed below the rate. Confirming books one transfer per share in a single
  transaction, rejecting is logged and counted, a proposal open for seven days is highlighted and
  never booked by itself, a proposal that would eat into the reserve says so with a reduced amount,
  and a deleted income lists the transfers it had booked for you to undo; `/api/allocation`, new
  Allocation page (T-2026-CU-9130-025).
- Forecast screen (Analyse → "Forecast"): the daily liquidity curve for 3, 6 or 12 months over all
  accounts, an area or one account, with the balance, the line "available after levies", the
  reserve as a dashed line and the lowest point marked; the reserve check refers to the available
  line. A click (or arrow keys and Enter) on a day lists its items; what-if items redraw the curve
  and are stored nowhere unless adopted as an expected item; a breach offers the pots that would
  cover the shortfall with a one-click withdrawal. The shared chart gained day selection, dashed
  lines and point marks; manual chapter in English and German (T-2026-CU-9130-024).
- Dashboard as the start page: the three figures of the weekly routine without scrolling — "free
  until the end of the month" with the levy share named beside it, the remaining debt with its
  trend against the previous month, and the number of overdue items — and below them the items due
  in the next 14 days, the account balances, the running month by category, the next 90 days as a
  curve, the income waiting to be allocated (highlighted after seven days) and the receipts to
  check; hints when the levies are not covered or no tax plan is stored. One read
  (`GET /api/dashboard`) over the existing services, every card links into its screen; the start
  page's manual chapter in English and German now describes the dashboard and the weekly routine
  (T-2026-CU-9130-026).
- Windows installer for people without a development environment: a PyInstaller one-folder bundle
  and an Inno Setup script (per-user install, Start menu entry, optional desktop icon) that never
  writes or deletes below the data directory, so an update and an uninstall both keep accounts,
  bookings and backups. The installed program takes the first free port from 8765 upwards, keeps
  one instance per data directory, opens the browser and offers Open, Back up now, Open data folder
  and Quit in the notification area. The first start leads through language, first account,
  categories and an optional levy rate; the settings page gained an opt-in update notice that asks
  the GitHub releases API which version is published and downloads nothing. A `v*` tag builds the
  installer and attaches it to the release; README and the manual explain the three steps and the
  SmartScreen warning (T-2026-CU-9130-027).
- Documentation, final: the manual has an entry chapter "The weekly routine", "Questions and answers"
  and a glossary, every screen chapter shows its screen with a screenshot (English and German, all with
  example data, marked in the picture), and the screenshots are regenerated from the running tool with
  `tools/screenshots.py` and the example story of `tools/demo_data.py`; the README starts with the
  installation in three steps, ARCHITECTURE describes the domain model as built, the migration order,
  the core rule, the data flows of import, receipt agent and allocation, and the engine dependencies
  as diagrams; `agent/README.md` was checked against the code; public modules, classes, methods and
  functions all have docstrings, ruff's `D1` rules keep it so (T-2026-CU-9130-028).

- Browser acceptance run for release 1.0: `tests/e2e/` drives a real Chromium against the running
  program and checks the 76 acceptance criteria of `docs/UC-z-finance.md` with the figures they
  name — one module per use case, the criterion quoted in every test. The run is deselected in the
  normal test run (marker `e2e`) and started with
  `uv run --group docs pytest tests/e2e -m e2e`. Its result is the protocol
  `docs/qa/acceptance-1.0.md`: 67 criteria pass, 4 in part, 4 are not observable in a browser, 1
  fails (a booking does not say that it came from a recurring item). The protocol also records that
  a screen whose parallel requests write at the same time can answer 500 `database is locked`
  (T-2026-CU-9130-029).
- Public export: `packaging/public_export.toml` lists which files go into a public repository.
  `tools/public_export.py` copies them into a folder and does a dry run by default. It never pushes.
  A test examines each exported text file for personal data of the owner (T-2026-HAR-9130-002).

### Changed

- Backup page and backup error messages are translatable and translated into German: Lingui macros
  on the page and nav label, `N_()` msgids with named parameters for the backup errors, the restore
  upload now sends the UI language as `Accept-Language` (T-2026-CU-9130-030).
- Commit gate for PO catalogs: a German translation identical to the English text ("Backup",
  "Name", "Status") is kept. The `oca-checks-po` hook runs through `tools/oca_checks_po.py`, which
  keeps its checks and its sort/wrap layout but no longer blanks `msgstr == msgid`; the freshness
  check already counts such an entry as translated and now has a test for it (T-2026-CU-9130-031).
- New look for every screen, after dev-front's EDEL style and made for desktop screens: sidebar
  navigation in the groups Capture / Planning / Analyse / Settings, a header with the current page,
  language switch and help, glass cards, Montserrat bundled with the app (works offline), WCAG AA
  text contrast and a visible focus ring; shared components in `frontend/src/ui/` for new screens;
  rows of the recurring and debt lists can be selected with Enter or Space (T-2026-CU-9130-032).
- Manual: chapters no longer announce features as "a later part of z-finance"; the passages point at
  the forecast, due items, import, payoff plan and allocation, which exist (T-2026-CU-9130-028).
- `tools/screenshots.py` and the acceptance run share their harness: the free port, the app as its
  own process on a throwaway data directory and the deterministic Chromium context now live in
  `tools/live_app.py`. Each instance writes its server output to `server.log` in its data directory
  instead of discarding it (T-2026-CU-9130-029).
- Version of the package is `1.0.0`. It is what `GET /api/health`, the footer of the interface and
  the tray title report, and what the update check compares against the newest release tag — left at
  `0.1.0`, a `v1.0.0` release would have told every installation for ever that a newer version is
  available (T-2026-CU-9130-029).
- ARCHITECTURE "Known risks" names the collision between endpoints that write while answering a
  read (`database is locked`), with the measurement it comes from (T-2026-CU-9130-029).
- Manual/help text nachgeschaerft: deutsche Anfuehrungszeichen schliessen jetzt mit dem korrekten
  „…“-Zeichen statt mit dem geraden ASCII-Zeichen (14 der 15 betroffenen DE-Kapitel), 18 Kapitel bekommen
  einen Schlusssatz statt mitten im Troubleshooting-Abschnitt zu enden, `install.md` und
  `manual.md` bekommen einen fehlenden Einstiegssatz, und sechs Kapitel-Einstiege ("Konten ist der
  Ort, wo …") sind auf die Frage umgestellt, die der Bildschirm beantwortet. `entry.md` ist davon
  ausgenommen (paralleler Task T-2026-CU-9130-043, gleiche Welle) (T-2026-CU-9130-040).
- Manual/help text: `entry.md` nachgezogen, ausgenommen von -040 solange -043 die Datei parallel
  aenderte. Deutsche Anfuehrungszeichen schliessen jetzt mit `“` statt mit dem geraden
  ASCII-Zeichen, ein Schlusssatz folgt jetzt auf den Troubleshooting-Abschnitt, und der
  Einstiegssatz ist auf die Frage umgestellt, die der Bildschirm beantwortet
  (T-2026-CU-9130-045).
- Release workflow: the tag must be a plain version that matches `pyproject.toml` and sits on
  `main`, tags with a suffix (`-rc.N`) are published as pre-releases, and the version reaches the
  scripts through `env:` instead of a `${{ }}` expression (T-2026-HAR-9130-015).
- Acceptance run 2 on main (`ec16be6`): all 75 browser tests green in one process, a security
  review of the changes since `v1.0.0-rc.1` without an exploitable finding, and a release
  checklist in `docs/qa/acceptance-1.0.md` (T-2026-HAR-9130-001)
- Clean-machine install protocol run in Windows Sandbox with `v1.0.0-rc.2`: `tools/sandbox/`
  clicks the 15 steps by button name and takes a picture of each screen; the result and the
  pictures are in `docs/qa/install-protocol.md` (T-2026-HAR-9130-019)
- Install protocol run 2 in Windows Sandbox with `v1.0.0-rc.4`: steps 3–15 pass; the new row 13b
  (`run_protocol.py --update-while-running`) shows that Setup asks to close a running z-finance and
  finishes after a tray "Quit" (T-2026-HAR-9130-024)

### Fixed

- A screen no longer fails with "API request failed with status 500" (`database is locked`) when
  several of its requests write at the same time: a database transaction takes the write lock at its
  start (`BEGIN IMMEDIATE`), so the second writer waits up to 10 seconds for the first one instead of
  failing at once. The receipt agent's hand-over (`POST /api/receipts/submit`) runs its write in the
  thread pool, so such a wait never holds up the other requests (T-2026-CU-9130-038).
- Receipt agent: `ZF_AGENT_SUBJECT_SECRET` is now required. With an empty secret the agent booked a
  mail whose forged `From` header named an allowed sender; it now stops with exit code 2 and names
  the setting. An installation without a secret word must set one (set-up step 2 of
  `agent/README.md`). The README's security section no longer claims a forged mail is ignored
  without it (T-2026-CU-9130-035).
- Quick entry booking list: a row whose booking came from a recurring item (confirmed under Due
  items) now carries a "recurring" badge next to its amount, so it is no longer indistinguishable
  from one typed by hand (UC-ZF-004 AK1, second half; finding F-1 of `docs/qa/acceptance-1.0.md`,
  T-2026-CU-9130-039).
- `z-finance --host` with an address other than loopback (for example `0.0.0.0` or a LAN IP) is
  refused with exit code 2 unless `--allow-network` is passed too. z-finance has no login: bound to
  the network, any program on it could read and change the data by sending `Host: 127.0.0.1`. With
  `--allow-network` the start logs that risk as a warning. The default start (127.0.0.1, installer,
  Start menu) is unchanged (T-2026-CU-9130-036).
- The installed program (Start-menu shortcut, `packaging/zfinance_launcher.py`) applies the same
  `--host` refusal: a non-loopback `--host` needs `--allow-network` too, and the opted-in start logs
  the risk to `<data dir>/runtime/z-finance.log`. The default Start-menu launch (no `--host`) is
  unchanged (T-2026-CU-9130-044).
- Quick entry: a booking typed and saved (keyboard-only, `Alt+N` then `Enter`) the instant the
  screen opens, before its account list had finished loading, was silently lost — it was rejected
  with "Choose an account." and never sent to the server, with only a small, easy-to-miss message as
  the sign. Saving now waits for the account list if it has not arrived yet and then uses the
  account the form would default to, instead of failing (T-2026-CU-9130-043).
- Recurring items: on the day an item is due, it was planned twice in the 12-month outlook — this
  year's date and the same date next year — so the tax plan saved on 15.02., 15.05., 15.08. or
  15.11. showed five income-tax prepayments instead of four. The outlook now runs from today up to,
  but not including, the same day in 12 months, so every due date is planned once; this applies to
  all recurring items, instalments and savings plans (T-2026-CU-9130-047).
- Quick entry: editing a booking that sits on an archived account silently moved it to the first active account when saved, so both balances were wrong. The booking now stays on its account while you edit it, and the account is shown in the list marked "archived" (T-2026-HAR-9130-011).
- Month screen: stepping to another month or area no longer leaves the old month's figures under
  the new heading. The figures are cleared and "Loading…" shows until the answer arrives, a failed
  request shows only the error, and a late answer of a month already left is dropped
  (T-2026-HAR-9130-012).
- The installed Windows build starts again: pre-release `v1.0.0-rc.2` is built from the launcher fix, installed over the earlier one, and answers `/api/health` and writes its log without a console (T-2026-CU-9130-041)
- Test suite: the recurring API-flow test and the dashboard allocation-proposal test no longer depend on the calendar day — the first expected the wrong count on the 1st of a month, the second went red once the real date passed 2026-10-12; both now pin their clock (T-2026-HAR-9130-004).
- The web client shows the field and message of a validation error (HTTP 422) instead of "API request failed with status 422", treats a success answer that is not JSON as an error instead of handing `undefined` to the screen, and the backup restore shares that error handling; the sidebar health check retries after 1, 2 and 4 s and then polls every 30 s, so a backend that starts after the page no longer stays "not reachable" until a reload (T-2026-HAR-9130-013).
- The forecast now ends one day before the same day 3, 6 or 12 months ahead, like the recurring outlook, so the last day of the curve no longer lacks the items due on today's calendar day; the architecture notes state the one window rule (T-2026-HAR-9130-007).
- First-run wizard: a failed **Finish** (for example the connection drops after the account was created) can be repeated. The wizard remembers the account it created, and an account of the same name that already exists is reused, so the second click no longer creates a duplicate account with a doubled opening balance or stops on a name conflict (T-2026-HAR-9130-018).
- Tax plan and recurring items: deleting an income-tax or SVS item of the tax plan on the recurring
  items page unlinked its booked payments, so they no longer counted as prepayments and the levy
  gap grew by that amount. Such a delete is now refused (409) with a pointer to Taxes & levies.
  Deleting a planned occurrence of a recurring item in the booking list now skips it, and deleting
  the skipped row again leaves it skipped, so the next generation run never plans it again
  (T-2026-HAR-9130-006).
- Accounts: deleting an account that a savings pot, recurring item (as source or transfer target), debt, import run, the tax plan, the levy reserve or an allocation proposal still uses now answers 409 with a localized message instead of a 500 or a silent cascade; a late foreign-key error is also a 409 (T-2026-HAR-9130-008).
- A double click or retry no longer books twice: confirming or skipping an allocation proposal, withdrawing from, moving between or assigning an expense to a pot, and committing an import now check their precondition again under the write lock. A second request that raced the first gets 409 Conflict instead of a second booking, a negative pot balance or HTTP 500 (T-2026-HAR-9130-005).
- Due items and debts: an amount typed in another format than the plan shows ("800.00" or "800" for "800,00") no longer counts as a change, so the confirm sends no amount. The special payment form on a debt stays disabled and sends nothing while no account to pay from is loaded; it asks to choose one (T-2026-HAR-9130-014).
- An amount beyond 100 billion euros (`MAX_CENTS`, 10**13 cents) is now refused with 422 at every input (bookings, accounts, recurring items, receipts, pots, debts, due items, tax plan, allocation) and as a CSV line error ("The amount is too large") instead of answering 500; before, two huge bookings made every sum over the account fail until one was deleted by hand (T-2026-HAR-9130-009).
- Receipt agent: without `ZF_AGENT_API_URL` it now follows the address the launcher wrote to `runtime/launcher.json`, so a z-finance on port 8766 no longer ends every run with exit code 4. The README states that `claude -p` can read files outside its temporary folder (the folder is where it starts, not a lock), and `data-*/` folders and `*.log` files are git-ignored (T-2026-HAR-9130-016).
- Start options: `--host ::1` opened an invalid address (`http://::1:8765`) and `--host 0.0.0.0` opened `http://0.0.0.0:8765`, which Chrome on Windows refuses. The browser now opens `http://[::1]:<port>` or `http://127.0.0.1:<port>`. A start with `--host 0.0.0.0` or `::` now logs that a LAN address still gets 403 and which address works; the README and the install chapter say that `--allow-network` accepts only the exact bind address (T-2026-HAR-9130-010).
- Installer and manual: the setup no longer asks "only for me / for all users" (it stays per-user, no administrator prompt), and the install chapter and README now say that SmartScreen shows "Publisher: Unknown" for the unsigned file, instead of asking the user to look for z-finance (T-2026-HAR-9130-017).
- Tray and installer: **Quit** in the tray now ends z-finance. Before, the icon stayed and the process kept its lock and `runtime/launcher.json`, so the next start opened a dead address. The running program now holds a named mutex that the installer checks (`AppMutex`), so an update or uninstall over a running z-finance asks you to quit it instead of offering "Ignore the error" on files in use (T-2026-HAR-9130-022).
- API: the what-if amount of the liquidity forecast (`amount_cents`) and the payoff plan's monthly budget and income override (`budget_cents`, `income_override_cents`) are bounded at 100 billion euros like every other amount. A larger value answers 422 instead of 200 or, for the income override, a 500 when the plan was saved (T-2026-HAR-9130-021).
- Launcher: a Start-menu shortcut with `--host 0.0.0.0 --allow-network` (or `--host ::1`, `--host ::`) opened and recorded an invalid address (`http://0.0.0.0:8765`, `http://::1:8765`). The launcher now uses the same address as the `z-finance` command: a wildcard bind opens the loopback address and an IPv6 literal gets brackets, in the browser and in `runtime/launcher.json` that the receipt agent reads. The host guard is unchanged. (T-2026-HAR-9130-020)
- Release workflow: the step "Version from the tag" no longer runs `git fetch`, which asked a private repo for a password and stopped every tag build since the main check came in. It now checks the tagged commit against the `origin/main` that the full checkout already holds, and `GIT_TERMINAL_PROMPT=0` makes a missing credential fail at once (T-2026-HAR-9130-023).
