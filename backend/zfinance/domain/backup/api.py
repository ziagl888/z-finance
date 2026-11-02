"""``/api/backup``: list snapshots, create a manual snapshot, download an export or archive, restore.

Restore takes the file as the raw request body instead of a multipart upload: ``application/json``
for an ``export.json`` (the browser sends the file text unchanged, so no re-serialisation turns
``1.0`` into ``1``) or ``application/zip`` for the archive with the receipt photos, which is
streamed to a temporary file below the data directory under a size limit. No multipart dependency is
needed, and together with the local-only guard (foreign ``Origin`` refused) a cross-site form cannot
reach it.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from zfinance.config import Settings
from zfinance.db import get_connection
from zfinance.domain.backup import files, service
from zfinance.domain.backup.errors import BackupDirError, BackupError
from zfinance.i18n import N_
from zfinance.i18n.errors import LocalizedHTTPException

router = APIRouter(prefix="/api/backup", tags=["backup"])

_ZIP_TYPES = {"application/zip", "application/x-zip-compressed"}


def get_settings(request: Request) -> Settings:
    """Dependency: the settings the app was created with."""
    return request.app.state.settings


class SnapshotOut(BaseModel):
    """One snapshot folder."""

    name: str
    trigger: str
    created_at: str
    path: str
    download_url: str
    archive_url: str


class SnapshotList(BaseModel):
    """All snapshots, newest first, and the folder they live in."""

    directory: str
    snapshots: list[SnapshotOut]


class RestoreOut(BaseModel):
    """Success report of a restore: schema versions, forward migrations, rows per table, photos."""

    export_schema_version: int
    schema_version: int
    migrations_applied: list[str]
    rows: dict[str, int]
    receipt_photos: int


def _snapshot_out(snapshot: service.Snapshot) -> SnapshotOut:
    return SnapshotOut(
        name=snapshot.name,
        trigger=snapshot.trigger,
        created_at=snapshot.created_at,
        path=str(snapshot.path),
        download_url=f"/api/backup/{snapshot.name}/download",
        archive_url=f"/api/backup/{snapshot.name}/archive",
    )


def _fail(exc: BackupError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


@router.get("")
def list_backups(settings: Annotated[Settings, Depends(get_settings)]) -> SnapshotList:
    """List the snapshots in the backup folder, newest first."""
    directory = service.backups_dir(settings.data_dir)
    return SnapshotList(
        directory=str(directory), snapshots=[_snapshot_out(s) for s in service.list_snapshots(directory)]
    )


@router.post("/export", status_code=201)
def export_backup(
    conn: Annotated[sqlite3.Connection, Depends(get_connection)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SnapshotOut:
    """Write a manual snapshot (JSON export + SQLite copy + receipt photos) and return where it is."""
    try:
        return _snapshot_out(
            service.create_snapshot(conn, service.backups_dir(settings.data_dir), "manual", data_dir=settings.data_dir)
        )
    except BackupError as exc:
        _fail(exc)


@router.get("/{name}/download")
def download_backup(name: str, settings: Annotated[Settings, Depends(get_settings)]) -> FileResponse:
    """Send the ``export.json`` of a snapshot as a file download (no photos)."""
    path = service.snapshot_export_path(service.backups_dir(settings.data_dir), name)
    if path is None:
        raise HTTPException(status_code=404, detail=N_("No such backup."))
    return FileResponse(path, media_type="application/json", filename=f"z-finance-export-{name}.json")


@router.get("/{name}/archive")
def download_archive(name: str, settings: Annotated[Settings, Depends(get_settings)]) -> FileResponse:
    """Send a snapshot as a ZIP: its ``export.json`` plus the receipt photos it holds."""
    export = service.snapshot_export_path(service.backups_dir(settings.data_dir), name)
    if export is None:
        raise HTTPException(status_code=404, detail=N_("No such backup."))
    fd, temporary = tempfile.mkstemp(dir=settings.data_dir, prefix=".archive-", suffix=".zip")
    path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as out:
            files.build_archive(export.parent, out)
    except OSError as exc:
        path.unlink(missing_ok=True)
        _fail(BackupDirError(N_("The archive could not be written: %(reason)s"), reason=exc))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"z-finance-backup-{name}.zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


async def _spool_upload(request: Request, directory: Path) -> Path:
    """Stream the request body into a temporary file below ``directory``, at most ``MAX_ARCHIVE_BYTES``.

    The chunks are written on the event loop: they are a few dozen kB each and the app serves one user.
    """
    limit = files.MAX_ARCHIVE_BYTES
    too_large = LocalizedHTTPException(413, N_("The archive is larger than %(limit)s GB."), limit=limit // 1024**3)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".upload-", suffix=".zip")
    path = Path(temporary)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise too_large
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


@router.post("/restore")
async def restore_backup(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_connection)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RestoreOut:
    """Load the export (JSON) or archive (ZIP with photos) in the request body into the (empty) database."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if media_type == "application/json":
            result = await run_in_threadpool(service.restore_export, conn, await request.body())
        elif media_type in _ZIP_TYPES:
            archive = await _spool_upload(request, settings.data_dir)
            try:
                result = await run_in_threadpool(service.restore_archive, conn, archive, settings.data_dir)
            finally:
                archive.unlink(missing_ok=True)
        else:
            raise HTTPException(
                status_code=415,
                detail=N_(
                    "Send the export as the request body with Content-Type: application/json (export.json) "
                    "or application/zip (backup archive)."
                ),
            )
    except BackupError as exc:
        _fail(exc)
    return RestoreOut(
        export_schema_version=result.export_schema_version,
        schema_version=result.schema_version,
        migrations_applied=result.migrations_applied,
        rows=result.rows,
        receipt_photos=result.receipt_photos,
    )
