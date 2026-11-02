# Contributing

## Project knowledge & docs

- **`README.md`** — what z-finance is, installation, developer quick start.
- **`ARCHITECTURE.md`** — structure, conventions, and where a new feature's migration, router,
  frontend route and tests go. Read it before adding a feature.
- **`docs/UC-z-finance.md`** — the use cases and non-functional requirements (German); the source
  of truth for what the tool must do.
- **`SPEC-z-finance.md`** — the developer-facing acceptance criteria derived from the use cases.
- **`docs/specs/`** — per-task specs written while building.
- **`docs/manual/`** — the user manual in English and German; **`docs/assets/screenshots/`** holds its
  pictures (generated, see "Writing a manual chapter"). **`docs/qa/`** — QA protocols.
- **`CHANGELOG.md`** — user-visible changes.

Update the relevant file as part of finishing a change.

### How docs are written

Prose docs state the current state: absolute assertions, update over add, remove what no longer
applies, absolute dates. History lives in git and in the changelog. Code, comments, commit
messages and the developer docs are in English; the requirement documents under `docs/` stay in
German.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python toolchain; Python 3.13+ is installed by uv if missing
- Node.js 20+ with npm — frontend build
- [just](https://just.systems) — command runner
- Windows: Git for Windows; the recipes run under its `sh`, so run `just` from Git Bash or put
  `C:\Program Files\Git\usr\bin` on `PATH` for PowerShell

## Setup and daily commands

```sh
just setup   # install Python and frontend dependencies from the lock files
just dev     # backend on :8765 with reload + Vite on :5173; data in ./data
just test    # pytest
just lint    # ruff check + ruff format --check + TypeScript type check
just fmt     # apply ruff fixes and formatting
just build   # build the SPA into backend/zfinance/web/
just i18n    # re-extract the translation catalogs after changing UI strings
```

Install the commit gates once per clone with `uvx pre-commit install`. They run on every commit
(formatting, lint, types, secret scan, docs gate) and check the commit subject; CI runs the same
hooks with `uvx pre-commit run --all-files`, plus the tests and the frontend build. The
German requirement documents (`docs/UC-z-finance.md`, `SPEC-z-finance.md`, `docs/handoff/`) are
excluded from the formatters.

The docs gate: a change under `backend/zfinance/domain/<feature>/` must come with a change to
`README.md`, `ARCHITECTURE.md`, `CHANGELOG.md` or the manual chapter
`docs/manual/<lang>/<feature>.md`. A change under `frontend/src/features/<feature>/` (a screen)
must come with a change to that screen's manual chapter, `docs/manual/<lang>/<chapter>.md` — the
project docs do not count here. Test files are exempt (`*.test.ts`, `*.test.tsx`, `__tests__/`):
they change no screen. The `manual-coverage` hook checks that every route has its chapter
in every language. Branch protection and the merge gate are described in
`docs/decisions/ADR-0001-branch-protection.md`.

## Writing a manual chapter

Every screen explains itself in the manual (help button and Manual page, ARCHITECTURE.md "Help and
manual"). For a new screen with `path: "accounts"`:

1. Write `docs/manual/en/accounts.md` and `docs/manual/de/accounts.md`: optional front matter
   `order: <the feature's nav.order>`, then `# Title`, then plain Markdown for a lay user — what the
   screen shows, what you can do, typical problems. Link other chapters as `[Backup](backup.md)`.
2. Add the screen to `tools/screenshots.py` (one `Shot`) and, if it needs data the example story
   lacks, to `tools/demo_data.py`. Run the generator with `--only accounts` (README, "Screenshots")
   and put `![…](../../assets/screenshots/<lang>/accounts.png)` after the chapter's first paragraph
   in both languages.
3. Check: `uv run python tools/check_manual_coverage.py` exits 0; `just dev` and the **?** button
   show the chapter in both languages.

When you change the screen later, update its chapter and its screenshot in the same commit. The
start page's chapter is `index.md`. Never put real financial data in examples or in a screenshot:
the generator uses only the example story, and every picture carries an "EXAMPLE DATA" badge.

## Adding or changing a translatable string

Every user-facing string is translatable; English is written in the code, German lives in PO
catalogs (details and examples: ARCHITECTURE.md, "Internationalisation").

1. Mark the string. Frontend: `<Trans>…</Trans>`, ``t`…` `` from `useLingui()`, or ``msg`…` ``
   for module-level constants. Backend: `N_("…")` for an API error detail, with named
   placeholders (`%(name)s`) and `LocalizedHTTPException` when it has parameters.
2. Run `just i18n`. It updates `messages.pot` and adds empty entries to the `de.po` files
   (`frontend/src/locales/de.po`, `backend/zfinance/i18n/de/LC_MESSAGES/messages.po`).
3. Translate every new empty `msgstr` in `de.po`. Remove the `#, fuzzy` flag once a suggested
   translation is checked — fuzzy entries count as missing. A German word that equals the English
   one ("Backup", "Name", "Status") is a translation: write it as `msgstr "Backup"`. The gates keep
   it (`tools/check_po.py` accepts `msgstr == msgid`, and the freshness check counts it as
   translated) — do not pick a different word only to get past them.
4. Check: `uv run python tools/check_pot_freshness.py` exits 0. The pre-commit hooks run it plus
   `check-po` (`tools/check_po.py`). That hook checks syntax, duplicate msgids and placeholders. It does not change the catalogs.

Never add an `en.po`: English is the source text (a pre-commit hook refuses one).

## Branches, commits, pull requests

- Branch per change from `main`: `feat/<ticket>`, `fix/<short-description>`, `docs/…`.
- Commit subjects and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/):
  `type(scope): message` with the scope optional, types `feat`, `fix`, `docs`, `refactor`, `test`,
  `chore`, `build`, `ci`, `perf`, `style`, `revert`.
  Example: `feat(accounts): opening balance and reserve floor`.
- One pull request per change against `main`; `main` is protected and merges after review and
  green checks.
- Add your line under `## Unreleased` in `CHANGELOG.md` (append only).

## Tests

- Python tests live in `tests/test_<feature>.py`; test business logic through the service
  functions, HTTP behaviour through the `client` fixture (temporary data directory).
- Frontend tests are `*.test.tsx` next to the code (Vitest + Testing Library, jsdom); run them with
  `cd frontend && npm test`.
- Expected values come from the requirements (hand-computed numbers from the use cases), not
  from running the code and copying its output.
- Never use real financial data, account numbers or receipts in tests, fixtures, screenshots or
  examples. Use obviously synthetic values.

## Code style

- Python: ruff formats and lints (`just fmt`, `just lint`); every public module, class, method and
  function has a docstring (ruff `D1`; `__init__`, dunder methods and tests are excused). Money is
  an integer number of cents, never a float.
- TypeScript: prettier and eslint through the commit gates; screens use the components of
  `frontend/src/ui/` and the tokens of `styles/tokens.css` (ARCHITECTURE.md, "UI system").
- Comments explain why, in English; the code and the developer docs are English throughout.

## Pull request checklist

- [ ] `uvx pre-commit run --all-files`, `just test` and `cd frontend && npm test` pass.
- [ ] A new or changed screen: its manual chapter in both languages and its screenshot.
- [ ] A new or changed string: `just i18n` run and the German entries translated.
- [ ] Tests for what you built; each test's docstring has an `Oracle:` line saying where the expected
      value comes from (the requirements, a hand calculation — not the code's own output).
- [ ] A line under `## Unreleased` in `CHANGELOG.md`.
- [ ] No real financial data, account number, receipt or credential anywhere in the diff.

## Security and privacy

z-finance handles personal financial data. Never commit `.env` files, credentials, database
files or anything from a real data directory; `data/` and `*.sqlite3` are git-ignored. Report
security problems privately through GitHub's "Report a vulnerability" (Security tab) instead of
opening a public issue.

## License

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
