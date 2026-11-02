"""UC-ZF-005 CSV import: parser, dialect, duplicate key, matching, one-transaction commit, API.

Expected values are hand-computed from the use case and the fixture files in ``tests/fixtures/csv``;
every bank, text and amount there is invented. "today" is 2026-09-19.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.imports import matching, parser, service
from zfinance.domain.imports.matching import LedgerRow, Tolerance
from zfinance.domain.imports.parser import ColumnMap, ImportFileError, ParsedLine, Profile
from zfinance.domain.imports.service import Override
from zfinance.domain.recurring import service as recurring
from zfinance.domain.recurring.service import RecurringFields
from zfinance.domain.rules import service as rules
from zfinance.domain.rules.service import RuleFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
FIXTURES = Path(__file__).parent / "fixtures" / "csv"

GERMAN = Profile(
    "Testbank Beispiel",
    ColumnMap(date="Buchungsdatum", text="Verwendungszweck", amount="Betrag", counterparty="Empfänger"),
    skip_rows=2,
)
SPLIT = Profile(
    "Test Bank Split",
    ColumnMap(date="Date", text="Description", debit="Debit", credit="Credit", counterparty="Payee"),
    date_format="%Y-%m-%d",
    decimal_sep=".",
)
SIMPLE = Profile("Test Simple", ColumnMap(date="Datum", text="Text", amount="Betrag"), delimiter="semicolon")


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _simple_csv(lines: list[tuple[str, str, str]]) -> bytes:
    """A ``Datum;Text;Betrag`` file (UTF-8) from (date, text, amount) rows."""
    return ("Datum;Text;Betrag\n" + "".join(f"{d};{t};{a}\n" for d, t, a in lines)).encode("utf-8")


# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "import.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


def _account(conn: sqlite3.Connection, name: str = "Test giro") -> int:
    fields = AccountFields(name, "private", "giro", 100_000, date(2026, 9, 1))  # type: ignore[arg-type]
    return accounts.create_account(conn, fields).id


def _category(conn: sqlite3.Connection, name: str) -> int:
    return accounts.create_category(conn, CategoryFields(name, "expense")).id  # type: ignore[arg-type]


def _profile(conn: sqlite3.Connection, profile: Profile = SIMPLE) -> int:
    return service.create_profile(conn, profile).id


def _import(
    conn: sqlite3.Connection, profile_id: int, account_id: int, data: bytes, overrides: list[Override] | None = None
) -> service.Run:
    staged = service.stage_preview(conn, profile_id, account_id, data)
    return service.commit(conn, staged.preview_id, overrides)


def _count(conn: sqlite3.Connection, where: str = "1 = 1", params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(f'SELECT count(*) FROM "transaction" WHERE {where}', params).fetchone()[0])  # noqa: S608


def _line(day: date, cents: int, text: str = "Test", line: int = 2) -> ParsedLine:
    return ParsedLine(line, day, cents, text, None)


# --- AK7: parser and dialect (pure) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "sep", "cents"),
    [
        ("-1.234,56", ",", -123_456),
        ("1234,5", ",", 123_450),
        ("+12", ",", 1_200),
        ("12,50-", ",", -1_250),
        ("1 234,56 €", ",", 123_456),
        ("EUR -0,10", ",", -10),
        ("-1,234.56", ".", -123_456),
        ("0.1", ".", 10),
        ("0.2", ".", 20),
        ("1000000.99", ".", 100_000_099),
    ],
)
def test_parse_amount_exact_cents(raw: str, sep: parser.DecimalSep, cents: int) -> None:
    """Oracle: spec-anchored — UC Schnittstellen (Dezimaltrenner , / .) + ARCHITECTURE "Money": exact integer cents."""
    assert parser.parse_amount(raw, sep) == cents


@pytest.mark.parametrize(("raw", "sep"), [("1,234", ","), ("12.345,6", "."), ("1.23,45", ","), ("abc", ","), ("", ",")])
def test_parse_amount_rejects_malformed(raw: str, sep: parser.DecimalSep) -> None:
    """Oracle: spec-anchored — SPEC Definitions "Amount": three decimals, misplaced groups and text are errors."""
    with pytest.raises(ValueError, match=r"amount|decimal"):
        parser.parse_amount(raw, sep)


def test_detect_dialect_of_fixtures() -> None:
    """Oracle: spec-anchored — UC step 1: separator and encoding recognised (cp1252 ; and UTF-8 ,)."""
    assert parser.detect_dialect(_fixture("semicolon_cp1252.csv"), skip_rows=2) == parser.Dialect("cp1252", "semicolon")
    assert parser.detect_dialect(_fixture("comma_utf8_debit_credit.csv")) == parser.Dialect("utf-8", "comma")
    assert parser.detect_dialect(b"\xef\xbb\xbfDatum;Text\n01.09.2026;x\n").encoding == "utf-8"


def test_detect_dialect_uncertain_is_refused_not_guessed() -> None:
    """Oracle: spec-anchored — task stop condition: uncertain encoding/delimiter -> profile field, never a guess."""
    undecodable = b"Datum;Text;Betrag\n01.09.2026;\x81\x8d;1,00\n"
    assert parser.detect_dialect(undecodable) == parser.Dialect(None, None)
    with pytest.raises(ImportFileError, match="encoding"):
        parser.parse_rows(SIMPLE, undecodable)
    single_column = b"Text\nabc\n"
    assert parser.detect_dialect(single_column).delimiter is None
    with pytest.raises(ImportFileError, match="separator"):
        parser.parse_rows(Profile("x", ColumnMap(date="Text", text="Text", amount="Text")), single_column)


def test_parse_rows_cp1252_semicolon_fixture() -> None:
    """Oracle: spec-anchored — fixture semicolon_cp1252.csv: umlauts intact, thousands, skip rows, file line numbers."""
    result = parser.parse_rows(GERMAN, _fixture("semicolon_cp1252.csv"))

    assert (result.encoding, result.delimiter, result.errors) == ("cp1252", "semicolon", [])
    assert [(p.line, p.date, p.amount_cents) for p in result.lines] == [
        (4, date(2026, 9, 5), -123_456),
        (5, date(2026, 9, 7), -350),
        (6, date(2026, 9, 7), -350),
        (7, date(2026, 9, 10), 250_000),
        (8, date(2026, 10, 3), -85_000),
    ]
    assert result.lines[1].text == "Kaffee Bäckerei Übermut"
    assert result.lines[1].counterparty == "Bäckerei Übermut"


def test_parse_rows_debit_credit_fixture() -> None:
    """Oracle: spec-anchored — fixture comma_utf8_debit_credit.csv: debit negative, credit positive, quoted commas."""
    result = parser.parse_rows(SPLIT, _fixture("comma_utf8_debit_credit.csv"))

    assert [(p.amount_cents, p.text) for p in result.lines] == [
        (-4_210, "Groceries, weekly"),
        (1_999, "Refund order 0001"),
        (-100_000, 'Transfer "savings"'),
    ]


def test_explicit_profile_encoding_overrides_detection() -> None:
    """Oracle: spec-anchored — UC 1a: the user chooses the encoding; the umlauts show whether it is right."""
    data = "Datum;Text;Betrag\n01.09.2026;Grüße;-1,00\n".encode("cp1252")
    as_cp1252 = parser.parse_rows(Profile("x", SIMPLE.column_map, encoding="cp1252"), data)
    assert as_cp1252.lines[0].text == "Grüße"
    with pytest.raises(ImportFileError, match="not valid utf-8"):
        parser.parse_rows(Profile("x", SIMPLE.column_map, encoding="utf-8"), data)


def test_line_errors_carry_file_line_numbers() -> None:
    """Oracle: spec-anchored — UC Fehlerverhalten: unparsable lines are listed, nothing dropped silently."""
    rows = [("01.09.2026", f"Test {i}", "-1,00") for i in range(10)]
    rows[3] = ("2026-09-01", "bad date", "-1,00")
    data = _simple_csv(rows) + b"\n;;\n"  # blank records are not data lines
    result = parser.parse_rows(SIMPLE, data)
    assert [(e.line, e.message) for e in result.errors] == [
        (5, "The date 2026-09-01 does not match the format %d.%m.%Y.")
    ]
    assert len(result.lines) == 9


def test_dedupe_key_numbers_identical_lines() -> None:
    """Oracle: spec-anchored — SPEC "Duplicate key": sha256(account|date|amount|text), n-th copy + #n."""
    day = date(2026, 9, 7)
    first = parser.dedupe_key(1, day, -350, "  Kaffee   BÄCKEREI ")
    assert first == parser.dedupe_key(1, day, -350, "kaffee bäckerei")
    assert first != parser.dedupe_key(2, day, -350, "kaffee bäckerei")
    keys = parser.assign_keys(1, [_line(day, -350, "Kaffee"), _line(day, -350, "kaffee")])
    assert keys == [parser.dedupe_key(1, day, -350, "Kaffee"), parser.dedupe_key(1, day, -350, "Kaffee", 2)]


