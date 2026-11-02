"""``/api/pots``: savings pots of UC-ZF-011.

The overview under ``/api/pots`` (pots with their figures plus each account's split into pots,
free balance and "available in an emergency"), one pot under ``/api/pots/{id}`` (get/put/delete)
with its history (``/allocations``), deposits, withdrawals, assigned expenses, closing and the
savings plan. Account-wide actions have fixed paths declared before ``/{pot_id}``: the emergency
order (``/ranks``), moving between pots (``/transfers``), the free balance (``/free-balance``) and
the emergency proposal and its confirmation (``/emergency``).

Amounts are integer cents (``*_cents``) and refused as floats; dates are ``YYYY-MM-DD``. "Today"
is the server's local date.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError, Area
from zfinance.domain.pots import service
from zfinance.domain.recurring.service import RecurringError
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import PositiveCents

router = APIRouter(prefix="/api/pots", tags=["pots"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
# A field named ``date`` with a default shadows the type inside its class body.
Day = date
Errors = (service.PotsError, AccountsError, TransactionsError, RecurringError)


def _fail(exc: service.PotsError | AccountsError | TransactionsError | RecurringError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


# --- models ------------------------------------------------------------------------------------


class PotIn(BaseModel):
    """Body of create and update (full replacement). ``area`` omitted = the account's area; the
    account cannot change on update. ``plan_expense`` needs ``target_amount_cents`` and ``target_date``."""

    name: Name
    account_id: int
    area: Area | None = None
    target_amount_cents: PositiveCents | None = None
    target_date: date | None = None
    plan_expense: bool = False
    locked: bool = False
    is_levy_pot: bool = False

    def fields(self) -> service.PotFields:
        """The service's input value."""
        return service.PotFields(**self.model_dump())


class PotOut(BaseModel):
    """A pot with its figures today. ``status``: ``on_track`` | ``behind`` | ``reached`` (``null`` without
    target, or without target date while not reached); ``monthly_rate_cents`` needs target and date."""

    id: int
    name: str
    account_id: int
    area: Area
    target_amount_cents: int | None
    target_date: date | None
    plan_expense: bool
    locked: bool
    is_levy_pot: bool
    emergency_rank: int | None
    plan_start: date
    planned_transaction_id: int | None
    closed: bool
    closed_at: str | None
    balance_cents: int
    progress_pct: int | None
    monthly_rate_cents: int | None
    status: service.PotStatus | None
    expected_balance_cents: int | None
    savings_plan_id: int | None
    savings_plan_amount_cents: int | None
    expected_reach: date | None


class AccountPotsOut(BaseModel):
    """An account's balance split into pots and free balance, plus the unlocked pots' sum."""

    account_id: int
    balance_cents: int
    pots_cents: int
    free_cents: int
    emergency_available_cents: int


class OverviewOut(BaseModel):
    """Pots (open first, by account in emergency order) and the accounts they are on."""

    pots: list[PotOut]
    accounts: list[AccountPotsOut]


class AllocationOut(BaseModel):
    """One movement of the pot (+ in, - out); ``transaction_id`` links a booking (deposit transfer, expense)."""

    id: int
    date: date
    amount_cents: int
    kind: service.AllocationKind
    transaction_id: int | None
    transfer_group_id: str | None
    memo: str | None


class DepositIn(BaseModel):
    """Money into the pot: from the free balance, or with ``from_account_id`` as a booked transfer."""

    amount_cents: PositiveCents
    date: Day | None = None
    from_account_id: int | None = None
    memo: Memo | None = None


class WithdrawalIn(BaseModel):
    """Money back to the free balance; a locked pot needs ``confirm_locked`` (logged as emergency)."""

    amount_cents: PositiveCents
    date: Day | None = None
    confirm_locked: bool = False
    memo: Memo | None = None


class ExpenseIn(BaseModel):
    """A booked expense on the pot's account that the pot pays for."""

    transaction_id: int
    confirm_locked: bool = False


class ExpenseOut(BaseModel):
    """The pot afterwards, the part it covered and the part that came from the free balance."""

    pot: PotOut
    covered_cents: int
    from_free_cents: int


class MoveIn(BaseModel):
    """Move money between two pots of the same account (no booking)."""

    from_pot_id: int
    to_pot_id: int
    amount_cents: PositiveCents
    date: Day | None = None
    confirm_locked: bool = False


class RanksIn(BaseModel):
    """The new emergency order of an account: every open, unlocked pot once, first = rank 1."""

    account_id: int
    pot_ids: list[int]


class FreeBalanceOut(BaseModel):
    """The account's free balance on a day: balance minus all its pots."""

    account_id: int
    date: date
    free_balance_cents: int


