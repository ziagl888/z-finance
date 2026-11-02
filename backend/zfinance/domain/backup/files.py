"""Receipt photos in a backup: linking them into a snapshot, packing and safely unpacking the archive.

A snapshot folder holds ``export.json``, the SQLite copy and, when the database names photos,
``receipts/<year>/<id>.<ext>``. The *archive* (``GET /api/backup/{name}/archive``) is a ZIP of
``export.json`` plus those photos; restoring one puts the photos back below ``<data dir>/receipts``.

Snapshot: the photos are the files that the ``receipt`` rows of the snapshot's own database copy
name. They are **hard-linked** to the live files (``os.link``) instead of copied, so the automatic
snapshot of every start costs one directory operation per photo, no bytes and no disk space. That
is sound because a photo is write-once: ``receipts.service`` creates a file once and only ever
deletes it, and an orphan left at a reused path is unlinked first, never written through. Deleting
a receipt removes the live link and the snapshot keeps the bytes, rotation removes only the
snapshot's link. Where linking fails (no hard links on that file system, link limit reached) the
photo is copied instead.

Restore: the archive is untrusted input (Zip-Slip, zip bomb).

Invariants:
    * No name read from an archive is used as a path. An entry is accepted only when its whole name
      matches ``receipts/<4 digits>/<id>.(jpg|png|webp)`` (or is ``export.json``); the path is then
      resolved through ``receipts.service._inside_receipts``, in the staging folder and again at the
      final place, so nothing can land outside ``<data dir>/receipts``.
    * A photo is accepted only when a receipt row of the export names its path, its SHA-256 equals
      that row's ``image_hash`` and its magic bytes give the type of its extension.
    * Every size limit is enforced on the bytes actually read, not only on the declared size.
    * Every failure is a ``BackupError`` with an ``N_()`` msgid.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import stat
import zipfile
import zlib
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from zfinance.domain.backup.errors import BackupDirError, InvalidExport
from zfinance.domain.receipts.service import (
    MAX_PHOTO_BYTES,
    RECEIPTS_DIR,
    NotFound,
    _inside_receipts,
    detect_photo_type,
)
from zfinance.i18n import N_

logger = logging.getLogger(__name__)

EXPORT_FILENAME = "export.json"

#: Largest archive accepted for a restore (bytes, upload size).
MAX_ARCHIVE_BYTES = 4 * 1024**3
#: Most photos an archive may hold.
MAX_PHOTOS = 50_000
#: All photos of an archive together (bytes, uncompressed).
MAX_PHOTOS_TOTAL_BYTES = 4 * 1024**3
#: ``export.json`` inside an archive (bytes, uncompressed); the restore holds it in memory.
MAX_EXPORT_BYTES = 256 * 1024**2
#: Directory entries and other overhead an archive may carry beyond its photos.
_ENTRY_SLACK = 1_000
_CHUNK = 1024 * 1024
_MB = 1024 * 1024

_PHOTO_NAME = re.compile(rf"{RECEIPTS_DIR}/\d{{4}}/[1-9]\d{{0,11}}\.(?P<ext>jpg|png|webp)")
_DIR_NAME = re.compile(rf"{RECEIPTS_DIR}/(?:\d{{4}}/)?")
_ARCHIVE_EXCEPTIONS = (zipfile.BadZipFile, zlib.error, EOFError)


def _shown(name: str) -> str:
    """An archive-supplied name as it may appear in a message: shortened and quoted."""
    return repr(name[:80])


# --- snapshot side -----------------------------------------------------------------------------


def link_photos(conn: sqlite3.Connection, data_dir: Path, snapshot_dir: Path) -> int:
    """Put the photos that the ``receipt`` rows of ``conn`` name into ``snapshot_dir``; return how many.

    ``conn`` is the snapshot's own database copy, so every row it holds finds its photo (taken after
    the copy). A row whose path is not a plain ``receipts/<year>/<id>.<ext>`` name, resolves outside
    ``<data dir>/receipts`` or points at a missing file is skipped with a warning; a database that
    was tampered with can therefore not make a snapshot link an arbitrary file.
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'receipt'").fetchone() is None:
        return 0
    linked = 0
    for (relative,) in conn.execute("SELECT DISTINCT file_path FROM receipt ORDER BY file_path"):
        if not isinstance(relative, str) or _PHOTO_NAME.fullmatch(relative) is None:
            logger.warning("backup: receipt path %s is not a photo path; not backed up", _shown(str(relative)))
            continue
        try:
            source = _inside_receipts(data_dir, relative)
        except NotFound:
            logger.warning("backup: receipt path %s lies outside the receipts folder; not backed up", relative)
            continue
        if not source.is_file():
            logger.warning("backup: photo %s is missing; not backed up", relative)
            continue
        target = snapshot_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except FileNotFoundError:  # the receipt was discarded since the check above
            logger.warning("backup: photo %s vanished; not backed up", relative)
            continue
        except OSError:  # no hard links on this file system, or the link limit is reached
            shutil.copy2(source, target)
        linked += 1
    return linked


