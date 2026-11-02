"""Setting up the state an acceptance criterion names, through the public HTTP API.

The criteria of ``docs/UC-z-finance.md`` start from a described situation ("balance 2,000 €, reserve
1,000 €, a planned item of -1,500 € on the 10th"). These helpers write exactly that situation into an
empty instance the way the program's own API does it, so what the browser then shows went through the
same rules as a user's data. Typing the situation into the UI instead is the job of the tests whose
criterion is about the typing (UC-ZF-002).

Amounts are integer cents everywhere, as in the API. Dates are relative to the day the run happens
on, never fixed to a calendar: a criterion that says "in ten months" has to hold in March too.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# The English default category set of zfinance.domain.accounts.service, by name.
CATEGORIES = (
    "Housing",
    "Groceries",
    "Eating out",
    "Clothing",
    "Fuel",
    "Public transport",
    "Insurance",
    "Leisure",
    "Health",
    "Subscriptions",
    "Fees",
    "Software",
    "Hardware",
    "Travel",
    "SVS",
    "Tax",
    "Owner draw/contribution",
)


def adopt_categories(app: Any) -> dict[str, int]:
    """Finish the first run with the offered default set; return the categories by name."""
    offer = app.get("/api/accounts/setup", language="en")
    adopted = app.post("/api/accounts/setup", {"categories": offer["default_categories"]})
    return {row["name"]: int(row["id"]) for row in adopted}


def account(
    app: Any,
    name: str,
    *,
    area: str = "private",
    kind: str = "giro",
    opening_cents: int = 0,
    opening: date | None = None,
    reserve_cents: int = 0,
) -> int:
    """Create an account and return its id (opening date: five months back by default)."""
    body = {
        "name": name,
        "area": area,
        "type": kind,
        "opening_balance_cents": opening_cents,
        "opening_date": (opening or (date.today() - timedelta(days=150))).isoformat(),
        "reserve_floor_cents": reserve_cents,
    }
    return int(app.post("/api/accounts", body)["id"])


def book(
    app: Any,
    *,
    day: date,
    cents: int,
    account_id: int,
    category_id: int | None = None,
    text: str = "Booking",
) -> int:
    """Book one transaction (a day in the future lands as a planned item, as UC-ZF-002 3c says)."""
    body = {
        "date": day.isoformat(),
        "amount_cents": cents,
        "account_id": account_id,
        "category_id": category_id,
        "text": text,
        "counterparty": text,
    }
    return int(app.post("/api/transactions", body)["id"])


def transfer(
    app: Any,
    *,
    day: date,
    cents: int,
    from_account_id: int,
    to_account_id: int,
    category_id: int | None = None,
    text: str = "Transfer",
) -> Any:
    """One transfer: the two coupled bookings of UC-ZF-002 3a."""
    body = {
        "date": day.isoformat(),
        "amount_cents": cents,
        "from_account_id": from_account_id,
        "to_account_id": to_account_id,
        "category_id": category_id,
        "text": text,
    }
    return app.post("/api/transactions/transfers", body)


def expected_item(
    app: Any,
    *,
    day: date,
    cents: int,
    account_id: int,
    category_id: int | None = None,
    text: str = "Expected",
) -> int:
    """A one-off expected item (a planned booking in the future)."""
    body = {
        "date": day.isoformat(),
        "amount_cents": cents,
        "account_id": account_id,
        "category_id": category_id,
        "text": text,
    }
    return int(app.post("/api/recurring/expected", body)["id"])


def recurring(
    app: Any,
    *,
    description: str,
    cents: int,
    account_id: int,
    category_id: int | None = None,
    interval: str = "monthly",
    day_of_month: int | None = 1,
    start: date | None = None,
    **extra: Any,
) -> int:
    """A recurring item; its planned items for the next twelve months are created with it."""
    body = {
        "description": description,
        "amount_cents": cents,
        "account_id": account_id,
        "category_id": category_id,
        "interval": interval,
        "day_of_month": day_of_month,
        "start_date": (start or date.today().replace(day=1)).isoformat(),
        **extra,
    }
    return int(app.post("/api/recurring", body)["id"])


def levy_settings(
    app: Any,
    *,
    rate_bp: int,
    account_id: int,
    pot_id: int | None = None,
    due: str = "07-01",
    base: str = "income",
) -> Any:
    """The levy rate and where the reserve sits (UC-ZF-015 preconditions)."""
    body = {
        "levy_rate_bp": rate_bp,
        "levy_base": base,
        "due_month_day": due,
        "levy_pot_id": pot_id,
        "account_id": account_id,
    }
    return app.put("/api/levy/settings", body)


def pot(app: Any, name: str, account_id: int, **extra: Any) -> int:
    """A savings pot on ``account_id``."""
    return int(app.post("/api/pots", {"name": name, "account_id": account_id, **extra})["id"])


def debt(
    app: Any,
    *,
    creditor: str,
    total_cents: int,
    repaid_cents: int = 0,
    installment_cents: int,
    pay_account_id: int,
    first_due: date,
    area: str = "private",
    interest_rate_pct: str = "0",
) -> int:
    """A debt: a liability account plus the recurring transfer of its instalment."""
    body = {
        "creditor": creditor,
        "area": area,
        "total_amount_cents": total_cents,
        "repaid_cents": repaid_cents,
        "installment_cents": installment_cents,
        "interval": "monthly",
        "first_due": first_due.isoformat(),
        "pay_account_id": pay_account_id,
        "interest_rate_pct": interest_rate_pct,
    }
    return int(app.post("/api/debts", body)["id"])


def planned_items(app: Any, recurring_id: int) -> list[dict[str, Any]]:
    """The occurrences of a recurring item that are still planned, earliest first."""
    items = app.get(f"/api/recurring/{recurring_id}/items")
    return sorted((item for item in items if item["status"] == "planned"), key=lambda item: item["date"])


def bulk_bookings(db_path: Path, account_id: int, *, count: int, planned: int, start: date) -> None:
    """Write ``count`` booked and ``planned`` planned rows straight into the database.

    The one direct write of the run that is not about time passing but about a volume (UC-ZF-006
    AK5, ten thousand bookings): those rows cannot be typed into a browser, and posting them one by
    one would measure the API instead of the render. Same technique as the backdating helpers of
    ``tools/demo_data.py``.
    """
    now = f"{date.today().isoformat()}T00:00:00+00:00"
    booked = [
        (
            (start + timedelta(days=index % 900)).isoformat(),
            -100 - (index % 500),
            account_id,
            f"Volume booking {index}",
            "booked",
            now,
        )
        for index in range(count)
    ]
    ahead = [
        (
            (date.today() + timedelta(days=1 + index % 350)).isoformat(),
            -1_000 - index,
            account_id,
            f"Volume planned {index}",
            "planned",
            now,
        )
        for index in range(planned)
    ]
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.executemany(
                'INSERT INTO "transaction" (date, amount_cents, account_id, text, status, created_at)'
                " VALUES (?, ?, ?, ?, ?, ?)",
                booked + ahead,
            )
    finally:
        conn.close()


def backdate_pot_plan(db_path: Path, pot_id: int, start: date) -> None:
    """Move the plan start of a pot into the past.

    A pot falls behind its plan only through months passing without a deposit (UC-ZF-011 AK3), which
    a run cannot wait for. Like the backdating helpers of ``tools/demo_data.py``, this writes the one
    column that carries the passing of time and nothing else.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.execute("UPDATE pot SET plan_start = ? WHERE id = ?", (start.isoformat(), pot_id))
    finally:
        conn.close()


def backdate_payoff_month(db_path: Path, month: str) -> None:
    """Move the earliest month of the saved payoff plan to ``month`` (``YYYY-MM``).

    A plan falls behind only when a month of it has passed without the special payment (UC-ZF-012
    AK4). The run cannot wait for the month, so the first month of the baseline is moved into the
    past; the payments that were not made stay unmade.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.execute(
                "UPDATE payoff_month SET month = ?, actual_cents = 0"
                " WHERE month = (SELECT min(month) FROM payoff_month)",
                (month,),
            )
    finally:
        conn.close()


def backdate_proposal(db_path: Path, proposal_id: int, created: date) -> None:
    """Let an allocation proposal have been waiting since ``created``.

    "Open for seven days" (UC-ZF-014 AK5) is a number of days that have passed, which a run cannot
    wait for; only the day the proposal was made is moved.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.execute(
                "UPDATE allocation_proposal SET created_at = ? WHERE id = ?",
                (f"{created.isoformat()}T08:00:00+00:00", proposal_id),
            )
    finally:
        conn.close()
