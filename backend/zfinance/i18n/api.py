"""``/api/i18n/language``: the UI language the user chose in the settings page.

Stored in ``app_setting`` under ``ui.language``. ``language`` is ``null`` until the user picks
one; the frontend then keeps its own default (English) and the API negotiates error messages
from ``Accept-Language``.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from zfinance.app_settings import get_setting, set_setting
from zfinance.db import get_connection
from zfinance.i18n import LANGUAGE_SETTING_KEY, N_, SUPPORTED_LANGUAGES
from zfinance.i18n.errors import LocalizedHTTPException

router = APIRouter(prefix="/api/i18n", tags=["i18n"])


class LanguageState(BaseModel):
    """Stored UI language (``None`` = never chosen) and the languages available."""

    language: str | None
    supported: list[str]


class LanguageChoice(BaseModel):
    """Request body of ``PUT /api/i18n/language``."""

    # Bounded because the code is echoed back in the error message.
    language: str = Field(max_length=35)


def _state(conn: sqlite3.Connection) -> LanguageState:
    stored = get_setting(conn, LANGUAGE_SETTING_KEY)
    return LanguageState(
        language=stored if stored in SUPPORTED_LANGUAGES else None, supported=list(SUPPORTED_LANGUAGES)
    )


@router.get("/language")
def get_language(conn: Annotated[sqlite3.Connection, Depends(get_connection)]) -> LanguageState:
    """Return the stored UI language."""
    return _state(conn)


@router.put("/language")
def put_language(choice: LanguageChoice, conn: Annotated[sqlite3.Connection, Depends(get_connection)]) -> LanguageState:
    """Store the UI language; an unsupported code is refused with 422 and nothing is stored."""
    if choice.language not in SUPPORTED_LANGUAGES:
        raise LocalizedHTTPException(
            422,
            N_("Unsupported language %(language)s. Available: %(supported)s."),
            language=choice.language,
            supported=", ".join(SUPPORTED_LANGUAGES),
        )
    set_setting(conn, LANGUAGE_SETTING_KEY, choice.language)
    return _state(conn)
