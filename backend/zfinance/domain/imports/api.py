"""``/api/imports``: CSV import of UC-ZF-005 — profiles, settings, detect, preview, commit, runs.

Files arrive as the raw request body (like the backup restore), not as a multipart upload: no
extra dependency, the bytes reach the parser unchanged (the encoding is part of the file), and
together with the local-only guard a cross-site form cannot post one. Form content types are
refused. The body is read in chunks and cut off above ``parser.MAX_FILE_BYTES`` (413).

    curl -X POST "http://127.0.0.1:8765/api/imports/preview?profile_id=1&account_id=1" \\
         -H "Content-Type: text/csv" --data-binary @statement.csv
    curl -X POST http://127.0.0.1:8765/api/imports/commit -H "Content-Type: application/json" \\
         -d '{"preview_id": "<from the preview>", "overrides": [{"line": 4, "category_id": 7}]}'

The preview answers with a ``preview_id``; the commit takes it with the overrides. Line errors come
back as ``{line, message}`` in the request language.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StringConstraints

from zfinance.db import get_connection
from zfinance.domain.accounts.service import AccountsError
from zfinance.domain.imports import parser, service
from zfinance.domain.imports.matching import Kind, Tolerance
from zfinance.domain.imports.parser import ColumnMap, DecimalSep, Delimiter, Encoding, ImportFileError, LineError
from zfinance.domain.recurring.service import RecurringError
from zfinance.domain.transactions.service import TransactionsError
from zfinance.i18n import N_, gettext
from zfinance.i18n.errors import LocalizedHTTPException, request_language

router = APIRouter(prefix="/api/imports", tags=["imports"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
OptionalName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]

Failure = ImportFileError | AccountsError | TransactionsError | RecurringError

_FORM_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")
_CSV_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
    }
}


def _fail(exc: Failure) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


def _translate(msgid: str, params: dict[str, Any], language: str) -> str:
    text = gettext(msgid, language)
    return text % params if params else text


async def _body(request: Request) -> bytes:
    """The raw file, refused for form content types or above the size limit (read in chunks)."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in _FORM_TYPES:
        raise HTTPException(status_code=415, detail=N_("Send the CSV file as the request body, not as a form upload."))
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > parser.MAX_FILE_BYTES:
            raise LocalizedHTTPException(
                413, N_("The file is larger than %(max)s MB."), max=parser.MAX_FILE_BYTES // (1024 * 1024)
            )
        chunks.append(chunk)
    return b"".join(chunks)


# --- models ------------------------------------------------------------------------------------


class ColumnMapIn(BaseModel):
    """Header names per role; ``amount`` or ``debit`` + ``credit``."""

    date: Name
    text: Name
    amount: OptionalName | None = None
    debit: OptionalName | None = None
    credit: OptionalName | None = None
    counterparty: OptionalName | None = None


class ProfileIn(BaseModel):
    """Body of create and update (full replacement)."""

    bank_name: Name
    column_map: ColumnMapIn
    delimiter: Delimiter = "auto"
    encoding: Encoding = "auto"
    date_format: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)] = "%d.%m.%Y"
    decimal_sep: DecimalSep = ","
    skip_rows: Annotated[int, Field(ge=0, le=50)] = 0

    def fields(self) -> parser.Profile:
        """The service's input value."""
        values = self.model_dump()
        values["column_map"] = ColumnMap(**values["column_map"])
        return parser.Profile(**values)


class ProfileOut(ProfileIn):
    """A stored profile."""

    id: int
    created_at: str


class ToleranceIo(BaseModel):
    """Match tolerance: amount in percent of the planned amount, date in days."""

    amount_pct: Annotated[int, Field(ge=0, le=service.AMOUNT_PCT_MAX)]
    date_days: Annotated[int, Field(ge=0, le=service.DATE_DAYS_MAX)]


class DetectOut(BaseModel):
    """Detected dialect, header and the first five records."""

    encoding: str
    delimiter: str
    header: list[str]
    rows: list[list[str]]


class LineErrorOut(BaseModel):
    """A line that is not imported, with the reason."""

    line: int
    message: str


