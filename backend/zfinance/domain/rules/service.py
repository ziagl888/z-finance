"""Categorisation rules (UC-ZF-007): plain functions over a ``sqlite3.Connection``.

A rule (``contains`` or ``regex`` pattern → category, optional counterparty, priority) is matched
against the ``text`` and the ``counterparty`` of a booking. ``apply_rules`` gives every
uncategorised, non-transfer booking the category of the winning rule: the highest ``priority``,
among equals the rule created first (lowest id). A booking that already has a category is never
touched unless the caller asks for ``only_uncategorized=False``.

Regular expressions come from the user and Python's ``re`` cannot be interrupted, so the
``regex`` package is used: its ``search(..., timeout=)`` raises ``TimeoutError``. Three layers keep
a catastrophic pattern from blocking the engine:

* on save, a pattern must compile, stay short, keep the product of its counted repeats small
  (compiling nested ``{n}`` repeats is itself unbounded) and survive a fixed probe corpus;
* while matching, every search has a time limit; the first ``TimeoutError`` of a rule marks it
  ``invalid_reason = 'timeout'`` and skips it for the rest of the run;
* an invalid rule is kept and listed, never applied and never deleted; saving a changed valid
  pattern clears the mark.

Invariants: ``apply_rules`` writes only rows without ``transfer_group_id`` (on every path, the
``UPDATE`` itself carries the condition), and only rows with ``category_id IS NULL`` unless
``only_uncategorized`` is false.

Errors are ``RulesError`` subclasses carrying an ``N_()`` msgid and its parameters; the API
translates them per request.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, get_args

import regex

from zfinance.db import transaction
from zfinance.domain.accounts import service as accounts
from zfinance.domain.transactions import service as transactions
from zfinance.i18n import N_

MatchKind = Literal["contains", "regex"]
InvalidReason = Literal["syntax", "timeout"]

MATCH_KINDS: tuple[MatchKind, ...] = get_args(MatchKind)

#: Longest accepted pattern, in characters.
PATTERN_MAX_LENGTH = 200
#: Largest accepted product of all counted repeats (``{n}``, ``{n,m}``) of one pattern.
REPEAT_PRODUCT_MAX = 10_000
#: Longest time one search may take, in seconds. A sane pattern needs microseconds.
MATCH_TIMEOUT_SECONDS = 0.25
#: Only this many leading characters of a booking's text/counterparty are matched.
MATCH_TEXT_MAX = 1_000
#: Texts a new regex must survive on save; the classic backtracking triggers.
PROBE_TEXTS = ("a" * 64 + "!", "0" * 64 + "x", "ab" * 32 + "!", "a " * 32 + "!")
#: Sample bookings a preview returns.
PREVIEW_SAMPLE_LIMIT = 5
#: Ids per query when a caller passes a list of bookings (SQLite caps the number of parameters).
ID_CHUNK = 500

_COUNTED_REPEAT = regex.compile(r"\{(\d*)(?:(,)(\d*))?\}")


# --- errors ------------------------------------------------------------------------------------


class RulesError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders filled from ``params``;
    ``str(exc)`` is the English text.
    """

    status_code = 400

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class NotFound(RulesError):
    """The rule does not exist."""

    status_code = 404


class Conflict(RulesError):
    """The rule already exists."""

    status_code = 409


# --- data --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleFields:
    """What the user enters for a rule (create and full update)."""

    match_kind: MatchKind
    pattern: str
    category_id: int
    counterparty: str | None = None
    priority: int = 0


@dataclass(frozen=True, kw_only=True)
class Rule(RuleFields):
    """A stored rule. ``invalid_reason`` is ``None`` for a rule the engine applies."""

    id: int
    invalid_reason: InvalidReason | None = None
    created_at: str


@dataclass(frozen=True)
class ApplyResult:
    """What one ``apply_rules`` run did.

    ``by_rule`` maps a rule id to the number of bookings it categorised; ``timed_out`` lists the
    rules this run marked ``timeout``.
    """

    categorised: int
    by_rule: dict[int, int] = field(default_factory=dict)
    timed_out: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class PreviewSample:
    """One uncategorised booking a pattern would hit."""

    id: int
    date: date
    text: str
    amount_cents: int


@dataclass(frozen=True)
class Preview:
    """How many uncategorised bookings a pattern matches, with a few examples."""

    count: int
    samples: list[PreviewSample]


