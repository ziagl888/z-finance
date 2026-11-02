"""Localized API error messages.

Every ``HTTPException`` whose ``detail`` is a string goes through
``localized_http_exception_handler``: the string is looked up as a catalog msgid and answered in
the request language. A detail that is not in the catalog comes back unchanged, so an unmarked
message degrades to English instead of failing.

Raising a translatable error in a feature::

    from zfinance.i18n import N_
    from zfinance.i18n.errors import LocalizedHTTPException

    raise HTTPException(status_code=404, detail=N_("Account not found"))
    raise LocalizedHTTPException(409, N_("Account %(name)s already exists"), name=name)

Request language, first match wins: the ``ui.language`` setting (the user's explicit choice in
the settings page), the ``Accept-Language`` header, English.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from zfinance.app_settings import get_setting
from zfinance.db import connect
from zfinance.i18n import DEFAULT_LANGUAGE, LANGUAGE_SETTING_KEY, N_, SUPPORTED_LANGUAGES, gettext, negotiate_language

logger = logging.getLogger(__name__)

# Details the framework raises itself (unknown API path, wrong method); marked here so they are
# extracted into the catalog like any feature message.
FRAMEWORK_MESSAGES = (N_("Not Found"), N_("Method Not Allowed"))


class LocalizedHTTPException(HTTPException):
    """An ``HTTPException`` whose ``detail`` msgid has ``%(name)s`` parameters.

    The parameters are substituted after translation, so the translated text can place them
    wherever its grammar needs them.
    """

    def __init__(self, status_code: int, detail: str, *, headers: dict[str, str] | None = None, **params: Any) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.params = params


def stored_language(request: Request) -> str | None:
    """Return the supported language saved in ``ui.language``, or None.

    A database error while answering an error must not turn it into a 500, so it is logged and
    treated as "no setting".
    """
    try:
        conn = connect(request.app.state.settings.db_path)
        try:
            value = get_setting(conn, LANGUAGE_SETTING_KEY)
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("could not read %s; falling back to Accept-Language", LANGUAGE_SETTING_KEY, exc_info=True)
        return None
    return value if value in SUPPORTED_LANGUAGES else None


def request_language(request: Request) -> str:
    """Language for this request's messages: stored setting, then Accept-Language, then English."""
    return stored_language(request) or negotiate_language(request.headers.get("accept-language")) or DEFAULT_LANGUAGE


async def localized_http_exception_handler(request: Request, exc: Exception) -> Response:
    """Translate a string ``detail`` and delegate the response to FastAPI's default handler."""
    # Registered for StarletteHTTPException only; the handler signature must accept Exception.
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError(f"unexpected exception type {type(exc).__name__}")
    if not isinstance(exc.detail, str):
        return await http_exception_handler(request, exc)
    detail = gettext(exc.detail, request_language(request))
    if isinstance(exc, LocalizedHTTPException) and exc.params:
        detail = detail % exc.params
    localized = StarletteHTTPException(status_code=exc.status_code, detail=detail, headers=exc.headers)
    return await http_exception_handler(request, localized)
