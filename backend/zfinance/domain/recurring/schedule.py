"""Due dates of a recurring item, as plain functions (no database).

Rules (UC-ZF-003 step 1, extension 1a):

* ``weekly``: every week on ``weekday`` (0 = Monday), from the first such day on or after the start.
* ``monthly`` / ``quarterly`` / ``yearly``: on ``day_of_month`` every 1 / 3 / 12 months, counted
  from the month of the start date. A day that the month does not have (29-31) falls on the
  month's last day: day 31 is 28 or 29 February, 30 April, 31 May.
* A due date before the start date or after the end date does not exist.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Interval = Literal["weekly", "monthly", "quarterly", "yearly"]

#: Months between two due dates of the month-based intervals.
MONTH_STEPS: dict[str, int] = {"monthly": 1, "quarterly": 3, "yearly": 12}


@dataclass(frozen=True)
class Schedule:
    """When a recurring item is due. Weekly needs ``weekday``, the others ``day_of_month``."""

    interval: Interval
    start_date: date
    end_date: date | None = None
    day_of_month: int | None = None
    weekday: int | None = None


def add_months(day: date, months: int) -> date:
    """``day`` shifted by ``months`` calendar months; a missing day becomes the month's last day."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return clamped(year, month + 1, day.day)


def clamped(year: int, month: int, day: int) -> date:
    """The date ``day`` in the month, or the month's last day when the month is shorter (UC 1a)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def due_dates(schedule: Schedule, first: date, last: date) -> list[date]:
    """All due dates of ``schedule`` within ``first`` .. ``last`` (both inclusive), ascending."""
    lower = max(first, schedule.start_date)
    upper = last if schedule.end_date is None else min(last, schedule.end_date)
    if lower > upper:
        return []
    if schedule.interval == "weekly":
        if schedule.weekday is None:
            raise ValueError("a weekly schedule needs a weekday")
        return _weekly(schedule.weekday, lower, upper)
    if schedule.day_of_month is None:
        raise ValueError(f"a {schedule.interval} schedule needs a day of the month")
    return _monthly(schedule.start_date, MONTH_STEPS[schedule.interval], schedule.day_of_month, lower, upper)


def _weekly(weekday: int, lower: date, upper: date) -> list[date]:
    day = lower + timedelta(days=(weekday - lower.weekday()) % 7)
    result: list[date] = []
    while day <= upper:
        result.append(day)
        day += timedelta(days=7)
    return result


def _monthly(start: date, step: int, day_of_month: int, lower: date, upper: date) -> list[date]:
    anchor = start.year * 12 + start.month - 1
    # First month of the cycle that can reach ``lower``: jump there instead of walking from the start.
    skipped = max(0, (lower.year * 12 + lower.month - 1 - anchor) // step)
    index = anchor + skipped * step
    result: list[date] = []
    while True:
        year, month = divmod(index, 12)
        due = clamped(year, month + 1, day_of_month)
        if due > upper:
            return result
        if due >= lower:
            result.append(due)
        index += step