@dataclass(frozen=True)
class _Candidate:
    """The part of a booking the engine looks at."""

    id: int
    date: str
    text: str
    counterparty: str | None
    amount_cents: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _rule(row: sqlite3.Row) -> Rule:
    return Rule(
        id=row["id"],
        match_kind=row["match_kind"],
        pattern=row["pattern"],
        category_id=row["category_id"],
        counterparty=row["counterparty"],
        priority=row["priority"],
        invalid_reason=row["invalid_reason"],
        created_at=row["created_at"],
    )


def _optional(value: str | None) -> str | None:
    """Blank optional text is stored as NULL."""
    if value is None:
        return None
    return value.strip() or None


# --- pattern validation ------------------------------------------------------------------------


def repeat_product(pattern: str) -> int:
    """Product of every counted repeat (``{n}``, ``{n,m}``, ``{n,}``) in a regular expression.

    Escaped characters and character classes are skipped. ``{n,m}`` counts as ``m``, ``{n,}`` as
    ``n``, ``{0}`` as 1. Nested counted repeats multiply, and compiling them expands the product,
    so the product bounds the compile cost whatever the nesting looks like.
    """
    product = 1
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "{":
            match = _COUNTED_REPEAT.match(pattern, index)
            if match is not None and (match.group(1) or match.group(3)):
                low, comma, high = match.group(1), match.group(2), match.group(3)
                count = int(high) if comma and high else int(low or high)
                product *= max(count, 1)
                index = match.end()
                continue
        index += 1
    return product


def _search_function(match_kind: MatchKind, pattern: str) -> Callable[[str, float], bool]:
    """Compile ``pattern`` into ``search(text, timeout) -> bool``; raise ``regex.error`` if invalid."""
    if match_kind == "contains":
        needle = pattern.casefold()
        return lambda text, _timeout: needle in text.casefold()
    compiled = regex.compile(pattern, regex.IGNORECASE)
    return lambda text, timeout: compiled.search(text, timeout=timeout) is not None


def check_pattern(match_kind: MatchKind, pattern: str, *, timeout_seconds: float = MATCH_TIMEOUT_SECONDS) -> None:
    """Refuse a pattern that is blank, too long, does not compile, is too costly or is too slow."""
    if not pattern.strip():
        raise RulesError(N_("Enter a pattern."))
    if len(pattern) > PATTERN_MAX_LENGTH:
        raise RulesError(N_("The pattern is longer than %(max)s characters."), max=PATTERN_MAX_LENGTH)
    if match_kind == "contains":
        return
    if repeat_product(pattern) > REPEAT_PRODUCT_MAX:
        raise RulesError(N_("The pattern repeats too much. Use smaller repeat counts."))
    try:
        search = _search_function(match_kind, pattern)
    except regex.error as exc:
        raise RulesError(N_("The regular expression is not valid: %(error)s"), error=str(exc)) from exc
    try:
        for probe in PROBE_TEXTS:
            search(probe, timeout_seconds)
    except TimeoutError as exc:
        raise RulesError(N_("The regular expression is too slow and could block the app. Simplify it.")) from exc


# --- reading and writing rules -----------------------------------------------------------------


def list_rules(conn: sqlite3.Connection) -> list[Rule]:
    """All rules in the order the engine tries them: priority descending, then oldest first."""
    rows = conn.execute("SELECT * FROM rule ORDER BY priority DESC, id ASC")
    return [_rule(row) for row in rows]


