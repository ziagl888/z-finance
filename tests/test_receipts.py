"""UC-ZF-013 receipt API: duplicates, review list, booking with attachment, hints, card match, upload safety.

Expected values are hand-computed from the use case and docs/specs/SPEC-T-2026-CU-9130-016.md.
Every photo is a few synthetic bytes with a real image signature; no real receipt is used.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zfinance.config import Settings
from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.receipts import service
from zfinance.domain.receipts.service import Correction, Extracted
from zfinance.domain.recurring import service as recurring
from zfinance.domain.rules import service as rules
from zfinance.domain.rules.service import RuleFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

DAY = date.today() - timedelta(days=10)


def jpeg(tag: str) -> bytes:
    """A tiny synthetic 'JPEG': the JPEG signature, a tag that makes each photo unique, the end marker."""
    return b"\xff\xd8\xff\xe0" + tag.encode() + b"\xff\xd9"


def png(tag: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag.encode()


def readable(**more: Any) -> Extracted:
    """A receipt read with full confidence: 12.34 EUR at a synthetic shop."""
    values: dict[str, Any] = {"date": DAY, "amount_cents": 1_234, "merchant": "Test shop", "confidence": 0.95}
    values.update(more)
    return Extracted(**values)


# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "receipts.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def ledger(conn: sqlite3.Connection) -> dict[str, int]:
    """Private giro + cash, business giro; opened a year ago."""
    opened = date.today() - timedelta(days=365)

    def account(name: str, area: str, kind: str) -> int:
        return accounts.create_account(conn, AccountFields(name, area, kind, 0, opened)).id  # type: ignore[arg-type]

    return {
        "giro": account("Test giro", "private", "giro"),
        "cash": account("Test wallet", "private", "cash"),
        "business": account("Test business giro", "business", "giro"),
    }


def _bookings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute('SELECT * FROM "transaction" ORDER BY id').fetchall()


# --- AK1 ---------------------------------------------------------------------------------------


def test_receipt_api_creates_booked_with_attachment(client: TestClient, settings: Settings) -> None:
    """A readable receipt becomes exactly one booked expense with the photo attached (AK1).

    Oracle: spec-anchored — UC-ZF-013 AK1 and interface details (booking `booked`, `source=receipt`,
    photo under data/receipts/<year>/<id>.jpg); SPEC AK16 test name.
    """
    giro = client.post(
        "/api/accounts",
        json={
            "name": "Test giro",
            "area": "private",
            "type": "giro",
            "opening_balance_cents": 0,
            "opening_date": "2020-01-01",
            "reserve_floor_cents": 0,
        },
    ).json()["id"]
    photo = jpeg("ak1")
    response = client.post(
        "/api/receipts",
        data={"date": DAY.isoformat(), "amount_cents": "1234", "merchant": "Test shop", "confidence": "0.95"},
        files={"photo": ("../../evil.exe", photo, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "booked" and body["reason"] is None and body["matched"] is False

    bookings = client.get("/api/transactions").json()
    assert len(bookings) == 1
    booking = bookings[0]
    assert booking["id"] == body["transaction_id"]
    assert (booking["amount_cents"], booking["date"], booking["account_id"]) == (-1_234, DAY.isoformat(), giro)
    assert booking["status"] == "booked" and booking["source"] == "receipt" and booking["text"] == "Test shop"

    stored = list((settings.data_dir / "receipts").rglob("*"))
    files = [p for p in stored if p.is_file()]
    assert [p.relative_to(settings.data_dir).as_posix() for p in files] == [
        f"receipts/{date.today().year}/{body['receipt_id']}.jpg"
    ]
    receipt = client.get(f"/api/receipts/{body['receipt_id']}").json()
    assert receipt["transaction_id"] == booking["id"] and receipt["photo_url"].endswith("/photo")
    served = client.get(receipt["photo_url"])
    assert served.status_code == 200 and served.content == photo
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["x-content-type-options"] == "nosniff"


# --- AK2 ---------------------------------------------------------------------------------------


def test_receipt_duplicate_hash(conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]) -> None:
    """The same photo a second time → duplicate, no second booking, answer names the first (AK2, 5a).

    Oracle: spec-anchored — UC-ZF-013 AK2 ("keine zweite Buchung, Rückmeldung schon erfasst"), 5a.
    """
    first = service.submit_receipt(conn, data_dir, jpeg("dup"), readable())
    again = service.submit_receipt(conn, data_dir, jpeg("dup"), readable(confidence=0.1))
    assert first.status == "booked"
    assert again.status == "duplicate" and again.receipt_id is None
    assert (again.duplicate_of, again.transaction_id) == (first.receipt_id, first.transaction_id)
    assert len(_bookings(conn)) == 1
    assert conn.execute("SELECT count(*) FROM receipt").fetchone()[0] == 1


def test_receipt_duplicate_key_keeps_photo(conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]) -> None:
    """Another photo with the same date, total and merchant → duplicate item with photo, no booking (AK2).

    Oracle: spec-anchored — UC-ZF-013 interface details (duplicate key = photo hash plus date,
    amount, merchant; "kein Foto geht verloren").
    """
    first = service.submit_receipt(conn, data_dir, jpeg("one"), readable())
    second = service.submit_receipt(conn, data_dir, png("two"), readable(merchant="  TEST SHOP "))
    assert second.status == "duplicate" and second.duplicate_of == first.receipt_id
    assert second.receipt_id is not None and second.transaction_id is None
    assert len(_bookings(conn)) == 1
    listed = service.list_receipts(conn, ["review", "duplicate"])
    assert [r.id for r in listed] == [second.receipt_id]
    path, media_type = service.photo_file(conn, data_dir, second.receipt_id)
    assert path.read_bytes() == png("two") and media_type == "image/png"


# --- AK3 ---------------------------------------------------------------------------------------


def test_receipt_low_confidence_review(conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]) -> None:
    """Confidence 0.79 below the default 0.8 → review item with photo and reason, no booking (AK3).

    Oracle: spec-anchored — UC-ZF-013 "Unsicherheit" + task (threshold setting, default 0.8).
    """
    result = service.submit_receipt(conn, data_dir, jpeg("low"), readable(confidence=0.79))
    assert (result.status, result.reason, result.transaction_id) == ("review", "low_confidence", None)
    assert _bookings(conn) == []
    item = service.get_receipt(conn, result.receipt_id or 0)
    assert item.extracted["merchant"] == "Test shop" and item.confidence == 0.79
    assert service.photo_file(conn, data_dir, item.id)[0].is_file()
    # Exactly at the threshold books.
    assert service.submit_receipt(conn, data_dir, jpeg("edge"), readable(confidence=0.8, amount_cents=999)).status == (
        "booked"
    )


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        (Extracted(date=DAY, merchant="Test shop", confidence=0.99), "missing_fields"),  # AK3: no amount
        (readable(merchant=None), "missing_fields"),
        (readable(date=None), "missing_fields"),
        (readable(unreadable=True), "unreadable"),  # 3a
        (readable(currency="CHF"), "foreign_currency"),  # 3c
        (readable(confidence=None), "low_confidence"),
        (readable(date=date(2000, 1, 1)), "not_bookable"),  # before the opening date
    ],
)
def test_review_reasons(
    conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int], fields: Extracted, reason: str
) -> None:
    """Each unsure case is a review item with its reason and no booking (AK3, 3a, 3c).

    Oracle: spec-anchored — UC-ZF-013 AK3, extensions 3a/3c; SPEC-T-2026-CU-9130-016 review reasons.
    """
    result = service.submit_receipt(conn, data_dir, jpeg(repr(fields)), fields)
    assert (result.status, result.reason) == ("review", reason)
    assert _bookings(conn) == []


def test_correction_books_review_item(conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]) -> None:
    """Correcting a review item creates the booking (source receipt) and removes it from the list (AK3).

    Oracle: spec-anchored — UC-ZF-013 AK3 ("Korrektur im Tool erzeugt die Buchung"), step 7.
    """
    result = service.submit_receipt(conn, data_dir, jpeg("fix"), Extracted(merchant="Test shop"))
    receipt_id = result.receipt_id or 0
    booked = service.book_receipt(conn, receipt_id, Correction(DAY, 870, "Test shop", ledger["cash"]))
    assert booked.status == "booked" and booked.reason is None
    booking = transactions.get_transaction(conn, booked.transaction_id or 0)
    assert (booking.amount_cents, booking.account_id, booking.source) == (-870, ledger["cash"], "receipt")
    assert service.list_receipts(conn, ["review", "duplicate"]) == []
    with pytest.raises(service.Conflict):
        service.book_receipt(conn, receipt_id, Correction(DAY, 870, "Test shop", ledger["cash"]))
    with pytest.raises(service.Conflict):
        service.delete_receipt(conn, data_dir, receipt_id)


def test_discard_removes_receipt_and_photo(conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]) -> None:
    """A review item can be discarded; its photo goes with it.

    Oracle: spec-anchored — SPEC-T-2026-CU-9130-016 design decision "Correction" (discard).
    """
    result = service.submit_receipt(conn, data_dir, jpeg("junk"), Extracted(unreadable=True))
    path, _ = service.photo_file(conn, data_dir, result.receipt_id or 0)
    service.delete_receipt(conn, data_dir, result.receipt_id or 0)
    assert not path.exists()
    assert conn.execute("SELECT count(*) FROM receipt").fetchone()[0] == 0


def test_new_photo_never_writes_through_an_orphan_link(
    conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]
) -> None:
    """A photo stored at a path where an orphan file lies replaces it without touching other links to it.

    Oracle: spec-anchored — backup.files invariant "a photo is write-once": a backup snapshot holds hard
    links to the live photos (SPEC-T-2026-CU-9130-033), so a new receipt that reuses an id (after the
    database was started fresh) must not rewrite the bytes a snapshot still points to.
    """
    first = service.submit_receipt(conn, data_dir, jpeg("old"), Extracted(unreadable=True))
    live, _ = service.photo_file(conn, data_dir, first.receipt_id or 0)
    snapshot_copy = data_dir / "snapshot-link.jpg"
    snapshot_copy.hardlink_to(live)
    conn.execute("DELETE FROM receipt")  # the database is started fresh, the photo file stays behind

    second = service.submit_receipt(conn, data_dir, jpeg("new"), Extracted(unreadable=True))
    assert service.photo_file(conn, data_dir, second.receipt_id or 0)[0] == live  # the id came back
    assert live.read_bytes() == jpeg("new")
    assert snapshot_copy.read_bytes() == jpeg("old")


# --- AK4 ---------------------------------------------------------------------------------------


def test_hint_business_and_cash(conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]) -> None:
    """Hint "business" → business account; hint "bar" → the private cash account (AK4, step 3).

    Oracle: spec-anchored — UC-ZF-013 AK4 ("Text business → Buchung im Bereich business auf dem
    Business-Konto"), step 1/3 hints "bar"; SPEC-T-2026-CU-9130-016 account mapping.
    """
    business = service.submit_receipt(conn, data_dir, jpeg("b"), readable(hints="Business"))
    cash = service.submit_receipt(conn, data_dir, jpeg("c"), readable(amount_cents=500, hints="bar"))
    card = service.submit_receipt(conn, data_dir, jpeg("d"), readable(amount_cents=600))
    by_id = {row["id"]: row["account_id"] for row in _bookings(conn)}
    assert by_id[business.transaction_id] == ledger["business"]
    assert by_id[cash.transaction_id] == ledger["cash"]
    assert by_id[card.transaction_id] == ledger["giro"]


def test_category_hint_suggestion_and_rules(conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]) -> None:
    """Category: hint beats suggestion; suggestion by name; without either the rules decide (step 3/5).

    Oracle: spec-anchored — UC-ZF-013 step 3 ("Text überschreibt den Vorschlag") and step 5
    ("wendet Regeln an").
    """
    food = accounts.create_category(conn, CategoryFields("Test food", "expense")).id
    office = accounts.create_category(conn, CategoryFields("Test office", "expense")).id
    rules.create_rule(conn, RuleFields("contains", "stationer", office))
    hinted = service.submit_receipt(
        conn, data_dir, jpeg("h"), readable(hints="test food", category_suggestion="Test office")
    )
    suggested = service.submit_receipt(
        conn, data_dir, jpeg("s"), readable(amount_cents=200, category_suggestion="test OFFICE")
    )
    ruled = service.submit_receipt(conn, data_dir, jpeg("r"), readable(amount_cents=300, merchant="Test stationer"))
    categories = {row["id"]: row["category_id"] for row in _bookings(conn)}
    assert categories[hinted.transaction_id] == food
    assert categories[suggested.transaction_id] == office
    assert categories[ruled.transaction_id] == office


# --- AK5 ---------------------------------------------------------------------------------------


def test_card_match_attaches_to_existing_booking(
    conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]
) -> None:
    """A card receipt with a booking of the same amount 3 days apart is attached to it; 4 days → new (5b).

    Oracle: spec-anchored — UC-ZF-013 extension 5b; orchestrator note "Karten-Match ±3 Tage hängt
    an bestehende Buchung".
    """
    existing = transactions.create_transaction(
        conn, BookingFields(DAY + timedelta(days=3), -1_234, ledger["giro"], None, "CARD PAYMENT TEST")
    )
    matched = service.submit_receipt(conn, data_dir, jpeg("m"), readable(payment_method="card"))
    assert matched.status == "booked" and matched.matched and matched.transaction_id == existing.id
    assert len(_bookings(conn)) == 1

    transactions.create_transaction(
        conn, BookingFields(DAY + timedelta(days=4), -2_000, ledger["giro"], None, "CARD PAYMENT TEST")
    )
    far = service.submit_receipt(conn, data_dir, jpeg("f"), readable(amount_cents=2_000, payment_method="card"))
    assert far.status == "booked" and not far.matched
    assert len(_bookings(conn)) == 3
    # A cash receipt never matches.
    transactions.create_transaction(conn, BookingFields(DAY, -700, ledger["cash"], None, "x"))
    cash = service.submit_receipt(conn, data_dir, jpeg("x"), readable(amount_cents=700, hints="bar"))
    assert not cash.matched


def test_card_receipt_on_planned_item_follows_the_booking(
    conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]
) -> None:
    """A card receipt attaches to a planned item (5b); confirming it moves the receipt to the booking.

    Oracle: spec-anchored — UC-ZF-013 extension 5b "Bon matcht eine geplante oder importierte
    Buchung ... Foto wird als Beleg an die bestehende Buchung gehängt"; recurring.replace_planned
    contract (the real booking takes the planned row's place).
    """
    planned = transactions.create_transaction(
        conn, BookingFields(DAY + timedelta(days=1), -1_234, ledger["giro"], None, "SUBSCRIPTION TEST")
    )
    conn.execute("UPDATE \"transaction\" SET status = 'planned' WHERE id = ?", (planned.id,))

    result = service.submit_receipt(conn, data_dir, jpeg("p"), readable(payment_method="card"))
    assert result.status == "booked" and result.matched and result.transaction_id == planned.id

    real = transactions.create_transaction(
        conn, BookingFields(DAY + timedelta(days=1), -1_234, ledger["giro"], None, "SUBSCRIPTION TEST")
    )
    recurring.replace_planned(conn, planned.id, real.id)

    assert result.receipt_id is not None
    assert service.get_receipt(conn, result.receipt_id).transaction_id == real.id


# --- AK6 ---------------------------------------------------------------------------------------


def test_upload_security_boundary(client: TestClient, settings: Settings) -> None:
    """Non-image bytes, oversized uploads and path-like names never write outside data/receipts (AK6).

    Oracle: spec-anchored — orchestrator security boundary (magic bytes, size limit, file name never
    from the request, delivery by id only).
    """
    fake = client.post(
        "/api/receipts",
        data={"merchant": "Test shop"},
        files={"photo": ("r.jpg", b"<html><script>alert(1)</script>", "image/jpeg")},
    )
    assert fake.status_code == 415
    too_big = client.post(
        "/api/receipts",
        files={"photo": ("r.jpg", b"\xff\xd8\xff" + b"0" * service.MAX_PHOTO_BYTES, "image/jpeg")},
    )
    assert too_big.status_code == 413
    missing = client.post("/api/receipts", data={"merchant": "Test shop"})
    assert missing.status_code == 400
    bad_field = client.post(
        "/api/receipts", data={"amount_cents": "12.30"}, files={"photo": ("r.jpg", jpeg("f"), "image/jpeg")}
    )
    assert bad_field.status_code == 422
    stored = client.post(
        "/api/receipts",
        data={"message_ref": "../../x"},
        files={"photo": ("../../../outside.png", png("p"), "text/html")},
    )
    assert stored.status_code == 201 and stored.json()["status"] == "review"
    files = [p for p in settings.data_dir.rglob("*") if p.is_file() and p.suffix in {".png", ".jpg", ".webp"}]
    assert [p.relative_to(settings.data_dir).parts[0] for p in files] == ["receipts"]
    assert files[0].suffix == ".png"
    assert client.get("/api/receipts/999/photo").status_code == 404
    assert client.get("/api/receipts/999").status_code == 404
    assert (
        client.post(
            "/api/receipts", files={"photo": ("r.jpg", jpeg("x"), "image/jpeg")}, headers={"Origin": "http://evil.test"}
        ).status_code
        == 403
    )


def test_stored_path_outside_receipts_is_refused(conn: sqlite3.Connection, data_dir: Path) -> None:
    """A tampered file_path pointing outside data/receipts is never served.

    Oracle: spec-anchored — orchestrator note "Pfad nach dem Zusammenbau gegen das Datenverzeichnis prüfen".
    """
    result = service.submit_receipt(conn, data_dir, jpeg("t"), Extracted())
    conn.execute("UPDATE receipt SET file_path = '../secret.txt' WHERE id = ?", (result.receipt_id,))
    (data_dir.parent / "secret.txt").write_text("x", encoding="utf-8")
    with pytest.raises(service.NotFound):
        service.photo_file(conn, data_dir, result.receipt_id or 0)


# --- AK7 / AK8 ---------------------------------------------------------------------------------


def test_agent_runs(client: TestClient) -> None:
    """Runs are recorded, receipts name their run, the list counts receipts per status (UC 6a).

    Oracle: spec-anchored — UC-ZF-013 6a ("Ergebnis steht im Tool unter Agent-Läufe") and
    "Rückmeldung: n gebucht, n zur Prüfung, n Duplikate".
    """
    run = client.post("/api/receipts/runs", json={"note": "test run"}).json()
    photo = {"photo": ("r.jpg", jpeg("run"), "image/jpeg")}
    client.post("/api/receipts", data={"run_id": str(run["id"])}, files=photo)
    client.post("/api/receipts", data={"run_id": str(run["id"])}, files=photo)
    finished = client.put(f"/api/receipts/runs/{run['id']}", json={"counts": {"messages": 2, "failed": 0}})
    assert finished.status_code == 200
    listed = client.get("/api/receipts/runs").json()
    assert len(listed) == 1
    assert (listed[0]["review"], listed[0]["booked"], listed[0]["duplicate"]) == (1, 0, 0)
    assert listed[0]["counts"] == {"messages": 2, "failed": 0} and listed[0]["finished_at"]
    assert client.post("/api/receipts", data={"run_id": "999"}, files=photo).status_code == 200  # same photo first
    other = {"photo": ("r.jpg", jpeg("other"), "image/jpeg")}
    assert client.post("/api/receipts", data={"run_id": "999"}, files=other).status_code == 404
    assert client.put("/api/receipts/runs/999", json={}).status_code == 404


def test_settings_threshold_and_mapping(conn: sqlite3.Connection, data_dir: Path, ledger: dict[str, int]) -> None:
    """Threshold and account mapping round-trip and change the outcome; wrong-area accounts are refused.

    Oracle: spec-anchored — task (threshold setting default 0.8, payment-method mapping).
    """
    assert service.get_receipt_settings(conn).confidence_threshold == 0.8
    second_cash = accounts.create_account(
        conn, AccountFields("Test wallet 2", "private", "cash", 0, date.today() - timedelta(days=365))
    ).id
    # Two cash accounts and no mapping: no automatic choice.
    ambiguous = service.submit_receipt(conn, data_dir, jpeg("a"), readable(hints="bar"))
    assert (ambiguous.status, ambiguous.reason) == ("review", "no_account")

    value = service.set_receipt_settings(
        conn, service.ReceiptSettings(0.5, {"private": {"cash": second_cash, "card": None}})
    )
    assert value.accounts["private"] == {"cash": second_cash, "card": None}
    booked = service.submit_receipt(conn, data_dir, jpeg("b"), readable(amount_cents=77, hints="bar", confidence=0.6))
    assert booked.status == "booked"
    assert transactions.get_transaction(conn, booked.transaction_id or 0).account_id == second_cash
    with pytest.raises(service.ReceiptsError):
        service.set_receipt_settings(conn, service.ReceiptSettings(0.5, {"business": {"cash": second_cash}}))
    with pytest.raises(service.ReceiptsError):
        service.set_receipt_settings(conn, service.ReceiptSettings(1.5, {}))


def test_api_errors_localized(client: TestClient) -> None:
    """Receipt errors are translated like every other API error (German catalog).

    Oracle: spec-anchored — ARCHITECTURE.md "Internationalisation" (msgid translated per request).
    """
    response = client.get("/api/receipts/42", headers={"Accept-Language": "de"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Beleg nicht gefunden."