class EmergencyLineOut(BaseModel):
    """One pot of the proposal: amount taken, new balance, target date before and after."""

    pot_id: int
    name: str
    rank: int
    amount_cents: int
    new_balance_cents: int
    target_date: date | None
    shifted_target_date: date | None


class EmergencyProposalOut(BaseModel):
    """What the pots would give in rank order; ``shortfall_cents`` is what they cannot cover."""

    account_id: int
    amount_cents: int
    lines: list[EmergencyLineOut]
    covered_cents: int
    shortfall_cents: int


class EmergencyLineIn(BaseModel):
    """One confirmed (or hand-edited) line of an emergency withdrawal."""

    pot_id: int
    amount_cents: PositiveCents


class EmergencyIn(BaseModel):
    """The confirmed emergency withdrawal on one account."""

    account_id: int
    lines: list[EmergencyLineIn]


class SavingsPlanIn(BaseModel):
    """Monthly transfer into the pot's account; amount defaults to the monthly rate, start to next month."""

    from_account_id: int
    amount_cents: PositiveCents | None = None
    start_date: date | None = None


def _pot_out(pot: service.Pot) -> PotOut:
    return PotOut(
        id=pot.id,
        name=pot.name,
        account_id=pot.account_id,
        area=pot.area or "private",
        target_amount_cents=pot.target_amount_cents,
        target_date=pot.target_date,
        plan_expense=pot.plan_expense,
        locked=pot.locked,
        is_levy_pot=pot.is_levy_pot,
        emergency_rank=pot.emergency_rank,
        plan_start=pot.plan_start,
        planned_transaction_id=pot.planned_transaction_id,
        closed=pot.closed_at is not None,
        closed_at=pot.closed_at,
        balance_cents=pot.balance_cents,
        progress_pct=pot.progress_pct,
        monthly_rate_cents=pot.monthly_rate_cents,
        status=pot.status,
        expected_balance_cents=pot.expected_balance_cents,
        savings_plan_id=pot.savings_plan_id,
        savings_plan_amount_cents=pot.savings_plan_amount_cents,
        expected_reach=pot.expected_reach,
    )


def _overview_out(result: service.Overview) -> OverviewOut:
    return OverviewOut(
        pots=[_pot_out(pot) for pot in result.pots],
        accounts=[AccountPotsOut(**vars(split)) for split in result.accounts],
    )


# --- fixed paths -------------------------------------------------------------------------------


@router.get("")
def list_pots(conn: Conn, account_id: int | None = None) -> OverviewOut:
    """All pots (or those of one account) with each account's split."""
    return _overview_out(service.overview(conn, account_id=account_id))


@router.post("", status_code=201)
def create_pot(body: PotIn, conn: Conn) -> PotOut:
    """Create a pot; an unlocked pot joins the end of its account's emergency order."""
    try:
        pot = service.create_pot(conn, body.fields())
    except Errors as exc:
        _fail(exc)
    return _pot_out(pot)


@router.put("/ranks")
def put_ranks(body: RanksIn, conn: Conn) -> OverviewOut:
    """Set the emergency order of an account."""
    try:
        result = service.set_ranks(conn, body.account_id, body.pot_ids)
    except Errors as exc:
        _fail(exc)
    return _overview_out(result)


@router.post("/transfers")
def move(body: MoveIn, conn: Conn) -> list[PotOut]:
    """Move money between two pots of one account; answers both pots (source, target)."""
    try:
        source, target = service.move(
            conn, body.from_pot_id, body.to_pot_id, body.amount_cents, on=body.date, confirm_locked=body.confirm_locked
        )
    except Errors as exc:
        _fail(exc)
    return [_pot_out(source), _pot_out(target)]


@router.get("/free-balance")
def get_free_balance(conn: Conn, account_id: int, on: Annotated[date | None, Query()] = None) -> FreeBalanceOut:
    """The account's free balance at the end of ``on`` (default today)."""
    day = on or date.today()
    try:
        service.collect_deposits(conn)
        cents = service.free_balance(conn, account_id, day)
    except Errors as exc:
        _fail(exc)
    return FreeBalanceOut(account_id=account_id, date=day, free_balance_cents=cents)


@router.get("/emergency")
def get_emergency(conn: Conn, account_id: int, amount_cents: Annotated[int, Query(gt=0)]) -> EmergencyProposalOut:
    """Proposal: take ``amount_cents`` from the account's pots in rank order, locked pots never."""
    try:
        proposal = service.emergency_proposal(conn, account_id, amount_cents)
    except Errors as exc:
        _fail(exc)
    return EmergencyProposalOut(
        account_id=proposal.account_id,
        amount_cents=proposal.amount_cents,
        lines=[EmergencyLineOut(**vars(line)) for line in proposal.lines],
        covered_cents=proposal.covered_cents,
        shortfall_cents=proposal.shortfall_cents,
    )