# --- matching (pure) -----------------------------------------------------------------------------


def test_match_tolerance_bounds() -> None:
    """Oracle: spec-anchored — UC assumption / SPEC AK6: amount ±5 % of the planned amount, date ±5 days."""
    planned = LedgerRow(1, date(2026, 10, 1), -10_000, "planned")
    tolerance = Tolerance()

    def hit(day: date, cents: int) -> bool:
        return matching.match_planned(_line(day, cents), [planned], tolerance)[0] is not None

    assert hit(date(2026, 10, 1), -10_500)  # +5.00 %
    assert not hit(date(2026, 10, 1), -10_501)  # +5.01 %
    assert hit(date(2026, 10, 1), -9_500)
    assert not hit(date(2026, 10, 1), 10_000)  # opposite sign
    assert hit(date(2026, 10, 6), -10_000) and hit(date(2026, 9, 26), -10_000)  # ±5 days
    assert not hit(date(2026, 10, 7), -10_000)  # 6 days


def test_classification_order_and_claims() -> None:
    """Oracle: spec-anchored — UC 3b/3c: match before suspect; each ledger row claimed by one line only."""
    ledger = [
        LedgerRow(10, date(2026, 9, 7), -350, "booked"),  # hand entry
        LedgerRow(11, date(2026, 9, 7), -350, "booked", imported=True),  # an earlier import is no suspect
        LedgerRow(12, date(2026, 9, 30), -85_000, "planned"),
    ]
    lines = [
        _line(date(2026, 9, 8), -350, "Kaffee", 2),
        _line(date(2026, 9, 8), -350, "Kaffee", 3),
        _line(date(2026, 10, 3), -85_000, "Miete", 4),
        _line(date(2026, 10, 3), -85_000, "Miete", 5),
    ]
    keys = parser.assign_keys(1, lines)
    result = matching.classify(lines, keys, {keys[3]}, ledger, Tolerance())

    assert [(c.kind, c.suspect_id, c.planned_id) for c in result] == [
        ("duplicate_suspect", 10, None),
        ("new", None, None),
        ("match", None, 12),
        ("duplicate", None, None),
    ]


