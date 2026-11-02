"""Backend translations: gettext catalogs per language, loaded from ``.po`` files.

English is the source language — the strings in the code are the English text, so there is no
English catalog. ``i18n/messages.pot`` is extracted from ``backend/zfinance`` and each supported
language has ``i18n/<lang>/LC_MESSAGES/messages.po``. The ``.po`` file is read at first use and
turned into a stdlib ``gettext.GNUTranslations`` in memory, so no compiled ``.mo`` file is
committed or built.

Marking a string for translation (the extractor looks for these names):

* ``N_("...")`` marks a message that is translated later, when the request language is known —
  the usual case for API error details (see ``zfinance.i18n.errors``).
* ``gettext("...", language)`` translates immediately.

Messages with parameters use named printf placeholders, ``N_("Account %(name)s not found")``,
so a translation can reorder them.
"""

from __future__ import annotations

import gettext as _gettext
import io
from functools import cache
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

#: Source language; also the answer when nothing better matches.
DEFAULT_LANGUAGE = "en"
#: Languages the UI and the API speak. Adding one means adding its catalogs (see CONTRIBUTING.md).
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "de")
#: ``app_setting`` key holding the user's choice (migration 001).
LANGUAGE_SETTING_KEY = "ui.language"

CATALOG_DIR = Path(__file__).parent
DOMAIN = "messages"


def N_(message: str) -> str:
    """Mark ``message`` for extraction without translating it (translated later, per request)."""
    return message


@cache
def translations(language: str) -> _gettext.NullTranslations:
    """Return the catalog for ``language``; English and unknown languages get the identity catalog.

    Fuzzy entries are skipped (``write_mo`` default), so an unreviewed translation shows English.
    """
    po_path = CATALOG_DIR / language / "LC_MESSAGES" / f"{DOMAIN}.po"
    if language == DEFAULT_LANGUAGE or not po_path.is_file():
        return _gettext.NullTranslations()
    with po_path.open("rb") as po_file:
        catalog = read_po(po_file, locale=language)
    buffer = io.BytesIO()
    write_mo(buffer, catalog)
    buffer.seek(0)
    return _gettext.GNUTranslations(buffer)


def gettext(message: str, language: str) -> str:
    """Translate ``message`` into ``language``; unknown messages come back unchanged."""
    return translations(language).gettext(message)


def negotiate_language(accept_language: str | None) -> str | None:
    """Pick the best supported language from an ``Accept-Language`` header, or None.

    Entries are ordered by their ``q`` weight (default 1, header order breaks ties); only the
    primary subtag counts (``de-AT`` → ``de``); ``q=0`` means "not acceptable". Malformed entries
    are skipped rather than rejected — the header is a hint, never a reason to fail a request.
    """
    if not accept_language:
        return None
    weighted: list[tuple[float, int, str]] = []
    for position, entry in enumerate(accept_language.split(",")):
        tag, _, params = entry.strip().partition(";")
        quality = 1.0
        params = params.strip()
        if params.startswith("q="):
            try:
                quality = float(params[2:])
            except ValueError:
                continue
        primary = tag.strip().split("-")[0].lower()
        if primary and quality > 0:
            weighted.append((-quality, position, primary))
    for _quality, _position, primary in sorted(weighted):
        if primary in SUPPORTED_LANGUAGES:
            return primary
    return None
