"""Manual end-to-end check: one agent run against a running z-finance, with a fake mailbox and fake claude.

Start z-finance on a throw-away data directory first, then run this script:

    ZFINANCE_DATA_DIR=./data-demo uv run z-finance --no-browser     (or: just dev)
    uv run python agent/tests/demo_run.py [--api-url http://127.0.0.1:8765]

It creates a private giro account when the ledger has none, puts two synthetic messages into an
in-memory mailbox (one from the allowlisted sender, one from a stranger) and runs the agent over
HTTP. Afterwards the Receipts page lists the run and the Quick entry list the booking. Nothing
leaves the machine: no real mailbox, no claude call, no mail is sent.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

import httpx
from agent_fakes import OWNER, STRANGER, FakeClaude, FakeImap, FakeSmtp, jpeg, mail, make_config
from zfinance_agent.log import Log
from zfinance_agent.mailbox import Mailbox
from zfinance_agent.run import Deps, run


def main() -> int:
    """Run the demo; the exit code is the agent's."""
    parser = argparse.ArgumentParser(description="One receipt-agent run against a running z-finance.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    imap, claude, smtp = FakeImap(), FakeClaude(), FakeSmtp()
    # A new photo per run (the API would report a repeated one as a duplicate): 1.00-90.99 EUR.
    cents = str(int(time.time()) % 9_000 + 100)
    imap.add(mail(subject="demo receipt", text="demo", photos=(jpeg(cents),)))
    imap.add(mail(sender=STRANGER, subject="not allowed", photos=(jpeg("999"),)))
    with httpx.Client(base_url=args.api_url, timeout=30) as client:
        if not client.get("/api/accounts").json():
            opened = (date.today() - timedelta(days=30)).isoformat()
            client.post(
                "/api/accounts",
                json={
                    "name": "Demo giro",
                    "area": "private",
                    "type": "giro",
                    "opening_balance_cents": 0,
                    "opening_date": opened,
                },
            ).raise_for_status()
        deps = Deps(
            open_mailbox=lambda config: Mailbox.open(config, lambda host, port: imap),
            http_client=client,
            runner=claude,
            resolve_claude=lambda name: "claude (fake)",
            send=smtp,
        )
        code = run(make_config(api_url=args.api_url, allowed_senders=frozenset({OWNER})), deps, Log())
    print(f"\nexit code {int(code)}; mailbox flags {imap.flags}; claude calls {len(claude.calls)}")
    for message in smtp.sent:
        print(f"\nreply to {message['To']}: {message['Subject']}\n{message.get_content()}")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
