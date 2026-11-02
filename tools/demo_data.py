"""Demo data set for the manual's screenshots (T-2026-CU-9130-028, UC NF7 and NF11).

Every screenshot in ``docs/assets/screenshots/`` shows this data set and nothing else: obviously
synthetic names ("Demo Landlord", "Sample Supermarket"), round example amounts and no real
account, receipt or person. ``seed`` fills an empty z-finance through its public HTTP API, so the
data set is what a user could type in and goes through the same rules as real data. The only
direct database writes are ``backdate_planned`` (an item is overdue only through time passing,
which a demo cannot wait for) and ``backdate_liabilities`` (a debt account opens today).

Dates are relative to ``today``: the same story on any day, with the same amounts. The story:

* a private current account, a savings account, a wallet, and a business account (fees income);
* four months of bookings: rent, groceries, subscriptions, an owner draw every month;
* recurring items for the next twelve months, two of them overdue, one for more than 30 days;
* three debts, four pots, a 30 % levy rate, the tax prepayments, an allocation rule with one
  open proposal, one booked receipt and two receipts to check, an earlier bank statement;
* ``demo_statement_csv``: the statement the manual's import chapter walks through.

Run through ``tools/screenshots.py``; ``tests/test_demo_data.py`` checks the story.
"""

from __future__ import annotations

import sqlite3
import struct
import time
import zlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DEMO_LANGUAGES = ("en", "de")

# The marker drawn into every screenshot (tools/screenshots.py) and asserted by the tests.
EXAMPLE_MARKER = {"en": "EXAMPLE DATA", "de": "BEISPIELDATEN"}

# Levy rate of the demo: 30 % in basis points, the figure the manual's examples use (UC NF5).
LEVY_RATE_BP = 3000

# Positions in the default category sets of zfinance.domain.accounts.service (same order in every
# language), so the demo finds its categories without knowing their translated names.
_CATEGORY = {
    "housing": 0,
    "groceries": 1,
    "eating_out": 2,
    "fuel": 4,
    "insurance": 6,
    "health": 8,
    "subscriptions": 9,
    "fees": 10,
    "software": 11,
    "hardware": 12,
    "owner_draw": 16,
}

_NAMES: dict[str, dict[str, str]] = {
    "en": {
        "checking": "Example checking",
        "savings": "Example savings",
        "wallet": "Example wallet",
        "business": "Example business account",
        "landlord": "Demo Landlord",
        "supermarket": "Sample Supermarket",
        "fuel": "Demo Fuel Station",
        "streaming": "Streaming Demo Co",
        "restaurant": "Demo Restaurant",
        "insurance": "Demo Insurance",
        "client": "Client Example Ltd",
        "software": "Demo Software Inc",
        "hardware": "Demo Hardware Shop",
        "hosting": "Demo Web Hosting",
        "pharmacy": "Sample Pharmacy",
        "cafe": "Demo Cafe",
        "rent": "Rent",
        "draw": "Owner draw",
        "invoice": "Invoice",
        "retainer": "Retainer",
        "laptop": "New laptop",
        "car": "Example Car Finance",
        "equipment": "Example Equipment Loan",
        "furniture": "Sample Furniture Store",
        "pot_emergency": "Emergency fund",
        "pot_holiday": "Holiday",
        "pot_retirement": "Retirement top-up",
        "pot_levy": "Levy reserve",
        "rule_split": "Fees split",
        "bank": "Example Bank",
        "refund": "Refund Sample Shop",
        "rule_supermarket": "Supermarket",
        "rule_pharmacy": "Pharmacy",
        "rule_fuel": "Fuel Station",
        "groceries_hint": "Groceries",
    },
    "de": {
        "checking": "Beispiel-Girokonto",
        "savings": "Beispiel-Sparkonto",
        "wallet": "Beispiel-Geldbörse",
        "business": "Beispiel-Geschäftskonto",
        "landlord": "Demo-Vermieter",
        "supermarket": "Muster-Supermarkt",
        "fuel": "Demo-Tankstelle",
        "streaming": "Streaming Demo GmbH",
        "restaurant": "Demo-Restaurant",
        "insurance": "Demo-Versicherung",
        "client": "Kunde Beispiel GmbH",
        "software": "Demo-Software AG",
        "hardware": "Demo-Elektronikmarkt",
        "hosting": "Demo-Webhosting",
        "pharmacy": "Muster-Apotheke",
        "cafe": "Demo-Café",
        "rent": "Miete",
        "draw": "Privatentnahme",
        "invoice": "Rechnung",
        "retainer": "Betreuungspauschale",
        "laptop": "Neuer Laptop",
        "car": "Beispiel-Autofinanzierung",
        "equipment": "Beispiel-Geräteleasing",
        "furniture": "Muster-Möbelhaus",
        "pot_emergency": "Notgroschen",
        "pot_holiday": "Urlaub",
        "pot_retirement": "Altersvorsorge",
        "pot_levy": "Abgaben-Rücklage",
        "rule_split": "Honorar-Aufteilung",
        "bank": "Beispielbank",
        "refund": "Erstattung Muster-Shop",
        "rule_supermarket": "Supermarkt",
        "rule_pharmacy": "Apotheke",
        "rule_fuel": "Tankstelle",
        "groceries_hint": "Lebensmittel",
    },
}


