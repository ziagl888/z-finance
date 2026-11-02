"""UC-ZF-008 tax plan: ESt-VZ and SVS as yearly recurring items, amount change, empty settings, API.

Expected values are hand-computed from the use case and the calendar; every value is synthetic.
"today" is 2026-09-19, so the 12-month horizon of the recurring engine ends on 2027-09-19.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zfinance.app_settings import set_setting
from zfinance.config import Settings
from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.recurring import service as recurring
from zfinance.domain.tax import service
from zfinance.domain.tax.service import TaxError, TaxFields
from zfinance.i18n import LANGUAGE_SETTING_KEY
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
ALL = date.max
EST = "Income tax prepayment"
SVS = "SVS contribution"

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "tax.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def business(conn: sqlite3.Connection) -> int:
    """The business account: 10,000.00 € from 2026-01-01."""
    return accounts.create_account(
        conn, AccountFields("Test business", "business", "giro", 1_000_000, date(2026, 1, 1))
    ).id


def _planned(conn: sqlite3.Connection, text: str) -> list[tuple[str, int]]:
    """(due date, positive amount) of the planned rows with ``text``, by due date."""
    rows = conn.execute(
        'SELECT due_date, amount_cents FROM "transaction" WHERE text = ? AND status = ? ORDER BY due_date',
        (text, "planned"),
    )
    return [(row[0], -row[1]) for row in rows]


def _booked(conn: sqlite3.Connection, text: str) -> list[tuple[str, int]]:
    """(due date, signed amount) of the booked rows with ``text``, by due date."""
    rows = conn.execute(
        'SELECT due_date, amount_cents FROM "transaction" WHERE text = ? AND status = ? ORDER BY due_date',
        (text, "booked"),
    )
    return [(row[0], row[1]) for row in rows]


def _confirm(conn: sqlite3.Connection, text: str, due: str) -> None:
    """Stand-in for confirming a due item (UC-ZF-004, task -011): the planned row becomes booked."""
    conn.execute('UPDATE "transaction" SET status = ? WHERE text = ? AND due_date = ?', ("booked", text, due))


def _fields(business_id: int | None, est: int | None = None, svs: int | None = None, **dates: Any) -> TaxFields:
    return TaxFields(business_id, est, svs, **dates)


def _term(conn: sqlite3.Connection, kind: str, due: str) -> service.TaxTerm:
    return next(t for t in service.get_settings(conn).terms if (t.kind, t.due) == (kind, due))


# --- AK1: four planned items at the due dates ---------------------------------------------------


def test_tax_plan_quarterly_terms(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-008 AK1 (1,500 € → four planned -1,500 € on 15.02./15.05./15.08./15.11.),
    step 2 (SVS at the end of Feb/May/Aug/Nov) and 2a (a Saturday due date stays: 2027-05-15)."""
    service.save_settings(conn, _fields(business, 150_000, 65_000), today=TODAY)

    assert _planned(conn, EST) == [
        ("2026-11-15", 150_000),
        ("2027-02-15", 150_000),
        ("2027-05-15", 150_000),
        ("2027-08-15", 150_000),
    ]
    assert _planned(conn, SVS) == [
        ("2026-11-30", 65_000),
        ("2027-02-28", 65_000),
        ("2027-05-31", 65_000),
        ("2027-08-31", 65_000),
    ]
    assert date(2027, 5, 15).weekday() == 5  # Saturday: the item stays on the legal date (2a)

    # Business account, expense category "Tax"/"SVS", nothing else planned.
    rows = conn.execute('SELECT DISTINCT account_id, category_id FROM "transaction" WHERE text = ?', (EST,)).fetchall()
    categories = {c.id: c.name for c in accounts.list_categories(conn)}
    assert [(r[0], categories[r[1]]) for r in rows] == [(business, "Tax")]

    # Saving the same values again changes nothing (idempotent).
    before = conn.execute('SELECT count(*) FROM "transaction"').fetchone()[0]
    service.save_settings(conn, _fields(business, 150_000, 65_000), today=TODAY)
    assert conn.execute('SELECT count(*) FROM "transaction"').fetchone()[0] == before == 8
    assert len(recurring.list_recurring(conn)) == 8


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 2, 15), ["2026-02-15", "2026-05-15", "2026-08-15", "2026-11-15"]),
        (date(2026, 11, 15), ["2026-11-15", "2027-02-15", "2027-05-15", "2027-08-15"]),
        (date(2026, 9, 22), ["2026-11-15", "2027-02-15", "2027-05-15", "2027-08-15"]),
    ],
)
def test_tax_plan_four_terms_on_a_due_date(
    conn: sqlite3.Connection, business: int, today: date, expected: list[str]
) -> None:
    """Oracle: spec-anchored — UC-ZF-008 AK1 ("genau vier" planned prepayments in the next 12 months) on
    every day, the legal due dates included (T-2026-CU-9130-046 reproduction, T-2026-CU-9130-047
    decision: window [today, today + 12 months)); dates hand-computed. 2026-09-22 is the control day."""
    service.save_settings(conn, _fields(business, 150_000), today=today)
    assert _planned(conn, EST) == [(due, 150_000) for due in expected]

    # A later generation run on the same day (app start, POST /api/recurring/generate) keeps four.
    assert recurring.generate_planned(conn, today=today).created == 0
    assert _planned(conn, EST) == [(due, 150_000) for due in expected]


