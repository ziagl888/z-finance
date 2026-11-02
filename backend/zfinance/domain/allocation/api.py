"""``/api/allocation``: the allocation rules of UC-ZF-014.

``GET /api/allocation`` returns what the screen shows — the rules, the open proposals with their
resolved lines, the reversal suggestions of a cancelled income, the pots to choose from and the free
amount (it creates the missing proposals first, like ``/api/payoff`` syncs). The rules are
maintained under ``/api/allocation/rules``; a proposal is decided with
``/api/allocation/proposals/{id}/confirm`` (optionally with edited line amounts) or ``/skip``.

Amounts are integer cents (``*_cents``) and refused as floats; percentages are basis points
(2000 = 20 %). "Today" is the server's local date.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import date
from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.allocation import service
from zfinance.domain.allocation.lines import FULL_BP, MAX_TARGETS, Target
from zfinance.domain.debts.service import DebtsError
from zfinance.domain.levy.service import LevyError
from zfinance.domain.pots.service import PotsError
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import NonNegativeCents, PositiveCents

router = APIRouter(prefix="/api/allocation", tags=["allocation"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Errors = (service.AllocationError, AccountsError, PotsError, DebtsError, TransactionsError, LevyError)
AnyError = service.AllocationError | AccountsError | PotsError | DebtsError | TransactionsError | LevyError


def _fail(exc: AnyError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class TargetIn(BaseModel):
    """One share of a rule: ``percent_bp`` (2000 = 20 %) **or** ``fixed_cents``, not both."""

    kind: Literal["debt_plan", "pot", "reserve"]
    pot_id: StrictInt | None = None
    percent_bp: Annotated[StrictInt, Field(ge=0, le=FULL_BP)] | None = None
    fixed_cents: PositiveCents | None = None


class RuleIn(BaseModel):
    """Body of POST/PUT ``/rules`` (full replacement)."""

    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    area: Literal["private", "business"] | None = None
    category_id: StrictInt | None = None
    min_amount_cents: NonNegativeCents = 0
    targets: Annotated[list[TargetIn], Field(min_length=1, max_length=MAX_TARGETS)]
    fallback_kind: Literal["pot", "reserve"] = "reserve"
    fallback_pot_id: StrictInt | None = None
    active: StrictBool = True

    def fields(self) -> service.RuleFields:
        """The service's fields for this body."""
        return service.RuleFields(
            name=self.name,
            area=self.area,
            category_id=self.category_id,
            min_amount_cents=self.min_amount_cents,
            targets=tuple(Target(item.kind, item.pot_id, item.percent_bp, item.fixed_cents) for item in self.targets),
            fallback_kind=self.fallback_kind,
            fallback_pot_id=self.fallback_pot_id,
            active=self.active,
        )


class ConfirmIn(BaseModel):
    """Body of the confirmation: the edited amount per line index (the levy line has a floor, AK7)."""

    amounts: dict[StrictInt, NonNegativeCents] = Field(default_factory=dict)


class TargetOut(TargetIn):
    """A stored share of a rule."""


class RuleOut(BaseModel):
    """A stored rule."""

    id: int
    name: str
    area: str | None
    category_id: int | None
    min_amount_cents: int
    targets: list[TargetOut]
    fallback_kind: str
    fallback_pot_id: int | None
    active: bool
    created_at: str


class LineOut(BaseModel):
    """One line of a proposal; ``target`` is empty for the total reserve (the screen names it)."""

    kind: str
    amount_cents: int
    floor_cents: int
    capped: bool
    target: str
    pot_id: int | None
    debt_id: int | None
    editable: bool
    books: bool


class ProposalOut(BaseModel):
    """A proposal with its lines (UC step 3); ``overdue`` after seven open days (AK5)."""

    id: int
    transaction_id: int | None
    rule_id: int | None
    rule_name: str | None
    amount_cents: int
    text: str
    account_id: int
    account_name: str
    date: date
    status: str
    lines: list[LineOut]
    remaining_cents: int
    bookable_cents: int
    free_cents: int
    reserve_warning: bool
    reduced_total_cents: int
    days_open: int
    overdue: bool
    created_at: str
    confirmed_at: str | None


class ReversalOut(BaseModel):
    """A confirmed proposal whose income was deleted (UC 2a); nothing is reversed automatically."""

    proposal_id: int
    text: str
    amount_cents: int
    date: date
    booked_cents: int
    transfer_group_ids: list[str]
    confirmed_at: str | None


class PotOut(BaseModel):
    """An open pot the rule editor can choose."""

    id: int
    name: str
    account_id: int
    account_name: str
    is_levy_pot: bool


class OverviewOut(BaseModel):
    """What the allocation screen shows."""

    rules: list[RuleOut]
    proposals: list[ProposalOut]
    reversals: list[ReversalOut]
    pots: list[PotOut]
    levy_rate_bp: int
    levy_pot_id: int | None
    levy_pot_missing: bool
    free_cents: int
    skipped_this_month: int


class ConfirmationOut(BaseModel):
    """Result of a confirmation: the proposal afterwards, the transfers it wrote and their sum."""

    proposal: ProposalOut
    transfer_count: int
    booked_cents: int


# --- routes ------------------------------------------------------------------------------------


@router.get("")
def get_overview(conn: Conn) -> OverviewOut:
    """Rules, open proposals, reversal suggestions, pots and the free amount."""
    try:
        return OverviewOut.model_validate(asdict(service.overview(conn)))
    except Errors as exc:
        _fail(exc)


@router.get("/rules")
def get_rules(conn: Conn) -> list[RuleOut]:
    """All allocation rules."""
    return [RuleOut.model_validate(asdict(rule)) for rule in service.list_rules(conn)]


@router.post("/rules", status_code=201)
def post_rule(body: RuleIn, conn: Conn) -> RuleOut:
    """Create a rule (UC step 1); it acts on incomes booked from now on."""
    try:
        return RuleOut.model_validate(asdict(service.create_rule(conn, body.fields())))
    except Errors as exc:
        _fail(exc)


@router.put("/rules/{rule_id}")
def put_rule(rule_id: int, body: RuleIn, conn: Conn) -> RuleOut:
    """Replace a rule's fields."""
    try:
        return RuleOut.model_validate(asdict(service.update_rule(conn, rule_id, body.fields())))
    except Errors as exc:
        _fail(exc)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, conn: Conn) -> None:
    """Delete a rule; the proposals it made stay."""
    try:
        service.delete_rule(conn, rule_id)
    except Errors as exc:
        _fail(exc)


@router.post("/proposals/{proposal_id}/confirm")
def post_confirm(proposal_id: int, body: ConfirmIn, conn: Conn) -> ConfirmationOut:
    """Book the proposal's lines as transfers (UC steps 4-5); a second call is a 409."""
    try:
        result = service.confirm_proposal(conn, proposal_id, dict(body.amounts))
    except Errors as exc:
        _fail(exc)
    return ConfirmationOut.model_validate(asdict(result))


@router.post("/proposals/{proposal_id}/skip")
def post_skip(proposal_id: int, conn: Conn) -> ProposalOut:
    """Reject a proposal (UC 4a): logged as skipped, never booked."""
    try:
        return ProposalOut.model_validate(asdict(service.skip_proposal(conn, proposal_id)))
    except Errors as exc:
        _fail(exc)