class PreviewLineOut(BaseModel):
    """One classified line; ``kind`` new | duplicate | match | duplicate_suspect."""

    line: int
    date: str
    amount_cents: int
    text: str
    counterparty: str | None
    kind: Kind
    import_hash: str
    planned_id: int | None
    candidates: list[int]
    review: bool
    suspect_id: int | None
    category_id: int | None
    category_source: str | None


class PreviewOut(BaseModel):
    """What the import would do; commit it with ``preview_id``."""

    preview_id: str
    account_id: int
    profile_id: int
    file_hash: str
    encoding: str
    delimiter: str
    counts: dict[str, int]
    lines: list[PreviewLineOut]
    errors: list[LineErrorOut]


class OverrideIn(BaseModel):
    """A correction of one line. Send ``planned_id`` (an id or ``null``) only to change the match."""

    line: int
    skip: bool | None = None
    category_id: int | None = None
    planned_id: int | None = None


class CommitIn(BaseModel):
    """The staged preview and the user's corrections."""

    preview_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    overrides: list[OverrideIn] = []


class RunOut(BaseModel):
    """A committed import with its counts and the line errors that were skipped."""

    id: int
    account_id: int
    profile_id: int | None
    file_name: str | None
    file_hash: str
    counts: dict[str, int]
    errors: list[LineErrorOut]
    created_at: str


def _profile_out(profile: service.StoredProfile) -> ProfileOut:
    return ProfileOut(
        bank_name=profile.bank_name,
        column_map=ColumnMapIn(**profile.column_map.roles()),
        delimiter=profile.delimiter,
        encoding=profile.encoding,
        date_format=profile.date_format,
        decimal_sep=profile.decimal_sep,
        skip_rows=profile.skip_rows,
        id=profile.id,
        created_at=profile.created_at,
    )


def _errors_out(errors: list[LineError], language: str) -> list[LineErrorOut]:
    return [LineErrorOut(line=e.line, message=_translate(e.msgid, e.params, language)) for e in errors]


def _run_out(run: service.Run, language: str) -> RunOut:
    return RunOut(
        id=run.id,
        account_id=run.account_id,
        profile_id=run.profile_id,
        file_name=run.file_name,
        file_hash=run.file_hash,
        counts=run.counts,
        errors=_errors_out(run.errors, language),
        created_at=run.created_at,
    )


# --- settings ----------------------------------------------------------------------------------


@router.get("/settings")
def get_settings(conn: Conn) -> ToleranceIo:
    """The match tolerance for planned items (default ±5 %, ±5 days)."""
    tolerance = service.get_tolerance(conn)
    return ToleranceIo(amount_pct=tolerance.amount_pct, date_days=tolerance.date_days)


@router.put("/settings")
def put_settings(body: ToleranceIo, conn: Conn) -> ToleranceIo:
    """Change the match tolerance."""
    try:
        tolerance = service.set_tolerance(conn, Tolerance(body.amount_pct, body.date_days))
    except ImportFileError as exc:
        _fail(exc)
    return ToleranceIo(amount_pct=tolerance.amount_pct, date_days=tolerance.date_days)


# --- profiles ----------------------------------------------------------------------------------


@router.get("/profiles")
def list_profiles(conn: Conn) -> list[ProfileOut]:
    """All import profiles by bank name."""
    return [_profile_out(profile) for profile in service.list_profiles(conn)]


@router.post("/profiles", status_code=201)
def create_profile(body: ProfileIn, conn: Conn) -> ProfileOut:
    """Store a profile for a bank."""
    try:
        return _profile_out(service.create_profile(conn, body.fields()))
    except ImportFileError as exc:
        _fail(exc)


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: int, conn: Conn) -> ProfileOut:
    """One profile."""
    try:
        return _profile_out(service.get_profile(conn, profile_id))
    except ImportFileError as exc:
        _fail(exc)


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: int, body: ProfileIn, conn: Conn) -> ProfileOut:
    """Replace a profile; earlier imports stay untouched."""
    try:
        return _profile_out(service.update_profile(conn, profile_id, body.fields()))
    except ImportFileError as exc:
        _fail(exc)


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: int, conn: Conn) -> Response:
    """Delete a profile; its runs stay."""
    try:
        service.delete_profile(conn, profile_id)
    except ImportFileError as exc:
        _fail(exc)
    return Response(status_code=204)


