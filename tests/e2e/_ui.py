"""Reading the rendered page: the formats the UI writes, and a few short locator helpers.

An acceptance criterion names a figure ("minimum 500 €"), not its notation. ``euro`` turns cents
into the string the English UI prints (``Intl.NumberFormat`` with EUR), so a test can look for the
figure without every test repeating the notation. ``date_label`` asks the browser itself how it
writes a day, for the same reason: the criterion is about *which* day, not about the format.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


def euro(cents: int) -> str:
    """Cents as the English UI prints them: ``euro(-968_000)`` is ``-€9,680.00``."""
    sign = "-" if cents < 0 else ""
    return f"{sign}€{abs(cents) / 100:,.2f}"


def read_cents(printed: str) -> int:
    """The euro amount inside a piece of page text as integer cents: ``-€1,234.56`` -> ``-123456``.

    The first amount in the string wins, which is what a cell or a figure tile holds.
    """
    match = re.search(r"(-?)\s*€\s*(-?)([\d,]+)\.(\d{2})", norm(printed))
    if not match:
        raise AssertionError(f"no euro amount in {printed!r}")
    sign = -1 if "-" in (match.group(1) + match.group(2)) else 1
    return sign * (int(match.group(3).replace(",", "")) * 100 + int(match.group(4)))


def norm(raw: str) -> str:
    """Page text with the spaces a layout inserts folded into plain ones (no-break, narrow, tabs)."""
    for space in ("\u00a0", "\u202f", "\u2009", "\t"):
        raw = raw.replace(space, " ")
    return " ".join(raw.split())


def text(target: Any) -> str:
    """Normalised visible text of a page or locator."""
    return norm(target.inner_text() if hasattr(target, "inner_text") else str(target))


def body(page: Any) -> str:
    """Normalised visible text of the whole page."""
    return norm(page.locator("body").inner_text())


def shown(target: Any, snippet: str) -> bool:
    """Whether the page or locator shows ``snippet``, ignoring case.

    Headings arrive in upper case: the style sheet sets ``text-transform``, and ``inner_text``
    reports what is painted. An assertion on a heading is about the words, not about the casing.
    """
    if isinstance(target, str):
        haystack = target
    elif hasattr(target, "goto"):  # a page, not a locator
        haystack = body(target)
    else:
        haystack = text(target)
    return snippet.casefold() in haystack.casefold()


def date_label(page: Any, day: date, style: str = "medium") -> str:
    """How the page writes ``day``: ``medium`` is the "Sep 30, 2026" of the lists, ``short`` the
    ``toLocaleDateString`` of the forms. Asked of the browser, because the format is the platform's."""
    iso = day.isoformat()
    if style == "short":
        return norm(page.evaluate("(iso) => new Date(`${iso}T00:00:00`).toLocaleDateString('en')", iso))
    return norm(
        page.evaluate(
            "(iso) => new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(iso))",
            iso,
        )
    )


def month_label(page: Any, day: date) -> str:
    """How the page writes the month of ``day`` ("July 2027")."""
    return norm(
        page.evaluate(
            "(iso) => new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric', timeZone: 'UTC' })"
            ".format(new Date(iso))",
            day.replace(day=1).isoformat(),
        )
    )


def kpi(page: Any, label: str) -> Any:
    """The figure tile whose label contains ``label`` (the tiles carry no id and no test hook)."""
    return page.locator("div.ui-kpi", has_text=label).first


def kpi_value(page: Any, label: str) -> str:
    """The value of that tile ("€25,473.69", or a bare count)."""
    return text(kpi(page, label).locator(".ui-kpi-value"))


def row(page: Any, contains: str) -> Any:
    """The table row that contains ``contains`` (the usual way to a figure next to a name)."""
    return page.locator("tr", has_text=contains).first


def wait_row(page: Any, contains: str, timeout: int = 5000) -> Any:
    """Wait for that row to be there and return it: a list refreshes after its notice appears."""
    page.wait_for_selector(f'tr:has-text("{contains}")', timeout=timeout)
    return row(page, contains)


def wait_gone(page: Any, selector: str, timeout: int = 5000) -> None:
    """Wait until ``selector`` is off the page: a list refreshes a moment after the click."""
    page.wait_for_selector(selector, state="detached", timeout=timeout)


def months_ahead(start: date, months: int) -> date:
    """``start`` shifted by whole months, clamped to the length of the target month."""
    index = start.year * 12 + start.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    lengths = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return date(year, month, min(start.day, lengths[month - 1]))