@router.post("/emergency")
def post_emergency(body: EmergencyIn, conn: Conn) -> list[PotOut]:
    """Take the confirmed emergency amounts; target dates move as proposed."""
    try:
        pots = service.confirm_emergency(
            conn, body.account_id, [(line.pot_id, line.amount_cents) for line in body.lines]
        )
    except Errors as exc:
        _fail(exc)
    return [_pot_out(pot) for pot in pots]


# --- one pot -----------------------------------------------------------------------------------


@router.get("/{pot_id}")
def get_pot(pot_id: int, conn: Conn) -> PotOut:
    """One pot."""
    try:
        pot = service.get_pot(conn, pot_id)
    except Errors as exc:
        _fail(exc)
    return _pot_out(pot)


@router.put("/{pot_id}")
def update_pot(pot_id: int, body: PotIn, conn: Conn) -> PotOut:
    """Replace a pot's fields; a changed target restarts the plan today."""
    try:
        pot = service.update_pot(conn, pot_id, body.fields())
    except Errors as exc:
        _fail(exc)
    return _pot_out(pot)


@router.delete("/{pot_id}", status_code=204)
def delete_pot(pot_id: int, conn: Conn) -> Response:
    """Delete a pot with its history; its money returns to the free balance."""
    try:
        service.delete_pot(conn, pot_id)
    except Errors as exc:
        _fail(exc)
    return Response(status_code=204)


@router.get("/{pot_id}/allocations")
def get_allocations(pot_id: int, conn: Conn) -> list[AllocationOut]:
    """The pot's movements, newest first."""
    try:
        rows = service.allocations(conn, pot_id)
    except Errors as exc:
        _fail(exc)
    return [AllocationOut(**{k: v for k, v in vars(row).items() if k != "pot_id"}) for row in rows]


@router.post("/{pot_id}/deposits", status_code=201)
def deposit(pot_id: int, body: DepositIn, conn: Conn) -> PotOut:
    """Put money into the pot; above the free balance it is refused."""
    try:
        pot = service.deposit(
            conn, pot_id, body.amount_cents, on=body.date, from_account_id=body.from_account_id, memo=body.memo
        )
    except Errors as exc:
        _fail(exc)
    return _pot_out(pot)


@router.post("/{pot_id}/withdrawals", status_code=201)
def withdraw(pot_id: int, body: WithdrawalIn, conn: Conn) -> PotOut:
    """Release money from the pot back to the free balance."""
    try:
        pot = service.withdraw(
            conn, pot_id, body.amount_cents, on=body.date, confirm_locked=body.confirm_locked, memo=body.memo
        )
    except Errors as exc:
        _fail(exc)
    return _pot_out(pot)


@router.post("/{pot_id}/expenses", status_code=201)
def assign_expense(pot_id: int, body: ExpenseIn, conn: Conn) -> ExpenseOut:
    """Let the pot pay for a booked expense on its account, up to its balance."""
    try:
        result = service.assign_expense(conn, pot_id, body.transaction_id, confirm_locked=body.confirm_locked)
    except Errors as exc:
        _fail(exc)
    return ExpenseOut(
        pot=_pot_out(result.pot), covered_cents=result.covered_cents, from_free_cents=result.from_free_cents
    )


@router.post("/{pot_id}/close")
def close_pot(pot_id: int, conn: Conn) -> PotOut:
    """Close the pot; its rest returns to the free balance."""
    try:
        pot = service.close_pot(conn, pot_id)
    except Errors as exc:
        _fail(exc)
    return _pot_out(pot)


@router.put("/{pot_id}/savings-plan")
def put_savings_plan(pot_id: int, body: SavingsPlanIn, conn: Conn) -> PotOut:
    """Create or replace the pot's monthly savings transfer."""
    try:
        pot = service.set_savings_plan(
            conn, pot_id, body.from_account_id, amount_cents=body.amount_cents, start_date=body.start_date
        )
    except Errors as exc:
        _fail(exc)
    return _pot_out(pot)


@router.delete("/{pot_id}/savings-plan")
def delete_savings_plan(pot_id: int, conn: Conn) -> PotOut:
    """End the pot's savings plan; booked deposits stay with the pot."""
    try:
        pot = service.delete_savings_plan(conn, pot_id)
    except Errors as exc:
        _fail(exc)
    return _pot_out(pot)
