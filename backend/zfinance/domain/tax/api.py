"""``/api/settings/tax``: the tax plan of UC-ZF-008.

``GET`` returns the stored settings with the current plan items and the ``plan_missing`` flag (the
dashboard hint "Taxes & levies not set"); ``PUT`` replaces the settings and brings the recurring
items in line. Amounts are integer cents (``*_cents``) and refused as floats; due dates are
``MM-DD`` (day 31 = last day of the month). There is no VAT field (small business).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.recurring.service import RecurringError
from zfinance.domain.tax import service
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import PositiveCents

router = APIRouter(prefix="/api/settings/tax", tags=["tax"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
DueDate = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5)]
Errors = (service.TaxError, AccountsError, RecurringError)


def _fail(exc: service.TaxError | AccountsError | RecurringError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


class TaxIn(BaseModel):
    """Body of PUT: the business account, the amount per due date (null = not entered) and the due dates."""

    business_account_id: int | None = None
    est_vz_quarter_cents: PositiveCents | None = None
    svs_quarter_cents: PositiveCents | None = None
    est_due_dates: list[DueDate] = list(service.DEFAULT_EST_DUE_DATES)
    svs_due_dates: list[DueDate] = list(service.DEFAULT_SVS_DUE_DATES)


class TermOut(BaseModel):
    """One current plan item: a due date with its recurring item and the next planned date."""

    kind: service.Kind
    due: str
    recurring_id: int
    amount_cents: int
    next_due_date: date | None
    planned_count: int


class TaxOut(BaseModel):
    """The stored settings, the current plan items and whether no plan exists yet."""

    business_account_id: int | None
    est_vz_quarter_cents: int | None
    svs_quarter_cents: int | None
    est_due_dates: list[str]
    svs_due_dates: list[str]
    plan_missing: bool
    terms: list[TermOut]


def _out(settings: service.TaxSettings) -> TaxOut:
    return TaxOut(
        business_account_id=settings.business_account_id,
        est_vz_quarter_cents=settings.est_vz_quarter_cents,
        svs_quarter_cents=settings.svs_quarter_cents,
        est_due_dates=list(settings.est_due_dates),
        svs_due_dates=list(settings.svs_due_dates),
        plan_missing=settings.plan_missing,
        terms=[
            TermOut(
                kind=term.kind,
                due=term.due,
                recurring_id=term.recurring_id,
                amount_cents=term.amount_cents,
                next_due_date=term.next_due_date,
                planned_count=term.planned_count,
            )
            for term in settings.terms
        ],
    )


@router.get("")
def get_tax_settings(conn: Conn) -> TaxOut:
    """The tax settings with the current plan items."""
    return _out(service.get_settings(conn))


@router.put("")
def put_tax_settings(body: TaxIn, conn: Conn) -> TaxOut:
    """Store the tax settings; the recurring items follow (a changed amount applies from the next due date)."""
    fields = service.TaxFields(
        business_account_id=body.business_account_id,
        est_vz_quarter_cents=body.est_vz_quarter_cents,
        svs_quarter_cents=body.svs_quarter_cents,
        est_due_dates=tuple(body.est_due_dates),
        svs_due_dates=tuple(body.svs_due_dates),
    )
    try:
        settings = service.save_settings(conn, fields)
    except Errors as exc:
        _fail(exc)
    return _out(settings)
