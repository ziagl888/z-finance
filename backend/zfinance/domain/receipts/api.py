"""``/api/receipts``: receipt photos from the receipt agent, the review list and agent runs (UC-ZF-013).

``POST /api/receipts`` takes ``multipart/form-data``: the file ``photo`` plus the fields the agent
read (all optional, see ``ReceiptForm``). The answer says what happened (``booked``, ``review``,
``duplicate``); 201 when a receipt was stored, 200 when the same photo was already there.

The upload is parsed by hand (``request.form`` with limits) instead of FastAPI form parameters, so
an oversized request is refused from its ``Content-Length`` before anything is read, and the part
count and field sizes are capped. The file name and content type of the upload are ignored: the
type comes from the bytes (``service.detect_photo_type``), the storage path from server values.
Photos are only served by receipt id.

The server is local-only (``LocalOnlyMiddleware``); fields are validated all the same.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from starlette.datastructures import UploadFile

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.receipts import service
from zfinance.domain.receipts.service import Reason, Status
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n import N_
from zfinance.i18n.errors import LocalizedHTTPException
from zfinance.money import MAX_CENTS, MAX_ID, PositiveCents

router = APIRouter(prefix="/api/receipts", tags=["receipts"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Errors = (service.ReceiptsError, AccountsError, TransactionsError)

#: Room for the multipart envelope and the text fields next to the photo.
_ENVELOPE_BYTES = 64 * 1024
_MAX_FIELDS = 20
_MAX_FIELD_BYTES = 32 * 1024


def _fail(exc: service.ReceiptsError | AccountsError | TransactionsError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


def get_data_dir(request: Request) -> Path:
    """Dependency: the data directory the app was created with."""
    return request.app.state.settings.data_dir


DataDir = Annotated[Path, Depends(get_data_dir)]


# --- models ------------------------------------------------------------------------------------

Day = date
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class ReceiptForm(BaseModel):
    """Text fields of the upload. Missing fields make a review item, not an error.

    ``amount_cents`` is the receipt total in cents (positive); ``payment_method`` accepts
    ``cash``/``card``/``unknown`` and the German ``bar``/``karte``/``unbekannt``; ``hints`` is the
    message text; ``line_items`` is JSON text (stored, not evaluated).
    """

    model_config = ConfigDict(extra="ignore")

    date: Day | None = None  # alias: the field name shadows the type inside the class
    amount_cents: Annotated[int, Field(ge=-MAX_CENTS, le=MAX_CENTS)] | None = None
    merchant: Short | None = None
    payment_method: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=20)] = "unknown"
    category_suggestion: Short | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None
    currency: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10)] = "EUR"
    hints: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    unreadable: bool = False
    line_items: Annotated[str, StringConstraints(max_length=_MAX_FIELD_BYTES)] | None = None
    message_ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    run_id: Annotated[int, Field(ge=1, le=MAX_ID)] | None = None

    def extracted(self) -> service.Extracted:
        """The service's input value."""
        line_items: Any = self.line_items
        if line_items is not None:
            # Not JSON: kept as text; the items are stored for reference only.
            with contextlib.suppress(ValueError):
                line_items = json.loads(line_items)
        return service.Extracted(
            date=self.date,
            amount_cents=self.amount_cents,
            merchant=self.merchant or None,
            payment_method=service.normalize_payment_method(self.payment_method),
            category_suggestion=self.category_suggestion or None,
            confidence=self.confidence,
            currency=self.currency or "EUR",
            hints=self.hints,
            unreadable=self.unreadable,
            line_items=line_items,
        )


class SubmitOut(BaseModel):
    """What happened to an uploaded receipt (see ``service.SubmitResult``)."""

    status: Status
    receipt_id: int | None
    transaction_id: int | None
    reason: Reason | None
    duplicate_of: int | None
    matched: bool