def _snapshot_photos(snapshot_dir: Path) -> list[str]:
    """Archive names of the photos in a snapshot folder: plain files only, never through a symlink."""
    root = snapshot_dir / RECEIPTS_DIR
    if root.is_symlink() or not root.is_dir():
        return []
    found: list[str] = []
    with os.scandir(root) as years:
        for year in years:
            if not year.is_dir(follow_symlinks=False):
                continue
            with os.scandir(year.path) as entries:
                for entry in entries:
                    name = f"{RECEIPTS_DIR}/{year.name}/{entry.name}"
                    if entry.is_file(follow_symlinks=False) and _PHOTO_NAME.fullmatch(name):
                        found.append(name)
    return sorted(found)


def build_archive(snapshot_dir: Path, out: BinaryIO) -> int:
    """Write ``export.json`` and the photos of a snapshot folder as a ZIP into ``out``; return the photo count.

    Photos are stored, not deflated: JPEG, PNG and WebP are compressed already.
    """
    names = _snapshot_photos(snapshot_dir)
    with zipfile.ZipFile(out, "w", allowZip64=True) as archive:
        archive.write(snapshot_dir / EXPORT_FILENAME, EXPORT_FILENAME, compress_type=zipfile.ZIP_DEFLATED)
        for name in names:
            archive.write(snapshot_dir / name, name, compress_type=zipfile.ZIP_STORED)
    return len(names)


# --- restore side ------------------------------------------------------------------------------


def _inside(base: Path, relative: str) -> Path:
    """``_inside_receipts`` with an archive-flavoured error."""
    try:
        return _inside_receipts(base, relative)
    except NotFound as exc:
        raise InvalidExport(
            N_("The archive entry %(name)s points outside the receipts folder."), name=_shown(relative)
        ) from exc


def _check_kind(info: zipfile.ZipInfo) -> None:
    """Refuse encrypted entries, exotic compression and anything that is not a plain file or folder."""
    if info.flag_bits & 0x1:
        raise InvalidExport(N_("The archive entry %(name)s is encrypted."), name=_shown(info.filename))
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise InvalidExport(
            N_("The archive entry %(name)s uses an unsupported compression."), name=_shown(info.filename)
        )
    if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):  # symlink, device, ...
        raise InvalidExport(N_("The archive entry %(name)s is not a plain file."), name=_shown(info.filename))


