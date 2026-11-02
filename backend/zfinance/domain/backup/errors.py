"""Errors of the backup feature, shared by ``service`` and ``files``.

Every error is built from an ``N_()`` msgid plus named parameters (never an f-string), so the API
can translate it per request; ``str(exc)`` is the English text.
"""

from __future__ import annotations

from typing import Any


class BackupError(Exception):
    """Base class; ``status_code`` is the HTTP status the API answers with.

    ``msgid`` is an ``N_()`` catalog id with ``%(name)s`` placeholders and ``params`` fills them;
    the API translates ``msgid`` first and substitutes afterwards. ``str(exc)`` is the English text.
    """

    status_code = 500

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid % params if params else msgid)
        self.msgid = msgid
        self.params = params


class BackupDirError(BackupError):
    """A snapshot could not be written (folder not writable, disk full, ...)."""


class InvalidExport(BackupError):
    """The uploaded file is not a usable export (not JSON, wrong shape, checksum mismatch, ...)."""

    status_code = 422


class RestoreRefused(BackupError):
    """The target database already holds data; a restore never merges."""

    status_code = 409


class IncompatibleVersion(BackupError):
    """The export was written by a newer z-finance than this one."""

    status_code = 409