# --- AK1: idempotent ------------------------------------------------------------------------------


def test_import_is_idempotent(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK1 / SPEC AK5: the same CSV twice -> second run 0 new bookings."""
    giro, profile = _account(conn), _profile(conn, GERMAN)
    data = _fixture("semicolon_cp1252.csv")

    first = _import(conn, profile, giro, data)
    before = _count(conn)
    second = _import(conn, profile, giro, data)

    assert first.counts["new"] == 5 and before == 5
    assert (second.counts["new"], second.counts["matched"], second.counts["duplicates"]) == (0, 0, 5)
    assert _count(conn) == before


def test_identical_lines_in_one_file_are_two_bookings(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC Definitions "Duplicate key" (#n): two equal coffees on one day stay two."""
    giro, profile = _account(conn), _profile(conn, GERMAN)
    _import(conn, profile, giro, _fixture("semicolon_cp1252.csv"))
    assert _count(conn, "text = ? AND amount_cents = -350", ("Kaffee Bäckerei Übermut",)) == 2


# --- AK2: rules -----------------------------------------------------------------------------------


def test_import_applies_rules(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK2: "SVS" in the text + rule "contains SVS -> SVS" -> category SVS."""
    giro, profile = _account(conn), _profile(conn, GERMAN)
    svs = _category(conn, "SVS")
    rules.create_rule(conn, RuleFields("contains", "SVS", svs))

    staged = service.stage_preview(conn, profile, giro, _fixture("semicolon_cp1252.csv"))
    line = next(line for line in staged.lines if "SVS" in line.text)
    assert (line.category_id, line.category_source) == (svs, "rule")
    assert staged.counts["rule_hits"] == 1

    service.commit(conn, staged.preview_id)
    assert _count(conn, "text LIKE 'SVS%' AND category_id = ?", (svs,)) == 1


# --- AK3: planned items ---------------------------------------------------------------------------


def test_import_matches_planned_transaction(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK3: -850 on 03.10. replaces planned rent -850 on 01.10., no second booking."""
    giro, profile = _account(conn), _profile(conn, GERMAN)
    rent = _category(conn, "Test rent")
    item = recurring.create_recurring(
        conn,
        RecurringFields(
            description="Miete",
            amount_cents=-85_000,
            account_id=giro,
            category_id=rent,
            interval="monthly",
            start_date=date(2026, 10, 1),
            day_of_month=1,
        ),
        today=TODAY,
    )
    planned_id = conn.execute(
        "SELECT id FROM \"transaction\" WHERE recurring_id = ? AND due_date = '2026-10-01'", (item.id,)
    ).fetchone()[0]

    staged = service.stage_preview(conn, profile, giro, _fixture("semicolon_cp1252.csv"))
    line = next(line for line in staged.lines if line.amount_cents == -85_000)
    assert (line.kind, line.planned_id, line.review, line.category_source) == ("match", planned_id, False, "planned")
    run = service.commit(conn, staged.preview_id)

    october = conn.execute(
        'SELECT id, date, status, source, due_date, category_id FROM "transaction"'
        " WHERE recurring_id = ? AND date BETWEEN '2026-10-01' AND '2026-10-31'",
        (item.id,),
    ).fetchall()
    assert [tuple(row)[1:] for row in october] == [("2026-10-03", "booked", "import", "2026-10-01", rent)]
    assert _count(conn, "id = ?", (planned_id,)) == 0
    assert run.counts["matched"] == 1
    assert recurring.generate_planned(conn, today=TODAY).created == 0  # the due date counts as done


def test_ambiguous_match_nearest_date_and_override(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-005 3b: two fitting items -> nearer date, flagged; the user can re-hang or drop."""
    giro, profile = _account(conn), _profile(conn)

    def expected(day: date) -> int:
        return recurring.create_expected(conn, BookingFields(day, -5_000, giro, None, "Test insurance"), today=TODAY).id

    near, far = expected(date(2026, 10, 2)), expected(date(2026, 10, 6))
    data = _simple_csv([("03.10.2026", "Versicherung", "-50,00")])

    staged = service.stage_preview(conn, profile, giro, data)
    line = staged.lines[0]
    assert (line.kind, line.planned_id, line.review, line.candidates) == ("match", near, True, (near, far))

    service.commit(conn, staged.preview_id, [Override(line=2, set_match=True, planned_id=far)])
    assert _count(conn, "id = ?", (far,)) == 0 and _count(conn, "id = ?", (near,)) == 1

    other = _simple_csv([("03.10.2026", "Versicherung 2", "-50,00")])
    run = _import(conn, profile, giro, other, [Override(line=2, set_match=True, planned_id=None)])
    assert (run.counts["new"], run.counts["matched"]) == (1, 0)
    assert _count(conn, "id = ?", (near,)) == 1  # the match was removed, the item stays planned


def test_override_to_foreign_planned_item_is_refused(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC Contract-Sketch Pre: an override planned_id must be one of the line's candidates."""
    giro, profile = _account(conn), _profile(conn)
    far_away = recurring.create_expected(conn, BookingFields(date(2026, 12, 1), -5_000, giro), today=TODAY).id
    staged = service.stage_preview(conn, profile, giro, _simple_csv([("03.10.2026", "x", "-50,00")]))
    with pytest.raises(service.Conflict):
        service.commit(conn, staged.preview_id, [Override(line=2, set_match=True, planned_id=far_away)])
    assert _count(conn, "source = 'import'") == 0


# --- AK3c: duplicate suspects ----------------------------------------------------------------------


def test_manual_booking_is_duplicate_suspect(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-005 3c: equal hand entry (amount exact, ±3 days) -> suspect, not replaced."""
    giro, profile = _account(conn), _profile(conn, GERMAN)
    manual = transactions.create_transaction(
        conn, BookingFields(date(2026, 9, 5), -350, giro, None, "Kaffee"), today=TODAY
    )

    staged = service.stage_preview(conn, profile, giro, _fixture("semicolon_cp1252.csv"))
    coffees = [line for line in staged.lines if line.amount_cents == -350]
    assert [(c.kind, c.suspect_id) for c in coffees] == [("duplicate_suspect", manual.id), ("new", None)]

    run = service.commit(conn, staged.preview_id)
    assert (run.counts["suspects"], run.counts["new"]) == (1, 4)
    assert _count(conn, "amount_cents = -350") == 2  # the hand entry and one imported coffee
    assert transactions.get_transaction(conn, manual.id).source == "manual"


def test_suspect_imported_on_override(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC Definitions "Overrides": skip false imports a suspect."""
    giro, profile = _account(conn), _profile(conn)
    transactions.create_transaction(conn, BookingFields(date(2026, 9, 5), -350, giro, None, "Kaffee"), today=TODAY)
    run = _import(conn, profile, giro, _simple_csv([("06.09.2026", "Kaffee", "-3,50")]), [Override(line=2, skip=False)])
    assert (run.counts["new"], run.counts["suspects"]) == (1, 1)
    assert _count(conn, "amount_cents = -350") == 2


def test_transfer_leg_booked_by_replace_is_duplicate_suspect(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — orchestrator trap: the leg replace_planned booked is a suspect in the target CSV."""
    giro, savings = _account(conn), _account(conn, "Test savings")
    profile = _profile(conn)
    recurring.create_recurring(
        conn,
        RecurringFields(
            description="Sparrate",
            amount_cents=20_000,
            account_id=giro,
            transfer_target_account_id=savings,
            interval="monthly",
            start_date=date(2026, 10, 1),
            day_of_month=1,
        ),
        today=TODAY,
    )
    giro_run = _import(conn, profile, giro, _simple_csv([("02.10.2026", "Sparrate", "-200,00")]))
    incoming = conn.execute(
        "SELECT id, status, date, amount_cents FROM \"transaction\" WHERE account_id = ? AND date < '2026-11-01'",
        (savings,),
    ).fetchone()
    assert giro_run.counts["matched"] == 1
    assert tuple(incoming)[1:] == ("booked", "2026-10-02", 20_000)

    staged = service.stage_preview(conn, profile, savings, _simple_csv([("03.10.2026", "Sparrate", "200,00")]))
    assert (staged.lines[0].kind, staged.lines[0].suspect_id) == ("duplicate_suspect", incoming["id"])
    service.commit(conn, staged.preview_id)
    assert _count(conn, "account_id = ? AND date < '2026-11-01'", (savings,)) == 1


# --- AK4: refusals ---------------------------------------------------------------------------------


def test_missing_required_column_refuses_file(conn: sqlite3.Connection, client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK4: amount column missing -> whole file refused naming it, no booking."""
    giro, profile = _account(conn), _profile(conn, replace(GERMAN, skip_rows=0))
    with pytest.raises(ImportFileError, match="Betrag"):
        service.stage_preview(conn, profile, giro, _fixture("missing_amount.csv"))
    assert _count(conn) == 0
    assert service.list_runs(conn) == []

    account = client.post("/api/accounts", json=_account_json()).json()["id"]
    created = client.post("/api/imports/profiles", json=_profile_json(skip_rows=0)).json()["id"]
    response = client.post(
        f"/api/imports/preview?profile_id={created}&account_id={account}",
        content=_fixture("missing_amount.csv"),
        headers={"Content-Type": "text/csv", "Accept-Language": "de"},
    )
    assert response.status_code == 400
    assert "Betrag" in response.json()["detail"]
    assert client.get("/api/transactions").json() == []


def test_error_threshold(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC Fehlerverhalten: >10 % unparsable refuses the file; up to 10 % listed and skipped."""
    giro, profile = _account(conn), _profile(conn)
    rows = [("01.09.2026", f"Test {i}", "-1,00") for i in range(20)]
    rows[4] = rows[9] = ("01.09.2026", "bad", "x")  # 2 of 20 = 10 %
    run = _import(conn, profile, giro, _simple_csv(rows))
    assert (run.counts["new"], run.counts["errors"]) == (18, 2)
    assert [error.line for error in run.errors] == [6, 11]

    rows[14] = ("01.09.2026", "bad", "x")  # 3 of 20 = 15 %
    with pytest.raises(ImportFileError, match="more than 10 percent"):
        service.stage_preview(conn, profile, giro, _simple_csv(rows))


def test_line_before_opening_date_is_a_listed_error(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-001 1a (no booking before the opening date) + UC Fehlerverhalten (listed)."""
    giro, profile = _account(conn), _profile(conn)
    staged = service.stage_preview(
        conn, profile, giro, _simple_csv([("31.08.2026", "old", "-1,00"), ("01.09.2026", "ok", "-1,00")])
    )
    assert [(e.line, "opening date" in e.message) for e in staged.errors] == [(2, True)]
    assert [line.line for line in staged.lines] == [3]


def test_profile_change_keeps_old_imports(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-005 2a: a changed profile names the missing column; old imports stay untouched."""
    giro, profile = _account(conn), _profile(conn)
    run = _import(conn, profile, giro, _simple_csv([("01.09.2026", "Test", "-1,00")]))
    rows_before = conn.execute('SELECT * FROM "transaction"').fetchall()

    changed = Profile("Test Simple", ColumnMap(date="Datum", text="Text", amount="Umsatz"), delimiter="semicolon")
    service.update_profile(conn, profile, changed)

    assert [tuple(r) for r in conn.execute('SELECT * FROM "transaction"')] == [tuple(r) for r in rows_before]
    assert service.get_run(conn, run.id) == run
    with pytest.raises(ImportFileError, match="Umsatz"):
        service.stage_preview(conn, profile, giro, _simple_csv([("02.09.2026", "Test", "-1,00")]))


# --- AK5: report ------------------------------------------------------------------------------------


def test_report_counts_match_bookings(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK5 + 3a: new/duplicates/matched/uncategorised equal the run's bookings."""
    giro, profile = _account(conn), _profile(conn)
    food = _category(conn, "Test food")
    rules.create_rule(conn, RuleFields("contains", "market", food))
    planned = recurring.create_expected(conn, BookingFields(date(2026, 10, 1), -2_000, giro, None, "Abo"), today=TODAY)
    transactions.create_transaction(conn, BookingFields(date(2026, 9, 10), -777, giro, None, "Hand"), today=TODAY)
    _import(conn, profile, giro, _simple_csv([("02.09.2026", "Old line", "-1,00")]))
    data = _simple_csv(
        [
            ("02.09.2026", "Old line", "-1,00"),  # duplicate
            ("03.09.2026", "Test Market", "-12,00"),  # new, rule hit
            ("04.09.2026", "No rule here", "-5,00"),  # new, uncategorised (3a)
            ("01.10.2026", "Abo", "-20,00"),  # match
            ("11.09.2026", "Hand", "-7,77"),  # suspect
            ("12.09.2026", "Skip me", "-9,00"),  # skipped by the user
            ("xx", "broken", "-1,00"),  # error
            *[(f"{day:02d}.09.2026", f"Filler {day}", "-1,00") for day in range(13, 23)],  # 10 new, uncategorised
        ]
    )
    run = _import(conn, profile, giro, data, [Override(line=7, skip=True)])

    in_run = "import_run_id = ?"
    assert run.counts == {
        "lines": 17,
        "new": 12,
        "matched": 1,
        "duplicates": 1,
        "suspects": 1,
        "skipped": 2,  # the user's skip and the suspect
        "errors": 1,
        "uncategorized": 12,
    }
    assert _count(conn, in_run, (run.id,)) == run.counts["new"] + run.counts["matched"]
    assert _count(conn, in_run + " AND category_id IS NULL", (run.id,)) == run.counts["uncategorized"]
    assert _count(conn, in_run + " AND text = 'No rule here' AND category_id IS NULL", (run.id,)) == 1
    assert _count(conn, in_run + " AND category_id = ?", (run.id, food)) == 1
    assert _count(conn, "id = ?", (planned.id,)) == 0
    assert [e.line for e in run.errors] == [8]


# --- AK6: all or nothing -----------------------------------------------------------------------------


def test_error_in_line_40_writes_nothing(conn: sqlite3.Connection, client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK6/6a: an error in line 40 of 50 writes none of the 50 lines."""
    giro, profile = _account(conn), _profile(conn)
    data = _simple_csv([("01.09.2026", f"Test {i}", "-1,00") for i in range(1, 51)])
    staged = service.stage_preview(conn, profile, giro, data)
    fortieth = staged.lines[39].line  # header is file line 1, so the 40th data line is file line 41

    with pytest.raises(service.LineWriteError) as failure:
        service.commit(conn, staged.preview_id, [Override(line=fortieth, category_id=999_999)])

    assert (fortieth, failure.value.line) == (41, 41)
    assert _count(conn) == 0
    assert service.list_runs(conn) == []

    account = client.post("/api/accounts", json=_account_json()).json()["id"]
    created = client.post("/api/imports/profiles", json=_simple_profile_json()).json()["id"]
    preview = _post_csv(client, f"/api/imports/preview?profile_id={created}&account_id={account}", data).json()
    response = client.post(
        "/api/imports/commit",
        json={"preview_id": preview["preview_id"], "overrides": [{"line": 41, "category_id": 999_999}]},
    )
    assert response.status_code == 404
    assert "Line 41" in response.json()["detail"] and "nothing was written" in response.json()["detail"]
    assert client.get("/api/transactions").json() == []
    assert client.get("/api/imports/runs").json() == []


def test_commit_syncs_debts_once_after_writing(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle: spec-anchored — contract of -013: sync_debts once after a commit, never after a failed one."""
    calls: list[int] = []

    def fake_sync(conn: sqlite3.Connection, *, today: date | None = None) -> list[int]:
        calls.append(1)
        return []

    monkeypatch.setattr(service.debts, "sync_debts", fake_sync)
    giro, profile = _account(conn), _profile(conn)
    data = _simple_csv([("01.09.2026", "Test", "-1,00"), ("02.09.2026", "Test 2", "-2,00")])
    _import(conn, profile, giro, data)
    assert calls == [1]

    staged = service.stage_preview(conn, profile, giro, _simple_csv([("03.09.2026", "Test 3", "-3,00")]))
    with pytest.raises(service.LineWriteError):
        service.commit(conn, staged.preview_id, [Override(line=2, category_id=999_999)])
    assert calls == [1]


def _interpose_after_overrides(monkeypatch: pytest.MonkeyPatch, interloper: Callable[[], object]) -> list[bool]:
    """Run ``interloper`` once, right after ``commit`` applied the overrides to its fresh preview.

    That is the gap between the duplicate check (the preview) and ``BEGIN IMMEDIATE`` that a second
    "Import" click on its own connection hits; the hook makes it deterministic.
    """
    real = service._apply_overrides
    fired: list[bool] = []

    def checked(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        if not fired:
            fired.append(True)
            interloper()
        return result

    monkeypatch.setattr(service, "_apply_overrides", checked)
    return fired


def test_concurrent_commit_of_one_preview_is_a_conflict(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oracle: spec-anchored — UC-ZF-005 AK1 (a line is imported once) and task T-2026-HAR-9130-005:
    a second commit of the same preview that ran its duplicate check before the first one committed
    raises Conflict (HTTP 409), not an IntegrityError (HTTP 500); the two lines are booked once."""
    giro, profile = _account(conn), _profile(conn)
    data = _simple_csv([("01.09.2026", "Test", "-1,00"), ("02.09.2026", "Test 2", "-2,00")])
    staged = service.stage_preview(conn, profile, giro, data)
    other = connect(tmp_path / "import.sqlite3")
    fired = _interpose_after_overrides(monkeypatch, lambda: service.commit(other, staged.preview_id))

    with pytest.raises(service.Conflict):
        service.commit(conn, staged.preview_id)
    other.close()

    assert fired == [True]
    assert _count(conn) == 2
    assert int(conn.execute("SELECT count(*) FROM import_run").fetchone()[0]) == 1


# --- AK8: upload limits --------------------------------------------------------------------------------


def test_upload_limits(client: TestClient) -> None:
    """Oracle: spec-anchored — orchestrator note: file size and line count bounded; form uploads refused."""
    account = client.post("/api/accounts", json=_account_json()).json()["id"]
    profile = client.post("/api/imports/profiles", json=_simple_profile_json()).json()["id"]
    url = f"/api/imports/preview?profile_id={profile}&account_id={account}"

    too_big = b"Datum;Text;Betrag\n" + b"x" * parser.MAX_FILE_BYTES
    assert _post_csv(client, url, too_big).status_code == 413
    too_long = _simple_csv([("01.09.2026", "Test", "-1,00")] * (parser.MAX_LINES + 1))
    response = _post_csv(client, url, too_long)
    assert response.status_code == 400 and str(parser.MAX_LINES) in response.json()["detail"]
    form = client.post(url, files={"file": ("a.csv", b"Datum;Text;Betrag\n")})
    assert form.status_code == 415


# --- AK9: API ------------------------------------------------------------------------------------------


def test_api_profile_crud_and_validation(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC Definitions "Profile": column map, date format, unique bank name."""
    created = client.post("/api/imports/profiles", json=_profile_json())
    assert created.status_code == 201
    profile = created.json()
    assert profile["column_map"]["amount"] == "Betrag" and profile["skip_rows"] == 2
    assert client.post("/api/imports/profiles", json=_profile_json(bank_name="testbank beispiel")).status_code == 409

    both = _profile_json(bank_name="B")
    both["column_map"]["debit"] = "Soll"
    assert client.post("/api/imports/profiles", json=both).status_code == 400
    assert (
        client.post("/api/imports/profiles", json=_profile_json(bank_name="C", date_format="%H:%M")).status_code == 400
    )

    changed = _profile_json(delimiter="semicolon")
    assert client.put(f"/api/imports/profiles/{profile['id']}", json=changed).json()["delimiter"] == "semicolon"
    assert len(client.get("/api/imports/profiles").json()) == 1
    assert client.delete(f"/api/imports/profiles/{profile['id']}").status_code == 204
    assert client.get(f"/api/imports/profiles/{profile['id']}").status_code == 404


def test_api_detect_preview_commit_runs(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-005 main scenario steps 1-6 over HTTP (curl-style raw body)."""
    account = client.post("/api/accounts", json=_account_json()).json()["id"]
    data = _fixture("semicolon_cp1252.csv")

    detected = _post_csv(client, "/api/imports/detect?skip_rows=2", data).json()
    assert (detected["encoding"], detected["delimiter"]) == ("cp1252", "semicolon")
    assert detected["header"] == ["Buchungsdatum", "Verwendungszweck", "Empfänger", "Betrag"]
    assert len(detected["rows"]) == 5

    profile = client.post("/api/imports/profiles", json=_profile_json()).json()["id"]
    preview = _post_csv(
        client, f"/api/imports/preview?profile_id={profile}&account_id={account}&file_name=test.csv", data
    ).json()
    assert preview["counts"]["new"] == 5 and preview["lines"][0]["kind"] == "new"

    run = client.post("/api/imports/commit", json={"preview_id": preview["preview_id"]})
    assert run.status_code == 201
    assert run.json()["counts"]["new"] == 5 and run.json()["file_name"] == "test.csv"
    assert client.get(f"/api/imports/runs/{run.json()['id']}").json() == run.json()
    assert [r["id"] for r in client.get(f"/api/imports/runs?account_id={account}").json()] == [run.json()["id"]]
    assert client.post("/api/imports/commit", json={"preview_id": preview["preview_id"]}).status_code == 404


def test_api_tolerance_settings(client: TestClient) -> None:
    """Oracle: spec-anchored — UC assumption: match tolerance ±5 %/±5 days, changeable as a setting."""
    assert client.get("/api/imports/settings").json() == {"amount_pct": 5, "date_days": 5}
    assert client.put("/api/imports/settings", json={"amount_pct": 3, "date_days": 2}).json() == {
        "amount_pct": 3,
        "date_days": 2,
    }
    assert client.put("/api/imports/settings", json={"amount_pct": 90, "date_days": 2}).status_code == 422


def test_api_archived_account_refused(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-001 1b via ensure_bookable: nothing can be booked on an archived account."""
    account = client.post("/api/accounts", json=_account_json()).json()["id"]
    client.post(f"/api/accounts/{account}/archive")
    profile = client.post("/api/imports/profiles", json=_simple_profile_json()).json()["id"]
    response = _post_csv(
        client,
        f"/api/imports/preview?profile_id={profile}&account_id={account}",
        _simple_csv([("01.09.2026", "x", "-1")]),
    )
    assert response.status_code == 409


# --- helpers for HTTP tests -----------------------------------------------------------------------------


def _account_json() -> dict[str, object]:
    return {
        "name": "Test giro",
        "area": "private",
        "type": "giro",
        "opening_balance_cents": 0,
        "opening_date": "2026-09-01",
    }


def _profile_json(**overrides: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        "bank_name": "Testbank Beispiel",
        "column_map": {
            "date": "Buchungsdatum",
            "text": "Verwendungszweck",
            "amount": "Betrag",
            "counterparty": "Empfänger",
        },
        "skip_rows": 2,
    }
    body.update(overrides)
    return body


def _simple_profile_json() -> dict[str, object]:
    return {"bank_name": "Test Simple", "column_map": {"date": "Datum", "text": "Text", "amount": "Betrag"}}


def _post_csv(client: TestClient, url: str, data: bytes) -> httpx.Response:
    return client.post(url, content=data, headers={"Content-Type": "text/csv"})