# --- AK2: an amount change touches only future, unconfirmed items -------------------------------


def test_tax_plan_change_keeps_confirmed(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-008 AK2 (a confirmed quarter keeps its amount, later ones change) and
    step 5 (the old item ends, a new one starts at the next due date): 1,500 € → 1,600 € on 2026-11-20
    after 2026-11-15 was confirmed."""
    service.save_settings(conn, _fields(business, 150_000), today=TODAY)
    old_item = _term(conn, "est", "11-15").recurring_id
    _confirm(conn, EST, "2026-11-15")

    service.save_settings(conn, _fields(business, 160_000), today=date(2026, 11, 20))

    assert _booked(conn, EST) == [("2026-11-15", -150_000)]
    assert _planned(conn, EST) == [
        ("2027-02-15", 160_000),
        ("2027-05-15", 160_000),
        ("2027-08-15", 160_000),
        ("2027-11-15", 160_000),
    ]
    assert accounts.balance_at(conn, business, ALL) == 1_000_000 - 150_000
    # The old 11-15 item ends the day before the new one starts and keeps the confirmed row.
    assert recurring.get_recurring(conn, old_item).end_date == date(2027, 11, 14)
    assert _term(conn, "est", "11-15").recurring_id != old_item
    assert recurring.get_recurring(conn, _term(conn, "est", "11-15").recurring_id).start_date == date(2027, 11, 15)


def test_tax_plan_change_skips_early_confirmed(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-008 AK2: a quarter paid before its due date (booked on 2026-11-15 while
    today is 2026-11-13) is confirmed, so the new amount starts with the following due dates."""
    service.save_settings(conn, _fields(business, 150_000), today=TODAY)
    _confirm(conn, EST, "2026-11-15")

    service.save_settings(conn, _fields(business, 160_000), today=date(2026, 11, 13))

    assert _booked(conn, EST) == [("2026-11-15", -150_000)]
    # Horizon ends 2027-11-13: the next 11-15 is outside it, so three planned items.
    assert _planned(conn, EST) == [("2027-02-15", 160_000), ("2027-05-15", 160_000), ("2027-08-15", 160_000)]
    assert recurring.get_recurring(conn, _term(conn, "est", "11-15").recurring_id).start_date == date(2027, 11, 15)


def test_tax_plan_change_keeps_overdue_unconfirmed(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-008 5a: a change only adjusts future items; quarters already due and not
    yet confirmed (2026-11-15, 2027-02-15 on 2027-03-01) keep the old 1,500 €."""
    service.save_settings(conn, _fields(business, 150_000), today=TODAY)

    service.save_settings(conn, _fields(business, 160_000), today=date(2027, 3, 1))

    assert _planned(conn, EST) == [
        ("2026-11-15", 150_000),
        ("2027-02-15", 150_000),
        ("2027-05-15", 160_000),
        ("2027-08-15", 160_000),
        ("2027-11-15", 160_000),
        ("2028-02-15", 160_000),
    ]


# --- UC 1a: empty settings ----------------------------------------------------------------------


def test_tax_plan_empty_creates_nothing_and_flags(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — UC-ZF-008 1a (no amounts: no recurring items, hint "Taxes & levies not set")
    and step 1 (an amount needs the business account)."""
    assert service.tax_plan_missing(conn) is True  # nothing stored yet

    service.save_settings(conn, _fields(None), today=TODAY)
    assert recurring.list_recurring(conn) == []
    assert service.tax_plan_missing(conn) is True

    saved = service.save_settings(conn, _fields(business, 150_000), today=TODAY)
    assert saved.plan_missing is False
    assert len(saved.terms) == 4
    assert service.tax_plan_missing(conn) is False

    # Clearing the amount ends the plan again: nothing planned, flag back on.
    cleared = service.save_settings(conn, _fields(business), today=TODAY)
    assert cleared.plan_missing is True and cleared.terms == []
    assert _planned(conn, EST) == []
    assert recurring.list_recurring(conn) == []

    with pytest.raises(TaxError):  # an amount without an account
        service.save_settings(conn, _fields(None, 150_000), today=TODAY)
    with pytest.raises(TaxError):  # an amount must be greater than 0
        service.save_settings(conn, _fields(business, 0), today=TODAY)


# --- editable due dates -------------------------------------------------------------------------


def test_tax_plan_due_dates_editable(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — orchestrator note: the due-date list is editable (one yearly item per date);
    hand-computed occurrences on/after 2026-09-19 inside the horizon to 2027-09-19."""
    service.save_settings(conn, _fields(business, 100_000, est_due_dates=("09-15", "03-15")), today=TODAY)
    assert service.get_settings(conn).est_due_dates == ("03-15", "09-15")  # ascending
    assert _planned(conn, EST) == [("2027-03-15", 100_000), ("2027-09-15", 100_000)]
    kept = _term(conn, "est", "03-15").recurring_id

    service.save_settings(conn, _fields(business, 100_000, est_due_dates=("03-15", "09-15", "12-31")), today=TODAY)
    assert _planned(conn, EST) == [("2026-12-31", 100_000), ("2027-03-15", 100_000), ("2027-09-15", 100_000)]
    assert _term(conn, "est", "03-15").recurring_id == kept  # an unchanged date keeps its item

    service.save_settings(conn, _fields(business, 100_000, est_due_dates=("09-15", "12-31")), today=TODAY)
    assert _planned(conn, EST) == [("2026-12-31", 100_000), ("2027-09-15", 100_000)]
    assert {t.due for t in service.get_settings(conn).terms if t.kind == "est"} == {"09-15", "12-31"}

    for bad in [
        (),
        ("13-01",),
        ("2-15",),
        ("02-32",),
        ("00-10",),
        ("02-15", "02-15"),
        (*(f"{m:02d}-01" for m in range(1, 13)), "01-02"),
    ]:
        with pytest.raises(TaxError):
            service.parse_due_dates(bad)
    assert service.occurrence("02-31", 2028) == date(2028, 2, 29)
    assert service.occurrence("11-31", 2026) == date(2026, 11, 30)


# --- prepayments_ytd ----------------------------------------------------------------------------


def test_prepayments_ytd(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — orchestrator contract: the sum of the BOOKED ESt-VZ + SVS of the year in positive
    cents; hand-computed 1,500 + 400 = 1,900 € for 2026 and 1,500 € for 2027, planned rows excluded."""
    assert service.prepayments_ytd(conn, 2026) == 0  # no plan

    service.save_settings(conn, _fields(business, 150_000, 40_000), today=TODAY)
    assert service.prepayments_ytd(conn, 2026) == 0  # planned rows do not count

    _confirm(conn, EST, "2026-11-15")
    _confirm(conn, SVS, "2026-11-30")
    assert service.prepayments_ytd(conn, 2026) == 190_000
    assert service.prepayments_ytd(conn, 2027) == 0

    # A back-payment with the category Tax is not a prepayment.
    tax_category = next(c.id for c in accounts.list_categories(conn) if c.name == "Tax")
    conn.execute(
        'INSERT INTO "transaction" (date, amount_cents, account_id, category_id, text, status, source, created_at)'
        " VALUES ('2026-12-01', -99900, ?, ?, 'Back-payment', 'booked', 'manual', '2026-12-01T00:00:00+00:00')",
        (business, tax_category),
    )
    assert service.prepayments_ytd(conn, 2026) == 190_000

    # After a change the booked rows of the superseded items still count, the new amount adds up.
    service.save_settings(conn, _fields(business, 160_000, 40_000), today=date(2026, 12, 2))
    assert service.prepayments_ytd(conn, 2026) == 190_000
    _confirm(conn, EST, "2027-02-15")
    assert service.prepayments_ytd(conn, 2027) == 160_000


# --- categories ---------------------------------------------------------------------------------


def test_tax_plan_reuses_or_creates_categories(conn: sqlite3.Connection, business: int, tmp_path: Path) -> None:
    """Oracle: spec-anchored — UC-ZF-008 precondition (categories "Steuer" and "SVS" exist): existing ones are
    reused, missing ones are created as business expense categories in the UI language."""
    accounts.create_category(conn, CategoryFields("Steuer", "expense", "business"))
    accounts.create_category(conn, CategoryFields("SVS", "expense", "business"))
    service.save_settings(conn, _fields(business, 100_000, 50_000), today=TODAY)
    assert sorted(c.name for c in accounts.list_categories(conn)) == ["SVS", "Steuer"]  # nothing added

    for language, expected in [("de", ["SVS", "Steuer"]), ("en", ["SVS", "Tax"])]:
        other = connect(tmp_path / f"{language}.sqlite3")
        migrate(other)
        account = accounts.create_account(
            other, AccountFields("Test business", "business", "giro", 0, date(2026, 1, 1))
        )
        set_setting(other, LANGUAGE_SETTING_KEY, language)
        service.save_settings(other, _fields(account.id, 100_000, 50_000), today=TODAY)
        created = [(c.name, c.kind, c.area) for c in accounts.list_categories(other)]
        assert sorted(created) == [(name, "expense", "business") for name in expected]
        other.close()


# --- AK3: no VAT --------------------------------------------------------------------------------

_VAT_WORDS = {"vat", "ust", "mwst", "umsatzsteuer", "uva", "zahllast", "vorsteuer"}


def _words(name: str) -> set[str]:
    return {word for word in re.split(r"[^a-z0-9]+", name.lower()) if word}


def _names(node: Any) -> Iterator[str]:
    """Every key and string value of an OpenAPI document (paths, schema and property names, parameters)."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _names(value)
    elif isinstance(node, list):
        for value in node:
            yield from _names(value)
    elif isinstance(node, str):
        yield node


def test_tax_plan_has_no_vat_anywhere(client: TestClient, settings: Settings) -> None:
    """Oracle: spec-anchored — UC-ZF-008 AK3 / SPEC AK11: no VAT field and no VAT calculation in the whole tool —
    checked over the OpenAPI schema (paths, schemas, properties) and every table and column."""
    schema = client.get("/openapi.json").json()
    assert "/api/settings/tax" in schema["paths"]  # the check is not vacuous
    assert _VAT_WORDS.isdisjoint({word for name in _names(schema) for word in _words(name)})

    conn = connect(settings.db_path)
    try:
        tables = [row[1] for row in conn.execute("PRAGMA table_list") if row[2] == "table"]
        names = tables + [column[1] for table in tables for column in conn.execute(f'PRAGMA table_info("{table}")')]  # nosec B608 — names from the schema
    finally:
        conn.close()
    assert "tax_settings" in tables
    assert _VAT_WORDS.isdisjoint({word for name in names for word in _words(name)})


# --- API ----------------------------------------------------------------------------------------


def test_tax_api_roundtrip(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-008 steps 1-2 over HTTP: two amounts and the business account give four
    plan items per kind; 1a: nothing entered → plan_missing; invalid input is refused."""
    first = client.get("/api/settings/tax").json()
    assert first["plan_missing"] is True and first["terms"] == []
    assert first["est_due_dates"] == ["02-15", "05-15", "08-15", "11-15"]
    assert first["svs_due_dates"] == ["02-31", "05-31", "08-31", "11-31"]

    today = date.today()
    business = client.post(
        "/api/accounts",
        json={
            "name": "Test business",
            "area": "business",
            "type": "giro",
            "opening_balance_cents": 1_000_000,
            "opening_date": date(today.year - 1, 1, 1).isoformat(),
        },
    ).json()["id"]
    body = {"business_account_id": business, "est_vz_quarter_cents": 150_000, "svs_quarter_cents": 65_000}

    saved = client.put("/api/settings/tax", json=body)
    assert saved.status_code == 200
    out = saved.json()
    assert out["plan_missing"] is False
    assert [(t["kind"], t["due"], t["amount_cents"]) for t in out["terms"]] == [
        ("est", "02-15", 150_000),
        ("est", "05-15", 150_000),
        ("est", "08-15", 150_000),
        ("est", "11-15", 150_000),
        ("svs", "02-31", 65_000),
        ("svs", "05-31", 65_000),
        ("svs", "08-31", 65_000),
        ("svs", "11-31", 65_000),
    ]
    assert sum(t["planned_count"] for t in out["terms"]) >= 8  # one per item; 9 on a due date's own day
    assert client.get("/api/settings/tax").json() == out

    assert client.put("/api/settings/tax", json={**body, "est_vz_quarter_cents": 1.5}).status_code == 422
    assert client.put("/api/settings/tax", json={**body, "est_vz_quarter_cents": -5}).status_code == 422
    assert client.put("/api/settings/tax", json={**body, "est_due_dates": ["13-01"]}).status_code == 400
    assert client.put("/api/settings/tax", json={**body, "business_account_id": 999}).status_code == 404
    refused = client.put("/api/settings/tax", json={"est_vz_quarter_cents": 150_000})
    assert refused.status_code == 400
    assert client.get("/api/settings/tax").json() == out  # a refusal changes nothing

    localized = client.put(
        "/api/settings/tax", json={"est_vz_quarter_cents": 150_000}, headers={"Accept-Language": "de"}
    )
    assert localized.json()["detail"] == "Wähle das Konto, von dem die Zahlungen abgehen."

    cleared = client.put("/api/settings/tax", json={"business_account_id": business}).json()
    assert cleared["plan_missing"] is True and cleared["terms"] == []


# --- a plan item is not deleted on the recurring screen ----------------------------------------


def test_deleting_a_tax_plan_recurring_is_refused(client: TestClient) -> None:
    """Oracle: spec-anchored — UC-ZF-008 success guarantee and step 5 (confirmed items stay): the settings
    are the source of the plan, so DELETE /api/recurring/{id} on a plan item answers 409 naming
    Taxes & levies and leaves the item in place; a German client gets the German message."""
    today = date.today()
    business = client.post(
        "/api/accounts",
        json={
            "name": "Test business",
            "area": "business",
            "type": "giro",
            "opening_balance_cents": 1_000_000,
            "opening_date": date(today.year - 1, 1, 1).isoformat(),
        },
    ).json()["id"]
    body = {"business_account_id": business, "est_vz_quarter_cents": 100_000}
    recurring_id = client.put("/api/settings/tax", json=body).json()["terms"][0]["recurring_id"]

    refused = client.delete(f"/api/recurring/{recurring_id}")
    assert refused.status_code == 409
    assert "Taxes & levies" in refused.json()["detail"]
    assert client.get(f"/api/recurring/{recurring_id}").status_code == 200

    localized = client.delete(f"/api/recurring/{recurring_id}", headers={"Accept-Language": "de"})
    assert localized.status_code == 409
    assert "Steuern & Abgaben" in localized.json()["detail"]


def test_booked_prepayments_stay_when_the_plan_item_is_protected(conn: sqlite3.Connection, business: int) -> None:
    """Oracle: spec-anchored — the bug trigger (1,000 € booked, then the plan's recurring item deleted):
    prepayments_ytd must stay 1,000 €. Current and superseded plan items are both protected, because
    the booked rows of a superseded item still count (tax_item.retired_at)."""
    service.save_settings(conn, _fields(business, 100_000), today=TODAY)
    _confirm(conn, EST, "2026-11-15")
    assert service.prepayments_ytd(conn, 2026) == 100_000

    current = _term(conn, "est", "11-15").recurring_id
    with pytest.raises(recurring.Conflict):
        service.ensure_not_plan_item(conn, current)

    service.save_settings(conn, _fields(business, 120_000), today=date(2026, 12, 2))
    assert _term(conn, "est", "11-15").recurring_id != current  # the old item is superseded
    with pytest.raises(recurring.Conflict):
        service.ensure_not_plan_item(conn, current)
    assert service.prepayments_ytd(conn, 2026) == 100_000

    other = recurring.create_recurring(
        conn,
        recurring.RecurringFields("Rent", -50_000, business, "monthly", date(2026, 10, 1), day_of_month=1),
        today=TODAY,
    )
    service.ensure_not_plan_item(conn, other.id)  # an ordinary recurring item is not affected
