# z-finance receipt agent

Photograph a shop receipt, e-mail the photo to a mailbox of your own, and z-finance books it for
you. The receipt agent is a small helper program that runs three times a day on the computer where
z-finance runs. It:

1. looks into the mailbox for new messages **from your own address only**,
2. lets Claude (`claude -p`) read date, total, shop and payment method from each photo,
3. hands photo and values to z-finance — z-finance books the expense with the photo attached, or
   puts it on the **Receipts** page to check when something is unsure,
4. answers you by mail: how many were booked, how many wait for a check, how many were already
   there.

It is an optional add-on: z-finance works without it, and the Windows installer does not include it.

## What you need

- z-finance on this computer, started. Leave `ZF_AGENT_API_URL` empty and the agent finds it.
  The launcher takes the next free port when 8765 is busy. It writes the address to
  `runtime\launcher.json` in the z-finance data folder, and the agent reads that file.
  Without the file the agent uses `http://127.0.0.1:8765`.
  If you start z-finance in another way, set `ZF_AGENT_API_URL` to its address.
- A **separate mailbox** only for receipts (for example a new address at your mail provider). The
  agent reads and marks messages there — do not use your everyday inbox.
  The mailbox must allow login with a **password or app password** over IMAP. Mailboxes that only
  allow "Sign in with Microsoft/Google" (OAuth) do not work; the agent then stops with exit code 6.