# The seeder needs get/post/put with the httpx signature: an ``httpx.Client`` or Starlette's ``TestClient``.
Http = Any


def name(lang: str, key: str) -> str:
    """A demo name in ``lang``: ``name("de", "checking")`` is the German name of the current account."""
    return _NAMES[lang][key]


@dataclass(frozen=True)
class DemoIds:
    """Ids of the rows ``seed`` created, for the screenshot script and the tests."""

    checking: int
    savings: int
    wallet: int
    business: int
    categories: dict[str, int]
    debts: list[int]
    pots: dict[str, int]
    recurring: dict[str, int]
    profile: int


def _ok(response: Any) -> Any:
    """The JSON of a 2xx answer; anything else stops the seeding with the server's message."""
    if response.status_code >= 300:
        raise RuntimeError(f"demo seeding failed: {response.request.method} {response.request.url}: {response.text}")
    return response.json()


def _month_start(today: date, back: int) -> date:
    """The first day of the month ``back`` months before ``today``'s month (negative = after)."""
    index = today.year * 12 + today.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def _eur(cents: int) -> str:
    """``-123456`` -> ``-1.234,56`` (German notation, as the demo bank writes it)."""
    sign = "-" if cents < 0 else ""
    euros, rest = divmod(abs(cents), 100)
    return f"{sign}{euros:,}".replace(",", ".") + f",{rest:02d}"


def demo_statement_csv(today: date, lang: str = "en") -> str:
    """The bank statement the import chapter walks through: one line of each kind.

    Semicolon-separated, ``dd.mm.yyyy`` dates, decimal comma. Against the seeded ledger the lines
    come out as: two already imported (grey), one resembling the coffee booked by hand, one
    matching the overdue streaming item, three new ones of which a rule categorises two.
    """
    n = _NAMES[lang]
    header = "Date;Text;Amount;Counterparty" if lang == "en" else "Datum;Text;Betrag;Gegenpartei"
    rows = [
        (today - timedelta(days=20), n["pharmacy"].upper(), -1420, n["pharmacy"]),
        (today - timedelta(days=14), n["fuel"].upper(), -6130, n["fuel"]),
        (today - timedelta(days=4), n["cafe"].upper() + " 12", -480, n["cafe"]),
        (today - timedelta(days=3), n["streaming"], -1299, n["streaming"]),
        (today - timedelta(days=2), n["supermarket"].upper() + " 4711", -3720, n["supermarket"]),
        (today - timedelta(days=2), n["fuel"].upper() + " 08", -5800, n["fuel"]),
        (today - timedelta(days=1), n["refund"], 2490, ""),
    ]
    lines = [f"{d.strftime('%d.%m.%Y')};{text};{_eur(cents)};{who}" for d, text, cents, who in rows]
    return "\n".join([header, *lines]) + "\n"