# --- file steps --------------------------------------------------------------------------------


@router.post("/detect", openapi_extra=_CSV_BODY)
async def detect(
    request: Request,
    encoding: Encoding = "auto",
    delimiter: Delimiter = "auto",
    skip_rows: Annotated[int, Query(ge=0, le=50)] = 0,
) -> DetectOut:
    """Encoding, delimiter, header and first five records of a file (for mapping the columns)."""
    data = await _body(request)
    try:
        result = await run_in_threadpool(
            service.detect, data, encoding=encoding, delimiter=delimiter, skip_rows=skip_rows
        )
    except ImportFileError as exc:
        _fail(exc)
    return DetectOut(encoding=result.encoding, delimiter=result.delimiter, header=result.header, rows=result.rows)


@router.post("/preview", openapi_extra=_CSV_BODY)
async def preview(
    request: Request,
    conn: Conn,
    profile_id: Annotated[int, Query()],
    account_id: Annotated[int, Query()],
    file_name: Annotated[str | None, Query(max_length=200)] = None,
) -> PreviewOut:
    """Classify the file's lines for the account (new, duplicate, match, duplicate suspect); nothing is written."""
    data = await _body(request)
    try:
        result = await run_in_threadpool(service.stage_preview, conn, profile_id, account_id, data, file_name)
    except (ImportFileError, AccountsError) as exc:
        _fail(exc)
    language = request_language(request)
    return PreviewOut(
        preview_id=result.preview_id,
        account_id=result.account_id,
        profile_id=result.profile_id,
        file_hash=result.file_hash,
        encoding=result.encoding,
        delimiter=result.delimiter,
        counts=result.counts,
        lines=[
            PreviewLineOut(
                line=line.line,
                date=line.date.isoformat(),
                amount_cents=line.amount_cents,
                text=line.text,
                counterparty=line.counterparty,
                kind=line.kind,
                import_hash=line.import_hash,
                planned_id=line.planned_id,
                candidates=list(line.candidates),
                review=line.review,
                suspect_id=line.suspect_id,
                category_id=line.category_id,
                category_source=line.category_source,
            )
            for line in result.lines
        ],
        errors=_errors_out(result.errors, language),
    )


@router.post("/commit", status_code=201)
def commit(body: CommitIn, request: Request, conn: Conn) -> RunOut:
    """Write the previewed import in one transaction; nothing is written if any line fails."""
    overrides = [
        service.Override(
            line=item.line,
            skip=item.skip,
            category_id=item.category_id,
            set_match="planned_id" in item.model_fields_set,
            planned_id=item.planned_id,
        )
        for item in body.overrides
    ]
    language = request_language(request)
    try:
        run = service.commit(conn, body.preview_id, overrides)
    except service.LineWriteError as exc:
        reason = _translate(exc.cause.msgid, exc.cause.params, language)
        raise LocalizedHTTPException(
            exc.status_code,
            N_("Line %(line)s could not be imported, nothing was written: %(reason)s"),
            line=exc.line,
            reason=reason,
        ) from exc
    except (ImportFileError, AccountsError) as exc:
        _fail(exc)
    return _run_out(run, language)


# --- runs --------------------------------------------------------------------------------------


@router.get("/runs")
def list_runs(request: Request, conn: Conn, account_id: Annotated[int | None, Query()] = None) -> list[RunOut]:
    """The latest import runs, newest first."""
    language = request_language(request)
    return [_run_out(run, language) for run in service.list_runs(conn, account_id=account_id)]


@router.get("/runs/{run_id}")
def get_run(run_id: int, request: Request, conn: Conn) -> RunOut:
    """One import run with its counts."""
    try:
        return _run_out(service.get_run(conn, run_id), request_language(request))
    except ImportFileError as exc:
        _fail(exc)
