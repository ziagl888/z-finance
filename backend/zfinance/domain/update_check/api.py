"""``/api/update-check``: the opt-in update notice of UC NF10.

``GET`` returns the switch and the remembered answer without asking GitHub, ``PUT`` sets the
switch, ``POST /run`` performs one check. Nothing here runs by itself: there is no startup hook and
no timer, so a user who never switches the check on never causes an outbound request (UC NF2).
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool

from zfinance.db import get_connection
from zfinance.domain.update_check import service
from zfinance.i18n.errors import LocalizedHTTPException

router = APIRouter(prefix="/api/update-check", tags=["update-check"])

Conn = Annotated[sqlite3.Connection, Depends(get_connection)]


def _fail(exc: service.UpdateCheckError) -> NoReturn:
    raise LocalizedHTTPException(exc.status_code, exc.msgid, **exc.params) from exc


class EnabledIn(BaseModel):
    """Body of PUT: the opt-in switch."""

    enabled: StrictBool


class UpdateCheckOut(BaseModel):
    """The switch, the running version and the answer of the last check (never a download link)."""

    enabled: bool
    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str
    checked_at: str | None


def _out(state: service.UpdateState) -> UpdateCheckOut:
    return UpdateCheckOut(
        enabled=state.enabled,
        current_version=state.current_version,
        latest_version=state.latest_version,
        update_available=state.update_available,
        release_url=state.release_url,
        checked_at=state.checked_at,
    )


@router.get("")
def get_update_check(conn: Conn) -> UpdateCheckOut:
    """Return the switch and the remembered result; makes no request."""
    return _out(service.state(conn))


@router.put("")
def put_update_check(body: EnabledIn, conn: Conn) -> UpdateCheckOut:
    """Switch the update check on or off. Switching it off forgets the last result."""
    service.set_enabled(conn, body.enabled)
    return _out(service.state(conn))


@router.post("/run")
def run_update_check(conn: Conn) -> UpdateCheckOut:
    """Ask GitHub once for the newest release. Refused while the check is switched off."""
    try:
        return _out(service.check_now(conn))
    except service.UpdateCheckError as exc:
        _fail(exc)
