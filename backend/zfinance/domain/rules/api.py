"""``/api/rules``: categorisation rules of UC-ZF-007.

The fixed paths (``/preview``, ``/apply``, ``/draft``) are declared before ``/{rule_id}``.
Creating a rule applies all rules to the uncategorised bookings at once and answers with the
number this rule categorised (UC step 5).
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, StrictInt, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.rules import service
from zfinance.domain.rules.service import InvalidReason, MatchKind
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException

router = APIRouter(prefix="/api/rules", tags=["rules"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Pattern = Annotated[str, StringConstraints(strip_whitespace=True, max_length=service.PATTERN_MAX_LENGTH)]
Counterparty = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]

Failure = service.RulesError | AccountsError | TransactionsError


def _fail(exc: Failure) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class RuleIn(BaseModel):
    """Body of create and update (full replacement); also the shape of a draft."""

    match_kind: MatchKind
    pattern: Pattern
    category_id: int
    counterparty: Counterparty | None = None
    priority: StrictInt = 0

    def fields(self) -> service.RuleFields:
        """The service's input value."""
        return service.RuleFields(**self.model_dump())


class RuleOut(RuleIn):
    """A stored rule; ``invalid_reason`` is set for a rule the engine skips."""

    id: int
    invalid_reason: InvalidReason | None
    created_at: str


class ApplyOut(BaseModel):
    """Result of applying rules: bookings categorised in total and per rule id."""

    categorised: int
    by_rule: dict[int, int]
    timed_out: list[int]


class CreatedOut(BaseModel):
    """A new rule and how many bookings it categorised on creation."""

    rule: RuleOut
    applied_count: int


class PreviewIn(BaseModel):
    """A pattern to try before saving it."""

    match_kind: MatchKind
    pattern: Pattern


class PreviewSampleOut(BaseModel):
    """An uncategorised booking the pattern would hit."""

    id: int
    date: str
    text: str
    amount_cents: int


class PreviewOut(BaseModel):
    """Hit count and a few examples."""

    count: int
    samples: list[PreviewSampleOut]


def _out(rule: service.Rule) -> RuleOut:
    return RuleOut(
        match_kind=rule.match_kind,
        pattern=rule.pattern,
        category_id=rule.category_id,
        counterparty=rule.counterparty,
        priority=rule.priority,
        id=rule.id,
        invalid_reason=rule.invalid_reason,
        created_at=rule.created_at,
    )


def _apply_out(result: service.ApplyResult) -> ApplyOut:
    return ApplyOut(categorised=result.categorised, by_rule=result.by_rule, timed_out=result.timed_out)


# --- fixed paths -------------------------------------------------------------------------------


@router.post("/preview")
def preview(body: PreviewIn, conn: Conn) -> PreviewOut:
    """How many uncategorised bookings a pattern would hit; nothing is stored."""
    try:
        result = service.preview_rule(conn, body.match_kind, body.pattern)
    except service.RulesError as exc:
        _fail(exc)
    samples = [
        PreviewSampleOut(id=s.id, date=s.date.isoformat(), text=s.text, amount_cents=s.amount_cents)
        for s in result.samples
    ]
    return PreviewOut(count=result.count, samples=samples)


@router.post("/apply")
def apply_all(conn: Conn) -> ApplyOut:
    """Apply all valid rules to every uncategorised booking (never overwrites a category)."""
    return _apply_out(service.apply_rules(conn))


@router.get("/draft")
def draft(conn: Conn, transaction_id: Annotated[int, Query()]) -> RuleIn:
    """The rule offered after categorising a booking: contains its text → its category."""
    try:
        fields = service.draft_from_transaction(conn, transaction_id)
    except (service.RulesError, TransactionsError) as exc:
        _fail(exc)
    return RuleIn(
        match_kind=fields.match_kind,
        pattern=fields.pattern,
        category_id=fields.category_id,
        counterparty=fields.counterparty,
        priority=fields.priority,
    )


# --- rules -------------------------------------------------------------------------------------


@router.get("")
def list_rules(conn: Conn) -> list[RuleOut]:
    """All rules in the order the engine tries them (priority descending, oldest first)."""
    return [_out(rule) for rule in service.list_rules(conn)]


@router.post("", status_code=201)
def create_rule(body: RuleIn, conn: Conn) -> CreatedOut:
    """Create a rule and apply the rules to the uncategorised bookings right away."""
    try:
        rule, result = service.create_rule_and_apply(conn, body.fields())
    except (service.RulesError, AccountsError) as exc:
        _fail(exc)
    return CreatedOut(rule=_out(rule), applied_count=result.by_rule.get(rule.id, 0))


@router.put("/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, conn: Conn) -> RuleOut:
    """Replace a rule; a valid pattern clears an invalid mark. Nothing is applied."""
    try:
        rule = service.update_rule(conn, rule_id, body.fields())
    except (service.RulesError, AccountsError) as exc:
        _fail(exc)
    return _out(rule)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, conn: Conn) -> Response:
    """Delete a rule; the bookings it categorised keep their category."""
    try:
        service.delete_rule(conn, rule_id)
    except service.RulesError as exc:
        _fail(exc)
    return Response(status_code=204)