- [Claude Code](https://claude.com/claude-code) installed and signed in, so that `claude` works in
  a terminal.
- [uv](https://docs.astral.sh/uv/) and a copy of this repository (the same as for developing
  z-finance).

## Set up

1. Copy `.env.example` from the repository root to `agent\.env` and fill in the values:
   the mail server names from your provider's help page, the receipts mailbox login, and in
   `ZF_AGENT_ALLOWED_SENDERS` the address(es) you send photos **from**. `agent\.env` is never
   committed (it is in `.gitignore`); keep it only on this computer.
2. Choose a secret word and put it into `ZF_AGENT_SUBJECT_SECRET` (see "Security" below). Write it
   into the subject of every receipt mail.
3. In z-finance, open **Receipts → Settings**: choose which account gets cash and card receipts per
   area, and the review threshold (how sure the reading must be to book without asking you).
4. Test once by hand from the repository folder:

   ```
   uv run --package z-finance-agent zfinance-agent --env-file agent\.env
   ```

   Send yourself a receipt photo first. The run prints one line per step; exit code 0 means done.

### Sending a receipt

Send one or more photos (JPEG, PNG or WebP; iPhones convert to JPEG when mailing) in one message.
Every photo is one receipt. Words in the subject or text help z-finance:

- `business` or `private` — which area the expense belongs to (default: private),
- `bar`/`cash` or `karte`/`card` — how you paid (otherwise read from the receipt),
- the name of one of your categories, e.g. `Groceries` — the category to use.

## Run it three times a day (Windows Task Scheduler)

1. Open **Task Scheduler** → **Create Task…**
2. **General:** name `z-finance receipts`; choose "Run only when user is logged on" (Claude uses
   your sign-in).
3. **Triggers:** add three triggers "Daily" at **08:00**, **13:00** and **20:00**.
4. **Actions:** "Start a program". Program: the full path of `uv.exe` (in a terminal:
   `where uv`). Arguments: `run --package z-finance-agent zfinance-agent --env-file agent\.env`.
   **Start in:** the repository folder, for example `C:\Projects\z-finance`.
5. **Settings:** "Stop the task if it runs longer than" 1 hour; "Do not start a new instance" if
   it is already running.

You can always start a run by hand with **Run** in Task Scheduler. Each run shows up in
z-finance under **Receipts → Agent runs**.

## What happens when something goes wrong

| Situation                                                                                    | What the agent does                                                                      |
| -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| z-finance is not running (or on another port than the agent calls: check `ZF_AGENT_API_URL`) | Stops, leaves every message unread; the next run picks them up. Exit code 4.             |
| Mailbox not reachable or password wrong                                                      | Stops without changes. Exit code 3.                                                      |
| Mailbox needs OAuth                                                                          | Stops without changes. Exit code 6 — choose a mailbox that allows an (app) password.     |
| `claude` not installed or not found                                                          | Stops before reading any photo. Exit code 5.                                             |
| A setting in `.env` is missing or wrong                                                      | Stops and names the setting (never its value). Exit code 2.                              |
| Claude cannot read a photo (twice)                                                           | The receipt goes to the Receipts page to check, with the photo.                          |
| z-finance refuses a photo                                                                    | The message stays unread and gets a **flag** in your mail program; later runs skip it.   |
| The answer mail cannot be sent                                                               | The run still counts as done; the result is on the Receipts page under Agent runs.       |
| More photos than `ZF_AGENT_MAX_RECEIPTS`                                                     | The rest waits for the next run. A single message with more photos than that is flagged. |

A message is marked as read only after z-finance has accepted every photo in it, so nothing is
lost and nothing is booked twice — a photo that was already stored comes back as "already
recorded".

## Costs

Every photo is one `claude -p` call (at most two when the first answer is unusable). It counts
against your Claude plan or API account like any other Claude Code use. Three settings keep it
small:

- `ZF_AGENT_MAX_RECEIPTS` — photos per run (default 10, so at most 30 a day),
- `ZF_AGENT_CLAUDE_MAX_BUDGET_USD` — spending cap per call (default 0.50),
- `ZF_AGENT_CLAUDE_MODEL` — a smaller model such as `sonnet` or `haiku` is cheaper and reads
  receipts well; empty uses your Claude Code default.

For orientation (measured in September 2026): a trivial `claude -p` test call with the default model
cost about 0.17 USD, mostly Claude Code's fixed start-up context; a smaller model costs a fraction
of that.

## Security

- **Your password** is only in `agent\.env` on this computer. The agent never writes it, the
  secret word or mail text into its log.
- **Only your address is read.** Messages from any other sender are not opened beyond their
  sender line, not marked and not answered. But the sender line (`From`) of an e-mail **can be
  forged** by anyone who knows your address, and a forged mail from your address would be booked.
  That is why the secret word in the subject (`ZF_AGENT_SUBJECT_SECRET`) is required: a mail
  without it is ignored, and the agent does not start without one (exit code 2). Keep the secret
  word and the receipts address to yourself.
- **Everything in a mail is treated as data.** The mail text only goes to z-finance as a hint
  (area, payment, category); it never reaches Claude.
- **Claude can read more than the photo.** Claude gets the photo's file name and no mail
  text. It starts in a temporary folder that holds only that photo. `Read` is its only tool.
  The folder is where Claude starts. It is **not a lock**: `Read` can open other files too.
  A photo with hidden instructions could therefore make Claude read another file on this computer.
  Two things limit the damage. The answer must match a fixed format, and each text field has at
  most 200 characters. The only reply goes to your own address.
- Photos are recognised by their content, not by file name; files over 10 MB are skipped.

## For developers

Package `zfinance_agent` (member of the repository's uv workspace):

| Module       | Job                                                                     |
| ------------ | ----------------------------------------------------------------------- |
| `config.py`  | `ZF_AGENT_*` settings from the environment over an optional `.env` file |
| `mailbox.py` | IMAP: candidates (`UNSEEN UNFLAGGED`), allowlist, photos, hints, flags  |
| `extract.py` | `claude -p` with fixed prompt and JSON schema, validation, one retry    |
| `submit.py`  | `/api/receipts` client: runs and receipt upload                         |
| `reply.py`   | the summary mail                                                        |
| `run.py`     | one run, exit codes, CLI `zfinance-agent`                               |
| `log.py`     | JSON-lines log                                                          |

Tests (`agent/tests/`, part of `uv run pytest`) use a fake IMAP server object and a fake `claude`
against the real z-finance app. `agent/tests/demo_run.py` runs the agent once against a running
z-finance with the same fakes:
`uv run --all-packages python agent/tests/demo_run.py`.