class ReceiptOut(BaseModel):
    """A stored receipt; ``extracted`` holds the agent's fields, ``booking_*`` its booking."""

    id: int
    status: Status
    reason: Reason | None
    transaction_id: int | None
    confidence: float | None
    extracted: dict[str, Any]
    duplicate_of: int | None
    message_ref: str | None
    run_id: int | None
    created_at: str
    booked_at: str | None
    booking_date: date | None
    booking_amount_cents: int | None
    booking_text: str | None
    booking_account_id: int | None
    photo_url: str


class BookIn(BaseModel):
    """Corrected values of a review item; ``amount_cents`` is the positive receipt total."""

    date: date
    amount_cents: PositiveCents
    merchant: Short
    account_id: int
    category_id: int | None = None


class SettingsIo(BaseModel):
    """Review threshold (0..1) and the account per area and payment method (null = automatic)."""

    confidence_threshold: Annotated[float, Field(ge=0, le=1)]
    accounts: dict[str, dict[str, int | None]]


class RunStartIn(BaseModel):
    """Start of an agent run."""

    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class RunFinishIn(BaseModel):
    """End of an agent run: the agent's counts (e.g. ``{"messages": 3, "failed": 0}``) and a note."""

    counts: dict[str, int] = Field(default_factory=dict, max_length=20)
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class RunOut(BaseModel):
    """An agent run with the number of its receipts per status."""

    id: int
    started_at: str
    finished_at: str | None
    counts: dict[str, Any]
    note: str | None
    booked: int
    review: int
    duplicate: int


def _receipt_out(item: service.Receipt) -> ReceiptOut:
    return ReceiptOut(
        id=item.id,
        status=item.status,
        reason=item.reason,
        transaction_id=item.transaction_id,
        confidence=item.confidence,
        extracted=item.extracted,
        duplicate_of=item.duplicate_of,
        message_ref=item.message_ref,
        run_id=item.run_id,
        created_at=item.created_at,
        booked_at=item.booked_at,
        booking_date=item.booking_date,
        booking_amount_cents=item.booking_amount_cents,
        booking_text=item.booking_text,
        booking_account_id=item.booking_account_id,
        photo_url=f"{router.prefix}/{item.id}/photo",
    )


def _run_out(run: service.AgentRun) -> RunOut:
    return RunOut(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        counts=run.counts,
        note=run.note,
        booked=run.booked,
        review=run.review,
        duplicate=run.duplicate,
    )


def _settings_io(value: service.ReceiptSettings) -> SettingsIo:
    return SettingsIo(confidence_threshold=value.confidence_threshold, accounts=value.accounts)


# --- upload ------------------------------------------------------------------------------------


