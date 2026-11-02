"""UC-ZF-007 categorisation rules: engine, priority, uncategorised-only, regex stop condition, HTTP.

Expected values are hand-computed from the use case and docs/specs/SPEC-T-2026-CU-9130-010.md;
every value is synthetic.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zfinance.db import connect
from zfinance.domain.accounts import service as accounts
from zfinance.domain.accounts.service import AccountFields, CategoryFields
from zfinance.domain.backup import service as backup
from zfinance.domain.rules import service
from zfinance.domain.rules.service import RuleFields
from zfinance.domain.transactions import service as transactions
from zfinance.domain.transactions.service import BookingFields, TransferFields
from zfinance.migrator import migrate

TODAY = date(2026, 9, 19)
CATASTROPHIC = "(a|aa)+$"  # exponential on "aaaa...!"

# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database with the shipped migrations."""
    connection = connect(tmp_path / "rules.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def giro(conn: sqlite3.Connection) -> int:
    return accounts.create_account(conn, AccountFields("Test giro", "private", "giro", 0, date(2026, 1, 1))).id


def _category(conn: sqlite3.Connection, name: str) -> int:
    return accounts.create_category(conn, CategoryFields(name, "expense")).id


def _book(
    conn: sqlite3.Connection,
    account_id: int,
    text: str,
    category_id: int | None = None,
    counterparty: str | None = None,
) -> transactions.Transaction:
    fields = BookingFields(date(2026, 9, 1), -1_000, account_id, category_id, text, counterparty)
    return transactions.create_transaction(conn, fields, today=TODAY)


def _category_of(conn: sqlite3.Connection, transaction_id: int) -> int | None:
    return transactions.get_transaction(conn, transaction_id).category_id


def _rule(conn: sqlite3.Connection, pattern: str, category_id: int, kind: str = "contains", **more: object):
    return service.create_rule(conn, RuleFields(kind, pattern, category_id, **more))  # type: ignore[arg-type]


def _insert_raw(conn: sqlite3.Connection, kind: str, pattern: str, category_id: int, priority: int = 0) -> int:
    """A rule as an old or hand-edited database could hold it: no validation."""
    row = conn.execute(
        "INSERT INTO rule (match_kind, pattern, category_id, priority, created_at)"
        " VALUES (?, ?, ?, ?, 'x') RETURNING id",
        (kind, pattern, category_id, priority),
    ).fetchone()
    return int(row["id"])


# --- AK10: new rule applies at once, spares categorised -----------------------------------------


def test_new_rule_applies_immediately_and_spares_categorised(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-007 AK2/5a: a new rule categorises all uncategorised hits, no categorised one."""
    groceries, other = _category(conn, "Test groceries"), _category(conn, "Test other")
    a = _book(conn, giro, "Test Shop A")
    b = _book(conn, giro, "test shop b")
    unrelated = _book(conn, giro, "Kiosk")
    already = _book(conn, giro, "Test Shop C", other)

    preview = service.preview_rule(conn, "contains", "test shop")
    rule, result = service.create_rule_and_apply(conn, RuleFields("contains", "test shop", groceries))

    assert preview.count == 2  # hit count before saving = what is applied (no higher-priority rule here)
    assert {sample.id for sample in preview.samples} == {a.id, b.id}
    assert result.by_rule == {rule.id: 2} and result.categorised == 2
    assert (_category_of(conn, a.id), _category_of(conn, b.id)) == (groceries, groceries)
    assert _category_of(conn, unrelated.id) is None
    assert _category_of(conn, already.id) == other


# --- AK11: priority, then age --------------------------------------------------------------------


def test_priority_then_age(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-007 5b: higher priority wins; equal priority -> the rule created first."""
    low, high, first, second = (
        _category(conn, name) for name in ("Test low", "Test high", "Test first", "Test second")
    )
    _rule(conn, "shop", low, priority=0)
    _rule(conn, "test", high, priority=5)  # created later, but higher priority
    _rule(conn, "market", first)
    _rule(conn, "mark", second)  # same priority, created later
    shop = _book(conn, giro, "Test Shop")
    market = _book(conn, giro, "Test Market")

    result = service.apply_rules(conn)

    assert _category_of(conn, shop.id) == high
    assert _category_of(conn, market.id) == high  # "test" (priority 5) beats both market rules
    assert result.categorised == 2

    # Without the priority-5 rule the age decides between the two priority-0 market rules.
    conn.execute("DELETE FROM rule WHERE pattern = 'test'")
    market_two = _book(conn, giro, "Other Market")
    service.apply_rules(conn)
    assert _category_of(conn, market_two.id) == first


# --- AK12: existing bookings, given bookings, idempotency ---------------------------------------


def test_rule_apply_to_existing(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK9: a rule applies afterwards to existing bookings; a second run is a no-op."""
    food = _category(conn, "Test food")
    booked_before = [_book(conn, giro, f"Billa {number}") for number in range(3)]
    _rule(conn, "billa", food)

    first_run = service.apply_rules(conn)
    second_run = service.apply_rules(conn)

    assert first_run.categorised == 3
    assert all(_category_of(conn, item.id) == food for item in booked_before)
    assert second_run.categorised == 0


def test_apply_to_given_transactions(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK9: the import passes the rows it wrote; only those are touched."""
    food = _category(conn, "Test food")
    _rule(conn, "billa", food)
    mine, elsewhere = _book(conn, giro, "Billa 1"), _book(conn, giro, "Billa 2")

    result = service.apply_rules(conn, [mine])

    assert result.categorised == 1
    assert (_category_of(conn, mine.id), _category_of(conn, elsewhere.id)) == (food, None)
    assert service.apply_rules(conn, []).categorised == 0
    many = [_book(conn, giro, "Billa x") for _ in range(service.ID_CHUNK + 30)]  # more ids than one query holds
    assert service.apply_rules(conn, many).categorised == len(many)


def test_only_uncategorized_flag(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — task interface only_uncategorized=True; False re-categorises."""
    old, new = _category(conn, "Test old"), _category(conn, "Test new")
    item = _book(conn, giro, "Billa", old)
    _rule(conn, "billa", new)

    assert service.apply_rules(conn).categorised == 0
    assert _category_of(conn, item.id) == old
    assert service.apply_rules(conn, only_uncategorized=False).categorised == 1
    assert _category_of(conn, item.id) == new


def test_transfer_legs_are_never_touched(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC Definitions: transfer legs are not categorised by rules (orchestrator note)."""
    savings = accounts.create_account(conn, AccountFields("Test savings", "private", "savings", 0, date(2026, 1, 1))).id
    food = _category(conn, "Test food")
    _rule(conn, "billa", food)
    source, target = transactions.create_transfer(
        conn, TransferFields(date(2026, 9, 2), 500, giro, savings, None, "Billa transfer"), today=TODAY
    )

    assert service.apply_rules(conn).categorised == 0
    assert service.apply_rules(conn, only_uncategorized=False).categorised == 0
    assert (_category_of(conn, source.id), _category_of(conn, target.id)) == (None, None)


# --- AK13: draft and preview ----------------------------------------------------------------------


def test_draft_and_preview(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-007 step 4/5: "contains <text> -> category", hit count shown before saving."""
    food = _category(conn, "Test food")
    item = _book(conn, giro, "Billa 1234", food, "Billa AG")
    only_counterparty = _book(conn, giro, "", food, "Baker Ltd")

    draft = service.draft_from_transaction(conn, item.id)
    assert draft == RuleFields("contains", "Billa 1234", food, "Billa AG")
    assert service.draft_from_transaction(conn, only_counterparty.id).pattern == "Baker Ltd"

    _book(conn, giro, "Billa 9")
    _book(conn, giro, "billa 10")
    assert service.preview_rule(conn, "contains", "billa").count == 2
    assert service.preview_rule(conn, "contains", "nothing").count == 0


def test_draft_refusals(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK13: no category, no text, or a transfer leg cannot be the basis of a rule."""
    food = _category(conn, "Test food")
    savings = accounts.create_account(conn, AccountFields("Test savings", "private", "savings", 0, date(2026, 1, 1))).id
    uncategorised = _book(conn, giro, "Billa")
    blank = _book(conn, giro, "", food)
    leg, _ = transactions.create_transfer(
        conn, TransferFields(date(2026, 9, 2), 500, giro, savings, food, "Billa"), today=TODAY
    )

    for item in (uncategorised, blank, leg):
        with pytest.raises(service.RulesError):
            service.draft_from_transaction(conn, item.id)
    with pytest.raises(transactions.NotFound):
        service.draft_from_transaction(conn, 9_999)


# --- AK14: saving a regex ---------------------------------------------------------------------------


def test_repeat_product() -> None:
    """Oracle: spec-anchored — SPEC AK14: the product of counted repeats bounds the compile cost (hand-counted)."""
    assert service.repeat_product(r"\d{4}-\d{2}-\d{2}") == 16
    assert service.repeat_product(r"a{2,5}") == 5
    assert service.repeat_product(r"a{3,}") == 3
    assert service.repeat_product(r"a{0}") == 1
    assert service.repeat_product(r"[{50}]") == 1  # inside a character class
    assert service.repeat_product(r"\{99\}") == 1  # escaped braces
    assert service.repeat_product(r"\\{50}") == 50  # an escaped backslash, then a real repeat
    assert service.repeat_product(r"((a{50}){50}){50}") == 125_000
    assert service.repeat_product("a{,}") == 1  # not a repeat


def test_check_pattern_refuses_the_bad_ones() -> None:
    """Oracle: spec-anchored — SPEC AK14: blank, long, invalid, costly and slow patterns are refused on save."""
    service.check_pattern("regex", r"^bill?a\s+\d{1,4}\b")  # fine
    service.check_pattern("contains", "(")  # a plain substring, no regex

    refused = [
        ("contains", "   "),
        ("regex", ""),
        ("contains", "x" * (service.PATTERN_MAX_LENGTH + 1)),
        ("regex", "("),
        ("regex", "[a-"),
        ("regex", r"((((a{50}){50}){50}){50}){50}"),  # compiling this would not return
    ]
    for kind, pattern in refused:
        with pytest.raises(service.RulesError):
            service.check_pattern(kind, pattern)  # type: ignore[arg-type]

    started = time.perf_counter()
    with pytest.raises(service.RulesError, match="too slow"):
        service.check_pattern("regex", CATASTROPHIC, timeout_seconds=0.02)
    # One probe times out after 0.02 s; the whole check must not run for seconds.
    assert time.perf_counter() - started < 2


def test_create_rule_refuses_a_slow_regex_and_stores_nothing(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC AK14: a refused pattern is not stored."""
    food = _category(conn, "Test food")

    with pytest.raises(service.RulesError):
        service.create_rule(conn, RuleFields("regex", CATASTROPHIC, food), timeout_seconds=0.02)

    assert service.list_rules(conn) == []


# --- AK15: a stored slow or broken rule ------------------------------------------------------------


def test_catastrophic_regex_is_marked_and_skipped_while_the_others_apply(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — task stop condition: a backtracking rule times out, is marked, the engine continues."""
    slow_cat, food = _category(conn, "Test slow"), _category(conn, "Test food")
    slow_id = _insert_raw(conn, "regex", CATASTROPHIC, slow_cat, priority=9)  # tried first
    good = _rule(conn, "shop", food)
    victim = _book(conn, giro, "a" * 60 + "!")
    shop = _book(conn, giro, "Test shop")

    started = time.perf_counter()
    result = service.apply_rules(conn, timeout_seconds=0.02)
    elapsed = time.perf_counter() - started

    assert result.timed_out == [slow_id]
    assert result.by_rule == {good.id: 1}
    assert _category_of(conn, shop.id) == food
    assert _category_of(conn, victim.id) is None
    assert service.get_rule(conn, slow_id).invalid_reason == "timeout"  # kept, marked, not deleted
    assert elapsed < 2  # one timeout, then the rule is skipped for the remaining bookings

    again = service.apply_rules(conn, timeout_seconds=0.02)
    assert again.timed_out == [] and again.categorised == 0


def test_regex_that_does_not_compile_is_marked_syntax(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK15: a stored pattern that does not compile is marked syntax and listed."""
    food = _category(conn, "Test food")
    broken = _insert_raw(conn, "regex", "(", food)
    _book(conn, giro, "Billa")

    assert service.apply_rules(conn).categorised == 0
    assert service.get_rule(conn, broken).invalid_reason == "syntax"
    assert [rule.id for rule in service.list_rules(conn)] == [broken]


def test_saving_a_valid_pattern_clears_the_mark(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC AK15: fixing the pattern clears the invalid mark; the rule applies again."""
    food = _category(conn, "Test food")
    slow = _insert_raw(conn, "regex", CATASTROPHIC, food)
    _book(conn, giro, "a" * 60 + "!")
    service.apply_rules(conn, timeout_seconds=0.02)
    assert service.get_rule(conn, slow).invalid_reason == "timeout"

    fixed = service.update_rule(conn, slow, RuleFields("regex", r"^a+!$", food))

    assert fixed.invalid_reason is None
    assert service.apply_rules(conn).categorised == 1


# --- AK16: matching, counterparty, validation ------------------------------------------------------


def test_contains_ignores_case_including_umlauts(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — UC-ZF-007 step 4 "enthält <Text>": case-insensitive for umlauts too (Müller = MÜLLER)."""
    baker = _category(conn, "Test baker")
    _rule(conn, "müller", baker)
    by_text = _book(conn, giro, "BÄCKEREI MÜLLER Wien")
    by_counterparty = _book(conn, giro, "Card payment", counterparty="Müller GmbH")
    miss = _book(conn, giro, "Muller")  # no umlaut: not the same text

    service.apply_rules(conn)

    assert (_category_of(conn, by_text.id), _category_of(conn, by_counterparty.id)) == (baker, baker)
    assert _category_of(conn, miss.id) is None


def test_regex_rule_matches_case_insensitively(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC Definitions "Rule match": regex is a case-insensitive search."""
    food = _category(conn, "Test food")
    _rule(conn, r"^bill?a\s+\d+$", food, "regex")
    yes, no = _book(conn, giro, "BILA 123"), _book(conn, giro, "Billa Filiale")

    service.apply_rules(conn)

    assert (_category_of(conn, yes.id), _category_of(conn, no.id)) == (food, None)


def test_rule_counterparty_fills_only_where_empty(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC column contract: the rule's counterparty is filled into a booking that has none."""
    food = _category(conn, "Test food")
    _rule(conn, "billa", food, counterparty="Billa AG")
    without = _book(conn, giro, "Billa 1")
    with_one = _book(conn, giro, "Billa 2", counterparty="Someone else")

    service.apply_rules(conn)

    assert transactions.get_transaction(conn, without.id).counterparty == "Billa AG"
    assert transactions.get_transaction(conn, with_one.id).counterparty == "Someone else"


def test_rule_validation_and_duplicates(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — SPEC AK16: blank pattern, unknown category and a duplicate rule are refused."""
    food, other = _category(conn, "Test food"), _category(conn, "Test other")
    _rule(conn, "Billa", food)

    with pytest.raises(service.Conflict):
        _rule(conn, "billa", food)  # same kind, pattern (case-insensitive) and category
    _rule(conn, "billa", other)  # a different category is a legitimate second rule
    with pytest.raises(service.RulesError):
        _rule(conn, "  ", food)
    with pytest.raises(accounts.NotFound):
        _rule(conn, "x", 9_999)
    with pytest.raises(service.NotFound):
        service.get_rule(conn, 9_999)
    with pytest.raises(service.NotFound):
        service.delete_rule(conn, 9_999)


def test_deleting_a_rule_keeps_the_categories_it_set(conn: sqlite3.Connection, giro: int) -> None:
    """Oracle: spec-anchored — SPEC column contract: rules are kept or removed, bookings keep their category."""
    food = _category(conn, "Test food")
    rule = _rule(conn, "billa", food)
    item = _book(conn, giro, "Billa")
    service.apply_rules(conn)

    service.delete_rule(conn, rule.id)

    assert service.list_rules(conn) == []
    assert _category_of(conn, item.id) == food


def test_deleting_a_category_removes_its_rules(conn: sqlite3.Connection) -> None:
    """Oracle: spec-anchored — migration 040 comment: ON DELETE CASCADE, so an unused category can still be deleted."""
    food = _category(conn, "Test food")
    _rule(conn, "billa", food)

    accounts.delete_category(conn, food)

    assert service.list_rules(conn) == []


def test_rules_survive_backup_and_restore(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Oracle: spec-anchored — ARCHITECTURE "Backup and restore": later feature tables are exported schema-driven."""
    food = _category(conn, "Test food")
    _rule(conn, "billa", food, priority=3)
    raw = backup.dumps_export(backup.export_data(conn))

    target = connect(tmp_path / "restored.sqlite3")
    try:
        migrate(target)
        backup.restore_export(target, raw)
        assert [(r.pattern, r.priority, r.category_id) for r in service.list_rules(target)] == [("billa", 3, food)]
    finally:
        target.close()


# --- HTTP ---------------------------------------------------------------------------------------------


def test_rule_endpoints(client: TestClient) -> None:
    """Oracle: spec-anchored — SPEC API: create applies at once, preview, draft, apply, update, delete, errors."""
    account = client.post(
        "/api/accounts",
        json={
            "name": "Test giro",
            "area": "private",
            "type": "giro",
            "opening_balance_cents": 0,
            "opening_date": "2026-01-01",
            "reserve_floor_cents": 0,
        },
    ).json()
    category = client.post(
        "/api/categories", json={"name": "Test food", "kind": "expense", "area": None, "parent_id": None}
    ).json()
    ids = []
    for text in ("Billa 1", "Billa 2", "Kiosk"):
        booking = {"date": "2026-09-01", "amount_cents": -500, "account_id": account["id"], "text": text}
        ids.append(client.post("/api/transactions", json=booking).json()["id"])

    preview = client.post("/api/rules/preview", json={"match_kind": "contains", "pattern": "billa"}).json()
    assert preview["count"] == 2 and len(preview["samples"]) == 2

    created = client.post(
        "/api/rules", json={"match_kind": "contains", "pattern": "billa", "category_id": category["id"], "priority": 1}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["applied_count"] == 2 and body["rule"]["invalid_reason"] is None
    assert client.get(f"/api/transactions/{ids[0]}").json()["category_id"] == category["id"]
    assert client.get(f"/api/transactions/{ids[2]}").json()["category_id"] is None

    draft = client.get("/api/rules/draft", params={"transaction_id": ids[0]}).json()
    assert draft["pattern"] == "Billa 1" and draft["match_kind"] == "contains"
    assert client.get("/api/rules/draft", params={"transaction_id": ids[2]}).status_code == 400

    duplicate = client.post(
        "/api/rules", json={"match_kind": "contains", "pattern": "BILLA", "category_id": category["id"]}
    )
    assert duplicate.status_code == 409
    invalid = client.post("/api/rules", json={"match_kind": "regex", "pattern": "(", "category_id": category["id"]})
    assert invalid.status_code == 400 and invalid.json()["detail"].startswith("The regular expression is not valid")
    assert client.post("/api/rules/apply").json()["categorised"] == 0

    rule_id = body["rule"]["id"]
    updated = client.put(
        f"/api/rules/{rule_id}",
        json={"match_kind": "contains", "pattern": "kiosk", "category_id": category["id"], "priority": 2},
    )
    assert updated.status_code == 200 and updated.json()["priority"] == 2
    assert client.post("/api/rules/apply").json()["categorised"] == 1
    assert [r["pattern"] for r in client.get("/api/rules").json()] == ["kiosk"]
    assert client.delete(f"/api/rules/{rule_id}").status_code == 204
    assert client.delete(f"/api/rules/{rule_id}").status_code == 404
