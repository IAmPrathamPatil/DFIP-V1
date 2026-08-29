"""Excel-derived scalars recovered from the Web-Engage Raw sheet formulas.

Recovered formulas (identical in every supplied production workbook):

    I (HHH)   = TEXT([Start Date], "HH")
    J (Month) = TEXT([Day], "MMM-YY")

`month_start` is the first day of the `Day` month. It is a DFIP reporting
convenience, not an Excel column, and is derived only from `Day`.
"""

from __future__ import annotations

from datetime import date, datetime

MONTH_ABBREVIATIONS: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

# Excel TEXT() on an empty cell formats serial 0, whose hour is 00.
BLANK_START_DATE_HOUR = "00"


def month_label(day: date) -> str:
    """TEXT(Day, "MMM-YY"), e.g. 2025-04-01 -> 'Apr-25'."""
    return f"{MONTH_ABBREVIATIONS[day.month - 1]}-{day.year % 100:02d}"


def month_start(day: date) -> date:
    return date(day.year, day.month, 1)


def hhh(start_date: datetime | None) -> str:
    """TEXT(Start Date, "HH"). Blank Start Date yields '00', as Excel does."""
    if start_date is None:
        return BLANK_START_DATE_HOUR
    return f"{start_date.hour:02d}"


def variation_id_key(variation_id: str | None) -> str:
    """OPEN-A6 resolution.

    `fact_campaign_day` needs a NOT NULL variation component for its primary
    key, but Variation ID is blank on a small number of production rows.

    Excel cannot distinguish an empty cell from an empty string, so both
    collapse to the same business value: "no variation". The empty string is
    therefore the smallest deterministic representation and cannot collide with
    any non-blank Variation ID. No sentinel literal is invented.
    """
    if variation_id is None:
        return ""
    return variation_id