async def _read_upload(request: Request) -> tuple[bytes, ReceiptForm]:
    """Parse the multipart body with limits; return the photo bytes and the validated fields."""
    limit = service.MAX_PHOTO_BYTES
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit + _ENVELOPE_BYTES:
        raise LocalizedHTTPException(413, N_("The photo is larger than %(limit)s MB."), limit=limit // (1024 * 1024))
    async with request.form(max_files=1, max_fields=_MAX_FIELDS, max_part_size=_MAX_FIELD_BYTES) as form:
        photo = form.get("photo")
        if not isinstance(photo, UploadFile):
            raise LocalizedHTTPException(400, N_("Attach the receipt photo."))
        data = await photo.read(limit + 1)
        values = {key: value for key, value in form.items() if isinstance(value, str) and value.strip() != ""}
    try:
        fields = ReceiptForm.model_validate(values)
    except ValidationError as exc:
        raise LocalizedHTTPException(422, N_("Invalid receipt field: %(field)s."), field=_first_field(exc)) from exc
    return data, fields


def _first_field(exc: ValidationError) -> str:
    errors = exc.errors()
    return str(errors[0]["loc"][0]) if errors and errors[0]["loc"] else "?"


@router.post("", status_code=201)
async def submit(request: Request, response: Response, conn: Conn, data_dir: DataDir) -> SubmitOut:
    """Hand over one receipt photo with the fields the agent read; books it or puts it on the review list."""
    photo, fields = await _read_upload(request)
    try:
        # In the thread pool, not on the event loop: the write waits at ``BEGIN IMMEDIATE`` while
        # another request holds the write lock, and that wait must not stall every other request.
        result = await run_in_threadpool(
            service.submit_receipt,
            conn,
            data_dir,
            photo,
            fields.extracted(),
            message_ref=fields.message_ref,
            run_id=fields.run_id,
        )
    except Errors as exc:
        _fail(exc)
    if result.receipt_id is None:
        response.status_code = 200
    return SubmitOut(
        status=result.status,
        receipt_id=result.receipt_id,
        transaction_id=result.transaction_id,
        reason=result.reason,
        duplicate_of=result.duplicate_of,
        matched=result.matched,
    )


# --- fixed paths -------------------------------------------------------------------------------


@router.get("")
def list_receipts(
    conn: Conn,
    status: Annotated[list[Status], Query()] = ["review", "duplicate"],  # noqa: B006 — FastAPI copies defaults
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ReceiptOut]:
    """Receipts with the given statuses (default: the review list), newest first."""
    return [_receipt_out(item) for item in service.list_receipts(conn, status, limit=limit)]


@router.get("/settings")
def get_settings(conn: Conn) -> SettingsIo:
    """Review threshold and account mapping."""
    return _settings_io(service.get_receipt_settings(conn))


@router.put("/settings")
def put_settings(body: SettingsIo, conn: Conn) -> SettingsIo:
    """Store review threshold and account mapping."""
    try:
        value = service.set_receipt_settings(conn, service.ReceiptSettings(body.confidence_threshold, body.accounts))
    except Errors as exc:
        _fail(exc)
    return _settings_io(value)


@router.get("/runs")
def list_runs(conn: Conn) -> list[RunOut]:
    """The latest 50 agent runs, newest first."""
    return [_run_out(run) for run in service.list_runs(conn)]


@router.post("/runs", status_code=201)
def start_run(body: RunStartIn, conn: Conn) -> RunOut:
    """Record the start of an agent run; pass its id as ``run_id`` with each receipt."""
    return _run_out(service.start_run(conn, body.note))


@router.put("/runs/{run_id}")
def finish_run(run_id: Annotated[int, PathParam(ge=1, le=MAX_ID)], body: RunFinishIn, conn: Conn) -> RunOut:
    """Record the end of an agent run with the agent's counts."""
    try:
        run = service.finish_run(conn, run_id, body.counts, body.note)
    except Errors as exc:
        _fail(exc)
    return _run_out(run)


# --- one receipt -------------------------------------------------------------------------------


@router.get("/{receipt_id}")
def get_receipt(receipt_id: int, conn: Conn) -> ReceiptOut:
    """One receipt."""
    try:
        item = service.get_receipt(conn, receipt_id)
    except Errors as exc:
        _fail(exc)
    return _receipt_out(item)


@router.get("/{receipt_id}/photo")
def get_photo(receipt_id: int, conn: Conn, data_dir: DataDir) -> FileResponse:
    """The receipt's photo, with the media type detected when it was stored."""
    try:
        path, media_type = service.photo_file(conn, data_dir, receipt_id)
    except Errors as exc:
        _fail(exc)
    return FileResponse(
        path,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@router.post("/{receipt_id}/book")
def book(receipt_id: int, body: BookIn, conn: Conn) -> ReceiptOut:
    """Book a review item with the corrected values."""
    try:
        item = service.book_receipt(conn, receipt_id, service.Correction(**body.model_dump()))
    except Errors as exc:
        _fail(exc)
    return _receipt_out(item)


@router.delete("/{receipt_id}", status_code=204)
def discard(receipt_id: int, conn: Conn, data_dir: DataDir) -> Response:
    """Discard a receipt without booking, with its photo."""
    try:
        service.delete_receipt(conn, data_dir, receipt_id)
    except Errors as exc:
        _fail(exc)
    return Response(status_code=204)