def get_rule(conn: sqlite3.Connection, rule_id: int) -> Rule:
    """Return the rule or raise ``NotFound``."""
    row = conn.execute("SELECT * FROM rule WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise NotFound(N_("Rule not found."))
    return _rule(row)


def _validated(conn: sqlite3.Connection, fields: RuleFields, timeout_seconds: float) -> RuleFields:
    check_pattern(fields.match_kind, fields.pattern, timeout_seconds=timeout_seconds)
    accounts.get_category(conn, fields.category_id)
    return RuleFields(
        fields.match_kind, fields.pattern.strip(), fields.category_id, _optional(fields.counterparty), fields.priority
    )


def create_rule(
    conn: sqlite3.Connection, fields: RuleFields, *, timeout_seconds: float = MATCH_TIMEOUT_SECONDS
) -> Rule:
    """Store a rule after validating pattern and category; a duplicate raises ``Conflict``."""
    clean = _validated(conn, fields, timeout_seconds)
    try:
        row = conn.execute(
            "INSERT INTO rule (match_kind, pattern, category_id, counterparty, priority, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (clean.match_kind, clean.pattern, clean.category_id, clean.counterparty, clean.priority, _now()),
        ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise Conflict(N_("This rule already exists.")) from exc
    return get_rule(conn, int(row["id"]))


def create_rule_and_apply(
    conn: sqlite3.Connection, fields: RuleFields, *, timeout_seconds: float = MATCH_TIMEOUT_SECONDS
) -> tuple[Rule, ApplyResult]:
    """Store a rule and apply all rules to the uncategorised bookings at once (UC step 5)."""
    rule = create_rule(conn, fields, timeout_seconds=timeout_seconds)
    return get_rule(conn, rule.id), apply_rules(conn, timeout_seconds=timeout_seconds)


def update_rule(
    conn: sqlite3.Connection, rule_id: int, fields: RuleFields, *, timeout_seconds: float = MATCH_TIMEOUT_SECONDS
) -> Rule:
    """Replace a rule's fields; a pattern that passes validation clears the invalid mark."""
    get_rule(conn, rule_id)
    clean = _validated(conn, fields, timeout_seconds)
    try:
        conn.execute(
            "UPDATE rule SET match_kind = ?, pattern = ?, category_id = ?, counterparty = ?, priority = ?,"
            " invalid_reason = NULL WHERE id = ?",
            (clean.match_kind, clean.pattern, clean.category_id, clean.counterparty, clean.priority, rule_id),
        )
    except sqlite3.IntegrityError as exc:
        raise Conflict(N_("This rule already exists.")) from exc
    return get_rule(conn, rule_id)


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    """Delete a rule; bookings it categorised keep their category."""
    get_rule(conn, rule_id)
    conn.execute("DELETE FROM rule WHERE id = ?", (rule_id,))


def draft_from_transaction(conn: sqlite3.Connection, transaction_id: int) -> RuleFields:
    """The rule the user is offered after categorising a booking: ``contains <text>`` → its category.

    The pattern is the booking's text, else its counterparty. A booking without category, without
    any text, or a transfer leg cannot be the basis of a rule.
    """
    item = transactions.get_transaction(conn, transaction_id)
    if item.transfer_group_id is not None:
        raise RulesError(N_("A transfer cannot be the basis of a rule."))
    if item.category_id is None:
        raise RulesError(N_("Choose a category for the booking first."))
    pattern = item.text.strip() or (item.counterparty or "").strip()
    if not pattern:
        raise RulesError(N_("The booking has no text to make a rule from."))
    return RuleFields("contains", pattern, item.category_id, item.counterparty)


# --- the engine --------------------------------------------------------------------------------


@dataclass
class _Compiled:
    """A rule ready to match; ``state`` becomes ``timeout`` when a search exceeded the limit."""

    rule: Rule
    search: Callable[[str, float], bool]
    state: Literal["ok", "timeout"] = "ok"


def _compile_rules(conn: sqlite3.Connection) -> list[_Compiled]:
    """Valid rules in trying order. A rule that no longer compiles is marked ``syntax`` and left out."""
    compiled: list[_Compiled] = []
    for rule in list_rules(conn):
        if rule.invalid_reason is not None:
            continue
        try:
            compiled.append(_Compiled(rule, _search_function(rule.match_kind, rule.pattern)))
        except regex.error:
            conn.execute("UPDATE rule SET invalid_reason = 'syntax' WHERE id = ?", (rule.id,))
    return compiled


def _matches(entry: _Compiled, candidate: _Candidate, timeout_seconds: float) -> bool:
    """Does the rule match text or counterparty? A timeout marks the entry and counts as no match."""
    try:
        for haystack in (candidate.text, candidate.counterparty or ""):
            if entry.search(haystack[:MATCH_TEXT_MAX], timeout_seconds):
                return True
    except TimeoutError:
        entry.state = "timeout"
    return False


def _candidates(
    conn: sqlite3.Connection, transaction_ids: list[int] | None, only_uncategorized: bool
) -> list[_Candidate]:
    """Non-transfer bookings: all of them, or just the given ids; only uncategorised ones if asked."""
    where = "transfer_group_id IS NULL" + (" AND category_id IS NULL" if only_uncategorized else "")
    select = 'SELECT id, date, text, counterparty, amount_cents FROM "transaction" WHERE ' + where  # noqa: S608  # nosec B608 — constant clauses
    if transaction_ids is None:
        rows = list(conn.execute(select + " ORDER BY id"))
    else:
        rows = []
        for start in range(0, len(transaction_ids), ID_CHUNK):
            chunk = transaction_ids[start : start + ID_CHUNK]
            marks = ",".join("?" * len(chunk))
            rows.extend(conn.execute(f"{select} AND id IN ({marks}) ORDER BY id", chunk))  # nosec B608 — only "?" placeholders are joined
    return [_Candidate(r["id"], r["date"], r["text"], r["counterparty"], r["amount_cents"]) for r in rows]


def _winner(compiled: list[_Compiled], candidate: _Candidate, timeout_seconds: float) -> _Compiled | None:
    """The first still-valid rule (priority order) that matches."""
    for entry in compiled:
        if entry.state == "ok" and _matches(entry, candidate, timeout_seconds):
            return entry
    return None


def apply_rules(
    conn: sqlite3.Connection,
    transactions_to_check: Iterable[transactions.Transaction] | None = None,
    *,
    only_uncategorized: bool = True,
    timeout_seconds: float = MATCH_TIMEOUT_SECONDS,
) -> ApplyResult:
    """Categorise bookings with the winning rule (highest priority, then oldest) and report what changed.

    ``transactions_to_check`` limits the run to those bookings (the CSV import passes the rows it
    just wrote); ``None`` means every booking in the database. Transfer legs are never touched.
    With ``only_uncategorized`` (the default) a booking that already has a category is left alone;
    the ``UPDATE`` repeats the condition, so a stale caller list cannot overwrite a category.
    A rule whose search exceeds ``timeout_seconds`` is marked ``timeout`` and skipped from then on.
    """
    ids = None if transactions_to_check is None else [item.id for item in transactions_to_check]
    if ids == []:
        return ApplyResult(0)
    compiled = _compile_rules(conn)
    by_rule: dict[int, int] = {}
    with transaction(conn):
        for candidate in _candidates(conn, ids, only_uncategorized):
            winner = _winner(compiled, candidate, timeout_seconds)
            if winner is None:
                continue
            cursor = conn.execute(
                'UPDATE "transaction" SET category_id = ?, counterparty = coalesce(counterparty, ?)'
                " WHERE id = ? AND transfer_group_id IS NULL AND (? = 0 OR category_id IS NULL)",
                (winner.rule.category_id, winner.rule.counterparty, candidate.id, int(only_uncategorized)),
            )
            if cursor.rowcount:
                by_rule[winner.rule.id] = by_rule.get(winner.rule.id, 0) + 1
        timed_out = [entry.rule.id for entry in compiled if entry.state == "timeout"]
        for rule_id in timed_out:
            conn.execute("UPDATE rule SET invalid_reason = 'timeout' WHERE id = ?", (rule_id,))
    return ApplyResult(sum(by_rule.values()), by_rule, timed_out)


def preview_rule(
    conn: sqlite3.Connection,
    match_kind: MatchKind,
    pattern: str,
    *,
    timeout_seconds: float = MATCH_TIMEOUT_SECONDS,
) -> Preview:
    """How many uncategorised bookings the pattern matches (before saving it), with a few examples.

    The count looks at this pattern alone; other rules with a higher priority may still take some
    of these bookings when the rule is applied.
    """
    check_pattern(match_kind, pattern, timeout_seconds=timeout_seconds)
    entry = _Compiled(
        Rule(match_kind, pattern.strip(), 0, id=0, created_at=""), _search_function(match_kind, pattern.strip())
    )
    hits: list[_Candidate] = []
    for candidate in _candidates(conn, None, True):
        if _matches(entry, candidate, timeout_seconds):
            hits.append(candidate)
        if entry.state == "timeout":
            raise RulesError(N_("The regular expression is too slow and could block the app. Simplify it."))
    samples = [
        PreviewSample(c.id, date.fromisoformat(c.date), c.text, c.amount_cents) for c in hits[:PREVIEW_SAMPLE_LIMIT]
    ]
    return Preview(len(hits), samples)
