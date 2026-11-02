"""UC-ZF-007 monthly report: figures per area, movements, comparison, boundaries, drilldown, CSV.

Expected values are hand-computed from the use case and the definitions in
docs/specs/SPEC-T-2026-CU-9130-010.md; every value is synthetic.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.reports import export, service
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransferFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "reports.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


def _account(conn: sqlite3.Connection, name: str, area: str = "private") -> int:
    fields = AccountFields(name, area, "giro", 0, date(2024, 1, 1))  # type: ignore[arg-type]
    return accounts.create_account(conn, fields).id


def _category(conn: sqlite3.Connection, name: str, kind: str = "expense", parent_id: int | None = None) -> int:
    return accounts.create_category(conn, CategoryFields(name, kind, None, parent_id)).id  # type: ignore[arg-type]


def _book(conn: sqlite3.Connection, account_id: int, on: str, cents: int, category_id: int | None = None, **more: str):
    fields = BookingFields(date.fromisoformat(on), cents, account_id, category_id, **more)  # type: ignore[arg-type]
    return transactions.create_transaction(conn, fields, today=TODAY)


def _set_status(conn: sqlite3.Connection, transaction_id: int, status: str) -> None:
    conn.execute('UPDATE "transaction" SET status = ? WHERE id = ?', (status, transaction_id))


def _lines(report: service.MonthReport) -> dict[tuple[str, str | None], service.Line]:
    return {(line.flow, line.category_name): line for line in report.lines}


# --- AK1, AK1b, AK2: area separation and figures -------------------------------------------------


@pytest.fixture
def two_areas(conn: sqlite3.Connection) -> dict[str, int]:
    """August 2026: private -300.00 groceries, -200.00 rent; business +5000.00 fees, -120.00 software."""
    private = _account(conn, "Test private")
    business = _account(conn, "Test business", "business")
    ids = {
        "private": private,
        "business": business,
        "groceries": _category(conn, "Test groceries"),
        "rent": _category(conn, "Test rent"),
        "fees": _category(conn, "Test fees", "income"),
        "software": _category(conn, "Test software"),
    }
    _book(conn, private, "2026-08-05", -30_000, ids["groceries"])
    _book(conn, private, "2026-08-06", -20_000, ids["rent"])
    _book(conn, business, "2026-08-10", 500_000, ids["fees"])
    _book(conn, business, "2026-08-11", -12_000, ids["software"])
    return ids


def test_area_separation(conn: sqlite3.Connection, two_areas: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-007 AK1: business shows no private booking and vice versa (hand-summed)."""
    private = service.month_report(conn, "2026-08", "private")
    business = service.month_report(conn, "2026-08", "business")

    assert set(_lines(private)) == {("expense", "Test groceries"), ("expense", "Test rent")}
    assert set(_lines(business)) == {("income", "Test fees"), ("expense", "Test software")}
    assert (private.income.amount_cents, private.expense.amount_cents) == (0, -50_000)
    assert (business.income.amount_cents, business.expense.amount_cents) == (500_000, -12_000)


