"""``/api/reports``: the monthly report of UC-ZF-007.

``GET /month`` (figures and lines), ``GET /month/bookings`` (the bookings behind a line, or the
uncategorised list when ``category_id`` is left out) and ``GET /month/export.csv``. ``month`` is
``YYYY-MM``, ``area`` is ``private``, ``business`` or ``all``. Amounts are signed integer cents.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from zfinance.db import get_connection
from zfinance.domain.reports import export, service
from zfinance.domain.reports.service import Area, Flow
from zfinance.i18n.errors import LocalizedHTTPException

router = APIRouter(prefix="/api/reports", tags=["reports"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
MonthParam = Annotated[str, Query(description="YYYY-MM", max_length=7)]
AreaParam = Annotated[Area, Query()]


def _fail(exc: service.ReportsError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


class ComparisonOut(BaseModel):
    """An amount with the previous month and the six-month average."""

    amount_cents: int
    previous_cents: int
    average_6m_cents: int


class LineOut(BaseModel):
    """Income or expense of one category (``category_id`` null = uncategorised)."""

    flow: Flow
    category_id: int | None
    category_name: str | None
    parent_name: str | None
    booking_count: int
    comparison: ComparisonOut


class MonthReportOut(BaseModel):
    """One month in one area."""

    month: str
    area: Area
    income: ComparisonOut
    expense: ComparisonOut
    balance: ComparisonOut
    movement_in_cents: int
    movement_out_cents: int
    uncategorized_count: int
    lines: list[LineOut]


class BookingOut(BaseModel):
    """A booking behind a line; the fields an edit of the booking needs."""

    id: int
    date: str
    amount_cents: int
    account_id: int
    account_name: str
    category_id: int | None
    text: str
    counterparty: str | None
    memo: str | None


def _comparison(value: service.Comparison) -> ComparisonOut:
    return ComparisonOut(
        amount_cents=value.amount_cents, previous_cents=value.previous_cents, average_6m_cents=value.average_6m_cents
    )


@router.get("/month")
def month_report(conn: Conn, month: MonthParam, area: AreaParam = "all") -> MonthReportOut:
    """Income, expense and balance of the month with a line per category and flow."""
    try:
        report = service.month_report(conn, month, area)
    except service.ReportsError as exc:
        _fail(exc)
    return MonthReportOut(
        month=report.month,
        area=report.area,
        income=_comparison(report.income),
        expense=_comparison(report.expense),
        balance=_comparison(report.balance),
        movement_in_cents=report.movement_in_cents,
        movement_out_cents=report.movement_out_cents,
        uncategorized_count=report.uncategorized_count,
        lines=[
            LineOut(
                flow=line.flow,
                category_id=line.category_id,
                category_name=line.category_name,
                parent_name=line.parent_name,
                booking_count=line.booking_count,
                comparison=_comparison(line.comparison),
            )
            for line in report.lines
        ],
    )


@router.get("/month/bookings")
def month_bookings(
    conn: Conn,
    month: MonthParam,
    area: AreaParam = "all",
    category_id: Annotated[int | None, Query(description="omit for the uncategorised bookings")] = None,
    flow: Annotated[Flow | None, Query()] = None,
) -> list[BookingOut]:
    """The bookings behind a line (``category_id`` + ``flow``) or the uncategorised list."""
    try:
        items = service.bookings(conn, month, area, category_id=category_id, flow=flow)
    except service.ReportsError as exc:
        _fail(exc)
    return [
        BookingOut(
            id=item.id,
            date=item.date.isoformat(),
            amount_cents=item.amount_cents,
            account_id=item.account_id,
            account_name=item.account_name,
            category_id=item.category_id,
            text=item.text,
            counterparty=item.counterparty,
            memo=item.memo,
        )
        for item in items
    ]


@router.get("/month/export.csv")
def month_export(conn: Conn, month: MonthParam, area: AreaParam = "all") -> Response:
    """The month report as a CSV download (machine-readable: ``.`` decimals, UTF-8, English header)."""
    try:
        report = service.month_report(conn, month, area)
    except service.ReportsError as exc:
        _fail(exc)
    return Response(
        content=export.month_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="z-finance-{report.month}-{report.area}.csv"'},
    )