@dataclass(frozen=True)
class PhotoArchive:
    """An opened archive whose entry list has passed every name, kind and size check."""

    archive: zipfile.ZipFile
    export: zipfile.ZipInfo
    photos: list[zipfile.ZipInfo]

    def read_export(self) -> bytes:
        """The bytes of ``export.json`` (at most ``MAX_EXPORT_BYTES``)."""
        try:
            with self.archive.open(self.export) as source:
                data = source.read(MAX_EXPORT_BYTES + 1)
        except _ARCHIVE_EXCEPTIONS as exc:
            raise InvalidExport(
                N_("The archive entry %(name)s is damaged: %(reason)s"), name=EXPORT_FILENAME, reason=exc
            ) from exc
        if len(data) > MAX_EXPORT_BYTES:
            raise InvalidExport(
                N_("The export.json in the archive is larger than %(limit)s MB."), limit=MAX_EXPORT_BYTES // _MB
            )
        return data

    def stage_photos(self, hashes: dict[str, str], staging_dir: Path) -> list[str]:
        """Check every photo and write it below ``staging_dir``; return the archive names.

        ``hashes`` maps the ``file_path`` of each receipt row of the export to its ``image_hash``.
        Nothing but ``staging_dir`` is written to.
        """
        staged: list[str] = []
        total = 0
        for info in self.photos:
            name = info.filename
            expected = hashes.get(name)
            if expected is None:
                raise InvalidExport(
                    N_("The archive holds the photo %(name)s, which no receipt in the export refers to."),
                    name=_shown(name),
                )
            target = _inside(staging_dir, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            size, digest, head = self._copy(info, target, total)
            total += size
            kind = detect_photo_type(head)
            if kind is None or kind[1] != name.rsplit(".", 1)[1]:
                raise InvalidExport(
                    N_("The photo %(name)s is not an image of the type its name says."), name=_shown(name)
                )
            if digest != expected:
                raise InvalidExport(
                    N_("The photo %(name)s does not match its receipt (the file was changed or damaged)."),
                    name=_shown(name),
                )
            staged.append(name)
        return staged

    def _copy(self, info: zipfile.ZipInfo, target: Path, total_before: int) -> tuple[int, str, bytes]:
        """Copy one entry to ``target`` under the size limits; return (size, sha256 hex, first bytes)."""
        digest = hashlib.sha256()
        size = 0
        head = b""
        try:
            with self.archive.open(info) as source, target.open("xb") as out:
                while chunk := source.read(_CHUNK):
                    size += len(chunk)
                    if size > MAX_PHOTO_BYTES:
                        raise InvalidExport(
                            N_("The photo %(name)s is larger than %(limit)s MB."),
                            name=_shown(info.filename),
                            limit=MAX_PHOTO_BYTES // _MB,
                        )
                    if total_before + size > MAX_PHOTOS_TOTAL_BYTES:
                        raise InvalidExport(
                            N_("The photos in the archive are larger than %(limit)s GB in total."),
                            limit=MAX_PHOTOS_TOTAL_BYTES // 1024**3,
                        )
                    head = head or chunk[:16]
                    digest.update(chunk)
                    out.write(chunk)
        except _ARCHIVE_EXCEPTIONS as exc:
            raise InvalidExport(
                N_("The archive entry %(name)s is damaged: %(reason)s"), name=_shown(info.filename), reason=exc
            ) from exc
        return size, digest.hexdigest(), head


@contextlib.contextmanager
def open_archive(path: Path) -> Generator[PhotoArchive]:
    """Open a backup archive and validate its entry list; nothing is extracted yet."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise InvalidExport(N_("The file is not a valid ZIP archive: %(reason)s"), reason=exc) from exc
    with archive:
        yield _inspect(archive)


def _inspect(archive: zipfile.ZipFile) -> PhotoArchive:
    infos = archive.infolist()
    if len(infos) > MAX_PHOTOS + _ENTRY_SLACK:
        raise InvalidExport(N_("The archive has too many entries."))
    export: zipfile.ZipInfo | None = None
    photos: list[zipfile.ZipInfo] = []
    seen: set[str] = set()
    declared = 0
    for info in infos:
        name = info.filename
        if name in seen:
            raise InvalidExport(N_("The archive lists the entry %(name)s twice."), name=_shown(name))
        seen.add(name)
        _check_kind(info)
        if info.is_dir():
            if _DIR_NAME.fullmatch(name) is None:
                raise InvalidExport(N_("The archive holds an unexpected entry: %(name)s."), name=_shown(name))
        elif name == EXPORT_FILENAME:
            if info.file_size > MAX_EXPORT_BYTES:
                raise InvalidExport(
                    N_("The export.json in the archive is larger than %(limit)s MB."), limit=MAX_EXPORT_BYTES // _MB
                )
            export = info
        elif _PHOTO_NAME.fullmatch(name):
            if info.file_size > MAX_PHOTO_BYTES:
                raise InvalidExport(
                    N_("The photo %(name)s is larger than %(limit)s MB."),
                    name=_shown(name),
                    limit=MAX_PHOTO_BYTES // _MB,
                )
            declared += info.file_size
            photos.append(info)
        else:
            raise InvalidExport(N_("The archive holds an unexpected entry: %(name)s."), name=_shown(name))
    if len(photos) > MAX_PHOTOS:
        raise InvalidExport(N_("The archive has too many photos."))
    if declared > MAX_PHOTOS_TOTAL_BYTES:
        raise InvalidExport(
            N_("The photos in the archive are larger than %(limit)s GB in total."),
            limit=MAX_PHOTOS_TOTAL_BYTES // 1024**3,
        )
    if export is None:
        raise InvalidExport(N_("The archive has no export.json."))
    return PhotoArchive(archive, export, photos)


def place_photos(staging_dir: Path, data_dir: Path, names: list[str]) -> list[Path]:
    """Move the staged photos to ``<data dir>/receipts``; return the placed paths.

    A restore only runs into an empty database, so a file already sitting at a target is an orphan
    and is replaced. When a move fails, the photos placed so far are removed again.
    """
    placed: list[Path] = []
    try:
        for name in names:
            source = _inside(staging_dir, name)
            target = _inside(data_dir, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            placed.append(target)
    except OSError as exc:
        remove_files(placed)
        raise BackupDirError(
            N_("The photos could not be restored into %(directory)s: %(reason)s"),
            directory=data_dir / RECEIPTS_DIR,
            reason=exc,
        ) from exc
    except BaseException:
        remove_files(placed)
        raise
    return placed


def remove_files(paths: list[Path]) -> None:
    """Delete files, ignoring the ones that are gone (rollback helper; never raises for a missing file)."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove %s", path, exc_info=True)