def receipt_png(variant: int) -> bytes:
    """A small synthetic receipt-like PNG (grey text bars on white paper); ``variant`` changes the bars.

    Written with ``zlib`` and ``struct`` so the demo needs no imaging library; no real receipt is
    ever used (UC NF7).
    """
    width, height = 180, 260
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type: none
        length = 60 + ((y // 12) * 37 + variant * 23) % 100
        for x in range(width):
            in_text = 20 <= x < 20 + length and 24 <= y < height - 24 and (y // 6) % 2 == 0
            grey = 96 if in_text else 250
            rows += bytes((grey, grey, grey))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def backdate_planned(db_path: Path, transaction_id: int, new_date: date) -> None:
    """Move one planned booking to a past date, so it shows up as overdue.

    The only direct database write of the demo. It changes ``date`` and leaves ``due_date`` alone,
    which is what an item looks like that was generated earlier and not confirmed in time.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.execute(
                "UPDATE \"transaction\" SET date = ? WHERE id = ? AND status = 'planned'",
                (new_date.isoformat(), transaction_id),
            )
    finally:
        conn.close()


def backdate_liabilities(db_path: Path, new_date: date) -> None:
    """Let the liability accounts of the debts start earlier (the demo debts are months old).

    ``create_debt`` opens a debt's account today. Without this the dashboard would show every
    debt as new since the end of last month, and a payment dated earlier would be refused.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.execute("UPDATE account SET opening_date = ? WHERE type = 'liability'", (new_date.isoformat(),))
    finally:
        conn.close()


def _first_planned(client: Http, recurring_id: int) -> int:
    """Id of the earliest planned occurrence of a recurring item."""
    items = _ok(client.get(f"/api/recurring/{recurring_id}/items"))
    planned = sorted((i for i in items if i["status"] == "planned"), key=lambda i: i["date"])
    return int(planned[0]["id"])


def seed(client: Http, *, lang: str, today: date, db_path: Path | None = None) -> DemoIds:
    """Fill an empty z-finance with the demo story; return the ids of the main rows.

    ``client`` talks to a z-finance whose data directory is empty and whose first run is not yet
    complete. ``db_path`` is the SQLite file of that instance; without it the two overdue items
    are left out.
    """
    if lang not in DEMO_LANGUAGES:
        raise ValueError(f"unsupported demo language: {lang}")
    n = _NAMES[lang]

    _ok(client.put("/api/i18n/language", json={"language": lang}))

    # --- first run: the default categories in the language of the demo ---------------------------
    offer = _ok(client.get("/api/accounts/setup", params={"language": lang}))
    adopted = _ok(client.post("/api/accounts/setup", json={"categories": offer["default_categories"]}))
    cat = {key: adopted[index]["id"] for key, index in _CATEGORY.items()}

    # --- accounts ---------------------------------------------------------------------------------
    opening = _month_start(today, 5)

    def account(key: str, area: str, kind: str, balance: int, floor: int = 0) -> int:
        body = {
            "name": n[key],
            "area": area,
            "type": kind,
            "opening_balance_cents": balance,
            "opening_date": opening.isoformat(),
            "reserve_floor_cents": floor,
        }
        return int(_ok(client.post("/api/accounts", json=body))["id"])

    checking = account("checking", "private", "giro", 320_000, 50_000)
    savings = account("savings", "private", "savings", 850_000)
    wallet = account("wallet", "private", "cash", 15_000)
    business = account("business", "business", "giro", 600_000, 100_000)
    _ok(client.put("/api/accounts/settings", json={"total_reserve_floor_cents": 200_000}))

    # --- rules (before the bookings they are meant for) ------------------------------------------
    for word, category in (
        (n["rule_supermarket"], "groceries"),
        (n["rule_pharmacy"], "health"),
        (n["rule_fuel"], "fuel"),
    ):
        rule = {"match_kind": "contains", "pattern": word, "category_id": cat[category]}
        _ok(client.post("/api/rules", json=rule))

    # --- five months of history ---------------------------------------------------------------------
    def book(day: date, cents: int, account_id: int, category: str | None, text: str) -> None:
        if day >= today:
            return
        body = {
            "date": day.isoformat(),
            "amount_cents": cents,
            "account_id": account_id,
            "category_id": cat[category] if category else None,
            "text": text,
            "counterparty": text,
        }
        _ok(client.post("/api/transactions", json=body))

    for back in (4, 3, 2, 1, 0):
        start = _month_start(today, back)
        wobble = 4 - back
        book(start, -95_000, checking, "housing", n["landlord"])
        for day, cents in ((3, -4630), (10, -6285), (17, -7110), (24, -3845)):
            book(start.replace(day=day), cents - wobble * 137, checking, "groceries", n["supermarket"])
        book(start.replace(day=8), -5500 - wobble * 210, checking, "fuel", n["fuel"])
        book(start.replace(day=12), -1299, checking, "subscriptions", n["streaming"])
        if back % 2 == 0:
            book(start.replace(day=20), -3500, checking, "eating_out", n["restaurant"])
        if back % 3 == 0:
            book(start.replace(day=15), -18_600, checking, "insurance", n["insurance"])
        book(start.replace(day=5), 340_000 + wobble * 15_000, business, "fees", n["client"])
        book(start.replace(day=19), 230_000 - wobble * 10_000, business, "fees", n["client"])
        book(start.replace(day=9), -4900, business, "software", n["software"])
        if back == 2:
            book(start.replace(day=14), -32_000, business, "hardware", n["hardware"])
        draw_day = start.replace(day=25)
        if draw_day < today:
            body = {
                "date": draw_day.isoformat(),
                "amount_cents": 220_000,
                "from_account_id": business,
                "to_account_id": checking,
                "category_id": cat["owner_draw"],
                "text": n["draw"],
            }
            _ok(client.post("/api/transactions/transfers", json=body))
    # Uncategorised bookings for the Month screen; the last coffee is what the statement resembles.
    book(today - timedelta(days=9), -2350, checking, None, n["cafe"])
    book(today - timedelta(days=7), -1780, checking, None, n["restaurant"])
    book(today - timedelta(days=5), -480, checking, None, n["cafe"])
    book(today - timedelta(days=11), -2000, wallet, None, n["supermarket"])

    # --- recurring items and expected items ------------------------------------------------------------
    def recurring(key: str, cents: int, account_id: int, category: str, interval: str, day: int, **extra: Any) -> int:
        body = {
            "description": n[key],
            "amount_cents": cents,
            "account_id": account_id,
            "category_id": cat[category],
            "interval": interval,
            "day_of_month": day,
            "start_date": _month_start(today, 0).isoformat(),
            **extra,  # a transfer target, or another description
        }
        return int(_ok(client.post("/api/recurring", json=body))["id"])

    rec = {
        "rent": recurring("rent", -95_000, checking, "housing", "monthly", 1),
        "streaming": recurring("streaming", -1299, checking, "subscriptions", "monthly", 12),
        "insurance": recurring("insurance", -18_600, checking, "insurance", "quarterly", 15),
        "draw": recurring("draw", 220_000, business, "owner_draw", "monthly", 25, transfer_target_account_id=checking),
        "retainer": recurring(
            "client", 70_000, business, "fees", "monthly", 5, description=f"{n['client']} - {n['retainer']}"
        ),
        "software": recurring("software", -4900, business, "software", "monthly", 9),
        "hosting": recurring("hosting", -12_000, business, "software", "yearly", 20),
    }
    expected = (
        (21, 250_000, "fees", f"{n['client']} - {n['invoice']}"),
        (37, -140_000, "hardware", n["laptop"]),
    )
    for days, cents, category, text in expected:
        body = {
            "date": (today + timedelta(days=days)).isoformat(),
            "amount_cents": cents,
            "account_id": business,
            "category_id": cat[category],
            "text": text,
        }
        _ok(client.post("/api/recurring/expected", json=body))
    if db_path is not None:
        backdate_planned(db_path, _first_planned(client, rec["streaming"]), today - timedelta(days=3))
        backdate_planned(db_path, _first_planned(client, rec["insurance"]), today - timedelta(days=35))

    # --- tax plan ---------------------------------------------------------------------------------------
    tax = {"business_account_id": business, "est_vz_quarter_cents": 90_000, "svs_quarter_cents": 150_000}
    _ok(client.put("/api/settings/tax", json=tax))

    # --- debts and payoff plan ------------------------------------------------------------------------
    def debt(key: str, area: str, total: int, repaid: int, instalment: int, day: int, pay: int, rate: str) -> int:
        body = {
            "creditor": n[key],
            "area": area,
            "total_amount_cents": total,
            "repaid_cents": repaid,
            "installment_cents": instalment,
            "interval": "monthly",
            "first_due": _month_start(today, -1).replace(day=day).isoformat(),
            "pay_account_id": pay,
            "interest_rate_pct": rate,
        }
        return int(_ok(client.post("/api/debts", json=body))["id"])

    debts = [
        debt("car", "private", 1_400_000, 400_000, 32_000, 5, checking, "4.9"),
        debt("equipment", "business", 600_000, 100_000, 25_000, 10, business, "6.2"),
        debt("furniture", "private", 120_000, 20_000, 10_000, 20, checking, "0"),
    ]
    if db_path is not None:
        # One instalment paid yesterday per debt: the remaining debt is lower than last month.
        backdate_liabilities(db_path, opening)
        for debt_id, instalment, pay in zip(
            debts, (32_000, 25_000, 10_000), (checking, business, checking), strict=True
        ):
            payment = {
                "amount_cents": instalment,
                "date": (today - timedelta(days=1)).isoformat(),
                "from_account_id": pay,
            }
            _ok(client.post(f"/api/debts/{debt_id}/payments", json=payment))
    _ok(client.put("/api/payoff", json={"strategy": "snowball", "budget_cents": 30_000}))

    # --- pots -----------------------------------------------------------------------------------------------
    def pot(key: str, account_id: int, **extra: Any) -> int:
        return int(_ok(client.post("/api/pots", json={"name": n[key], "account_id": account_id, **extra}))["id"])

    target = _month_start(today, -8).replace(day=15).isoformat()
    pots = {
        "holiday": pot("pot_holiday", savings, target_amount_cents=240_000, target_date=target),
        "emergency": pot("pot_emergency", savings, target_amount_cents=500_000),
        "retirement": pot("pot_retirement", savings, locked=True),
        "levy": pot("pot_levy", business, is_levy_pot=True),
    }
    for key, cents in (("holiday", 90_000), ("emergency", 320_000), ("retirement", 150_000), ("levy", 300_000)):
        _ok(client.post(f"/api/pots/{pots[key]}/deposits", json={"amount_cents": cents, "date": today.isoformat()}))
    ranks = {"account_id": savings, "pot_ids": [pots["holiday"], pots["emergency"]]}
    _ok(client.put("/api/pots/ranks", json=ranks))
    plan = {"from_account_id": checking, "amount_cents": 15_000}
    _ok(client.put(f"/api/pots/{pots['holiday']}/savings-plan", json=plan))

    levy = {
        "levy_rate_bp": LEVY_RATE_BP,
        "levy_base": "income",
        "due_month_day": "07-01",
        "levy_pot_id": pots["levy"],
        "account_id": business,
    }
    _ok(client.put("/api/levy/settings", json=levy))

    # --- a bank profile and an earlier statement --------------------------------------------------------
    columns = (
        {"date": "Date", "text": "Text", "amount": "Amount", "counterparty": "Counterparty"}
        if lang == "en"
        else {"date": "Datum", "text": "Text", "amount": "Betrag", "counterparty": "Gegenpartei"}
    )
    profile = _ok(
        client.post(
            "/api/imports/profiles",
            json={
                "bank_name": n["bank"],
                "column_map": columns,
                "delimiter": "semicolon",
                "encoding": "utf-8",
                "date_format": "%d.%m.%Y",
                "decimal_sep": ",",
            },
        )
    )
    statement = demo_statement_csv(today, lang).splitlines()
    earlier = "\n".join(statement[:3]) + "\n"  # header plus the two oldest lines
    preview = _ok(
        client.post(
            "/api/imports/preview",
            params={"profile_id": profile["id"], "account_id": checking},
            content=earlier.encode("utf-8"),
            headers={"Content-Type": "text/csv"},
        )
    )
    _ok(client.post("/api/imports/commit", json={"preview_id": preview["preview_id"]}))

    # --- allocation: a rule first, then the income it applies to -------------------------------------------
    # A rule only proposes for income booked after it was saved; ``created_at`` has one-second
    # resolution, so the history above must be older than the rule by more than a second.
    time.sleep(1.1)
    rule = {
        "name": n["rule_split"],
        "area": "business",
        "category_id": cat["fees"],
        "min_amount_cents": 50_000,
        "targets": [
            {"kind": "debt_plan", "percent_bp": 2000},
            {"kind": "pot", "pot_id": pots["holiday"], "percent_bp": 1000},
        ],
        "fallback_kind": "reserve",
    }
    _ok(client.post("/api/allocation/rules", json=rule))
    book(today - timedelta(days=2), 400_000, business, "fees", n["client"])

    # --- receipts: one booked, one unsure, one unreadable -------------------------------------------------
    def receipt(variant: int, **fields: Any) -> None:
        photo = ("demo-receipt.png", receipt_png(variant), "image/png")
        _ok(client.post("/api/receipts", files={"photo": photo}, data={k: str(v) for k, v in fields.items()}))

    receipt(
        1,
        date=(today - timedelta(days=6)).isoformat(),
        amount_cents=2340,
        merchant=n["supermarket"],
        payment_method="card",
        category_suggestion=n["groceries_hint"],
        confidence=0.95,
    )
    receipt(
        2,
        date=(today - timedelta(days=4)).isoformat(),
        amount_cents=8750,
        merchant=n["hardware"],
        payment_method="card",
        confidence=0.42,
    )
    receipt(3, unreadable="true", confidence=0.1)

    return DemoIds(
        checking=checking,
        savings=savings,
        wallet=wallet,
        business=business,
        categories=cat,
        debts=debts,
        pots=pots,
        recurring=rec,
        profile=int(profile["id"]),
    )