def test_area_all_is_the_sum_of_both(conn: sqlite3.Connection, two_areas: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-007 step 1: "gesamt" = private + business (hand-summed)."""
    everything = service.month_report(conn, "2026-08", "all")

    assert (everything.income.amount_cents, everything.expense.amount_cents) == (500_000, -62_000)
    assert len(everything.lines) == 4
    assert sum(line.comparison.amount_cents for line in everything.lines) == 438_000


def test_income_expense_balance(conn: sqlite3.Connection, two_areas: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-007 step 2: income, expense, balance; balance = income + expense."""
    business = service.month_report(conn, "2026-08", "business")
    assert business.balance.amount_cents == 488_000
    assert business.balance.amount_cents == business.income.amount_cents + business.expense.amount_cents

    empty = service.month_report(conn, "2026-01", "all")
    assert (empty.income.amount_cents, empty.expense.amount_cents, empty.balance.amount_cents) == (0, 0, 0)
    assert empty.lines == []


# --- AK3: owner draw ------------------------------------------------------------------------------


def test_owner_draw_is_neutral_in_total_and_a_movement_per_area(
    conn: sqlite3.Connection, two_areas: dict[str, int]
) -> None:
    """Oracle: spec-anchored — UC-ZF-007 AK3/2a: a 1,000.00 owner draw is neither income nor expense in "all"."""
    owner = _category(conn, "Test owner draw", "transfer")
    savings = _account(conn, "Test savings")
    transactions.create_transfer(
        conn,
        TransferFields(date(2026, 8, 15), 100_000, two_areas["business"], two_areas["private"], owner, "Owner draw"),
        today=TODAY,
    )
    # A transfer inside one area is no movement anywhere.
    transactions.create_transfer(
        conn,
        TransferFields(date(2026, 8, 16), 7_000, two_areas["private"], savings, None, "To savings"),
        today=TODAY,
    )

    everything = service.month_report(conn, "2026-08", "all")
    private = service.month_report(conn, "2026-08", "private")
    business = service.month_report(conn, "2026-08", "business")

    assert (everything.income.amount_cents, everything.expense.amount_cents) == (500_000, -62_000)
    assert (everything.movement_in_cents, everything.movement_out_cents) == (0, 0)
    assert (private.income.amount_cents, private.expense.amount_cents) == (0, -50_000)
    assert (private.movement_in_cents, private.movement_out_cents) == (100_000, 0)
    assert (business.income.amount_cents, business.expense.amount_cents) == (500_000, -12_000)
    assert (business.movement_in_cents, business.movement_out_cents) == (0, -100_000)
    assert private.balance.amount_cents == -50_000  # the movement does not change the balance
    assert all(line.category_name != "Test owner draw" for line in everything.lines)


# --- AK4, AK8: drilldown and the uncategorised list ----------------------------------------------


def test_drilldown_sums_to_line(conn: sqlite3.Connection, two_areas: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-007 AK4: the bookings behind a line sum to the line's amount."""
    private, groceries = two_areas["private"], two_areas["groceries"]
    _book(conn, private, "2026-08-20", -1_050, groceries)
    _book(conn, private, "2026-08-21", 400, groceries)  # a refund: income line of the same category
    _book(conn, private, "2026-08-22", -777)  # uncategorised
    _book(conn, private, "2026-08-23", 123)  # uncategorised income

    report = service.month_report(conn, "2026-08", "all")
    lines = _lines(report)
    assert ("income", "Test groceries") in lines and ("expense", "Test groceries") in lines
    assert ("expense", None) in lines and ("income", None) in lines
    for line in report.lines:
        items = service.bookings(conn, "2026-08", "all", category_id=line.category_id, flow=line.flow)
        assert sum(item.amount_cents for item in items) == line.comparison.amount_cents
        assert len(items) == line.booking_count
    assert lines[("expense", "Test groceries")].comparison.amount_cents == -31_050
    assert lines[("income", "Test groceries")].comparison.amount_cents == 400


def test_uncategorized_list(conn: sqlite3.Connection, two_areas: dict[str, int]) -> None:
    """Oracle: spec-anchored — UC-ZF-007 step 3: booked, non-transfer, category-less; assigning removes."""
    private, business = two_areas["private"], two_areas["business"]
    first = _book(conn, private, "2026-08-12", -500, text="Test kiosk")
    _book(conn, business, "2026-08-13", -900, text="Test tool")
    _book(conn, private, "2026-07-31", -100, text="Test other month")
    _book(conn, private, "2026-08-14", -300, two_areas["rent"], text="Test categorised")
    planned = _book(conn, private, "2026-08-15", -50, text="Test planned")
    _set_status(conn, planned.id, "planned")
    transactions.create_transfer(
        conn, TransferFields(date(2026, 8, 16), 1_000, private, business, None, "Test transfer"), today=TODAY
    )

    listed = service.bookings(conn, "2026-08", "private", category_id=None)
    assert [item.text for item in listed] == ["Test kiosk"]
    assert service.month_report(conn, "2026-08", "private").uncategorized_count == 1
    assert service.month_report(conn, "2026-08", "all").uncategorized_count == 2

    transactions.update_transaction(
        conn,
        first.id,
        BookingFields(first.date, first.amount_cents, private, two_areas["groceries"], first.text),
        today=TODAY,
    )
    assert service.bookings(conn, "2026-08", "private", category_id=None) == []
    assert service.month_report(conn, "2026-08", "private").uncategorized_count == 0


# --- AK5, AK6, AK7: comparison, boundaries, booked only ------------------------------------------


def test_comparison_previous_and_six_month_average(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — UC-ZF-007 step 2: previous month and 6-month average (hand-computed below)."""
    giro = _account(conn, "Test giro")
    groceries = _category(conn, "Test groceries")
    tiny = _category(conn, "Test tiny")
    old = _category(conn, "Test old")
    _book(conn, giro, "2026-09-03", -1_000, groceries)  # selected month
    _book(conn, giro, "2026-08-03", -600, groceries)  # previous month
    _book(conn, giro, "2026-03-03", -300, groceries)  # oldest month of the window
    _book(conn, giro, "2026-06-03", -100, groceries)
    _book(conn, giro, "2026-02-28", -9_999, groceries)  # one day outside the window: ignored
    _book(conn, giro, "2026-03-05", -3, tiny)  # -3 / 6 = -0.5 -> -1 (half away from zero)
    _book(conn, giro, "2026-03-06", -900, old)  # only in the past: line with 0 this month

    report = service.month_report(conn, "2026-09", "all")
    lines = _lines(report)

    # groceries: (-300 - 100 - 600) = -1000 / 6 = -166.67 -> -167
    assert lines[("expense", "Test groceries")].comparison == service.Comparison(-1_000, -600, -167)
    assert lines[("expense", "Test tiny")].comparison == service.Comparison(0, 0, -1)
    assert lines[("expense", "Test old")].comparison == service.Comparison(0, 0, -150)
    assert lines[("expense", "Test old")].booking_count == 0
    # Totals come from raw sums: (-1000 - 3 - 900) = -1903 / 6 = -317.17 -> -317 (rounded line averages: -318).
    assert report.expense == service.Comparison(-1_000, -600, -317)
    assert report.balance == report.expense
    assert report.income == service.Comparison(0, 0, 0)


def test_month_boundaries_and_year_rollover(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — calendar months: 28/29-day February, day 1 in its month, January follows December."""
    giro = _account(conn, "Test giro")
    for on, cents in (
        ("2026-01-31", -1),
        ("2026-02-01", -10),
        ("2026-02-28", -100),
        ("2026-03-01", -1_000),
        ("2024-02-29", -7),
        ("2024-03-01", -70),
    ):
        _book(conn, giro, on, cents)

    assert service.month_report(conn, "2026-02", "all").expense.amount_cents == -110
    assert service.month_report(conn, "2026-03", "all").expense.amount_cents == -1_000
    assert service.month_report(conn, "2024-02", "all").expense.amount_cents == -7
    assert [b.amount_cents for b in service.bookings(conn, "2026-02", "all", category_id=None)] == [-10, -100]

    _book(conn, giro, "2025-12-31", -700)
    _book(conn, giro, "2025-07-01", -600)  # oldest day of the January window (Jul-Dec 2025)
    january = service.month_report(conn, "2026-01", "all")
    # previous month = December 2025; average = (-700 - 600) / 6 = -216.67 -> -217
    assert january.expense == service.Comparison(-1, -700, -217)


def test_only_booked_counts(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — orchestrator note for -010: planned and skipped never count, anywhere."""
    giro = _account(conn, "Test giro")
    food = _category(conn, "Test food")
    kept = _book(conn, giro, "2026-08-10", -1_000, food)
    for on, text in (("2026-08-11", "planned now"), ("2026-07-11", "planned before"), ("2026-05-11", "skipped early")):
        row = _book(conn, giro, on, -5_000, food if "now" not in text else None, text=text)
        _set_status(conn, row.id, "skipped" if "skipped" in text else "planned")

    report = service.month_report(conn, "2026-08", "all")

    assert report.expense == service.Comparison(-1_000, 0, 0)
    assert report.uncategorized_count == 0
    assert [item.id for item in service.bookings(conn, "2026-08", "all", category_id=food)] == [kept.id]
    assert service.bookings(conn, "2026-08", "all", category_id=None) == []


def test_parse_month_refuses_bad_input() -> None:
    """Oracle: spec-anchored — API contract in the SPEC: month is YYYY-MM."""
    for bad in ("2026-13", "2026-00", "26-08", "2026-8", "2026/08", "", "abcd-ef", "2026-08-01"):
        with pytest.raises(service.ReportsError):
            service.parse_month(bad)
    assert service.parse_month("2026-08") == (2026, 8)


# --- AK9, AK9b: CSV -------------------------------------------------------------------------------


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_csv_export_content(conn: sqlite3.Connection, two_areas: dict[str, int]) -> None:
    """Oracle: spec-anchored — SPEC AK9 columns: cent-exact amounts with ".", area honoured, totals present."""
    _book(conn, two_areas["business"], "2026-08-12", -1_230, two_areas["software"])
    text = export.month_csv(service.month_report(conn, "2026-08", "business"))

    assert not text.startswith("﻿")
    assert text.splitlines()[0] == (
        "month,area,row_type,category_id,category,parent_category,amount,previous_month,average_6_months,bookings"
    )
    rows = _rows(text)
    by_type = {(row["row_type"], row["category"]): row for row in rows}
    assert by_type[("expense", "Test software")]["amount"] == "-132.30"  # -120.00 + -12.30
    assert by_type[("expense", "Test software")]["bookings"] == "2"
    assert by_type[("income", "Test fees")]["amount"] == "5000.00"
    assert by_type[("total_income", "")]["amount"] == "5000.00"
    assert by_type[("total_expense", "")]["amount"] == "-132.30"
    assert by_type[("balance", "")]["amount"] == "4867.70"
    assert {row["area"] for row in rows} == {"business"}
    assert "Test groceries" not in text  # a private category


def test_csv_quotes_delimiters_and_stays_cent_exact(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC AK9: values with a comma or quote are quoted, so the file round-trips."""
    giro = _account(conn, "Test giro")
    awkward = _category(conn, 'Test "quoted", with comma\nand newline')
    _book(conn, giro, "2026-08-01", -5, awkward)

    rows = _rows(export.month_csv(service.month_report(conn, "2026-08", "all")))

    line = next(row for row in rows if row["row_type"] == "expense")
    assert line["category"] == 'Test "quoted", with comma\nand newline'
    assert line["amount"] == "-0.05"


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_csv_text_cells_are_safe_amounts_are_untouched(conn: sqlite3.Connection, prefix: str) -> None:
    """Oracle: spec-anchored — SPEC AK9b: formula prefixes get a leading quote; negative amounts stay numeric."""
    giro = _account(conn, "Test giro")
    dangerous = _category(conn, f"{prefix}SUM(A1)")
    _book(conn, giro, "2026-08-01", -1_230, dangerous)

    line = next(
        row
        for row in _rows(export.month_csv(service.month_report(conn, "2026-08", "all")))
        if row["row_type"] == "expense"
    )

    assert line["category"] == f"'{prefix}SUM(A1)"
    assert line["amount"] == "-12.30"


def test_safe_text_covers_tab_and_carriage_return() -> None:
    """Oracle: spec-anchored - SPEC AK9b: tab and CR are formula prefixes too (names cannot start with them)."""
    assert export.safe_text("\t=1") == "'\t=1"
    assert export.safe_text("\r=1") == "'\r=1"
    assert export.safe_text("Test plain") == "Test plain"
    assert export.safe_text(None) == ""


# --- HTTP -----------------------------------------------------------------------------------------


def test_report_endpoints(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC API: /month, /month/bookings, /month/export.csv, localized month error."""
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test giro",
            "area": "private",
            "type": "giro",
            "opening_balance_cents": 0,
            "opening_date": "2025-01-01",
            "reserve_floor_cents": 0,
        },
    ).json()
    category = client.post(
        "/api/categories", json={"name": "Test food", "kind": "expense", "area": None, "parent_id": None}
    ).json()
    for cents, category_id in ((-1_250, category["id"]), (-400, None)):
        response = client.post(
            "/api/transactions",
            json={"date": "2026-08-05", "amount_cents": cents, "account_id": account["id"], "category_id": category_id},
        )
        assert response.status_code == 201

    report = client.get("/api/reports/month", params={"month": "2026-08", "area": "private"}).json()
    assert report["expense"]["amount_cents"] == -1_650
    assert report["uncategorized_count"] == 1
    assert {line["category_name"] for line in report["lines"]} == {"Test food", None}

    behind = client.get(
        "/api/reports/month/bookings", params={"month": "2026-08", "category_id": category["id"], "flow": "expense"}
    ).json()
    assert [item["amount_cents"] for item in behind] == [-1_250]
    uncategorized = client.get("/api/reports/month/bookings", params={"month": "2026-08"}).json()
    assert [item["amount_cents"] for item in uncategorized] == [-400]

    export_response = client.get("/api/reports/month/export.csv", params={"month": "2026-08", "area": "private"})
    assert export_response.status_code == 200
    assert export_response.headers["content-type"] == "text/csv; charset=utf-8"
    assert 'filename="z-finance-2026-08-private.csv"' in export_response.headers["content-disposition"]
    assert "-12.50" in export_response.text

    bad = client.get("/api/reports/month", params={"month": "2026-13"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Enter the month as YYYY-MM."
    assert client.get("/api/reports/month", params={"month": "2026-08", "area": "nowhere"}).status_code == 422
