"""Source-field extraction and validation for P4.

Reads the exact 57 Excel headers preserved by P3. Values are not trimmed,
cased, or repaired. Malformed numerics are rejected rather than coerced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# Excel header -> fact column, for the fields P4 copies onto the fact record.
TEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("Campaign Name", "campaign_name"),
    ("Variation Name", "variation_name"),
    ("Channel", "channel"),
    ("Type of Campaign", "type_of_campaign"),
    ("Template Name (WhatsApp)", "template_name_whatsapp"),
)

INTEGER_FIELDS: tuple[tuple[str, str], ...] = (
    ("Sent", "sent"),
    ("Failed", "failed"),
    ("Delivered", "delivered"),
    ("Unique Impressions", "unique_impressions"),
    ("Unique Clicks", "unique_clicks"),
    ("Unique Conversions", "unique_conversions"),
    ("Unique Impression-Through Conversions", "unique_impression_through_conversions"),
    ("Unique Click-Through Conversions", "unique_click_through_conversions"),
)

DECIMAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("Revenue (INR)", "revenue_inr"),
    ("Impression-Through Revenue (INR)", "impression_through_revenue_inr"),
    ("Click-Through Revenue (INR)", "click_through_revenue_inr"),
)

# Native Web Engage rate columns. Recorded here so the engine can prove it never
# uses them as the DFIP rate. They are evidence fields only (M1.4).
NATIVE_RATE_HEADERS: tuple[str, ...] = (
    "Unique Control Group Conversion Rate",
    "Failed Rate",
    "Queued Rate",
    "Delivered Rate",
    "Unique Impression Rate",
    "Unique Click Rate",
    "Unique Conversion Rate",
    "Unique Impression-Through Conversion Rate",
    "Unique Click-Through Conversion Rate",
)

_STRICT_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


class RowValidationError(Exception):
    """A staged row cannot become a fact row. Carries a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class SourceFields:
    campaign_id: str
    day: date
    variation_id: str | None
    campaign_name: str | None
    variation_name: str | None
    channel: str | None
    type_of_campaign: str | None
    template_name_whatsapp: str | None
    start_date: datetime | None
    integers: dict[str, int | None]
    decimals: dict[str, Decimal | None]


def _text(raw: dict[str, Any], header: str) -> str | None:
    """Exact source text. No trim, no case change."""
    value = raw.get(header)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _require_nonblank(value: str | None, reason_code: str, header: str) -> str:
    if value is None or value == "":
        raise RowValidationError(reason_code, f"{header} is blank and is part of the fact key")
    return value


def parse_integer(value: Any, header: str) -> int | None:
    """Blank stays blank. Malformed values are rejected, never coerced."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise RowValidationError("INVALID_NUMERIC", f"{header} is boolean, not a count")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise RowValidationError("INVALID_NUMERIC", f"{header}={value!r} is not a whole number")
    if isinstance(value, str) and _STRICT_NUMBER.match(value):
        number = Decimal(value)
        if number == number.to_integral_value():
            return int(number)
        raise RowValidationError("INVALID_NUMERIC", f"{header}={value!r} is not a whole number")
    raise RowValidationError("INVALID_NUMERIC", f"{header}={value!r} is not numeric")


def parse_decimal(value: Any, header: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise RowValidationError("INVALID_NUMERIC", f"{header} is boolean, not an amount")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str) and _STRICT_NUMBER.match(value):
        try:
            return Decimal(value)
        except InvalidOperation as exc:  # pragma: no cover - regex already guards
            raise RowValidationError("INVALID_NUMERIC", f"{header}={value!r}") from exc
    raise RowValidationError("INVALID_NUMERIC", f"{header}={value!r} is not numeric")


def parse_day(value: Any) -> date:
    if value is None or value == "":
        raise RowValidationError("MISSING_DAY", "Day is blank and is part of the fact key")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError as exc:
            raise RowValidationError("INVALID_DAY", f"Day={value!r} is not an ISO date") from exc
    raise RowValidationError("INVALID_DAY", f"Day={value!r} is not a date")


def parse_start_date(value: Any) -> datetime | None:
    """Start Date is informational. Unparseable values become NULL, not errors."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def extract_source_fields(raw: dict[str, Any]) -> SourceFields:
    """Pull the fields P4 needs out of the preserved 57-column payload.

    Approved extra keys on ``raw`` (RUN 009) are ignored. They are not
    business metrics and are not copied onto ``FactRecord``.
    """
    campaign_id = _require_nonblank(_text(raw, "Campaign ID"), "MISSING_CAMPAIGN_ID", "Campaign ID")
    day = parse_day(raw.get("Day"))
    variation_id = _text(raw, "Variation ID")
    texts = {name: _text(raw, header) for header, name in TEXT_FIELDS}
    integers = {name: parse_integer(raw.get(header), header) for header, name in INTEGER_FIELDS}
    decimals = {name: parse_decimal(raw.get(header), header) for header, name in DECIMAL_FIELDS}
    return SourceFields(
        campaign_id=campaign_id,
        day=day,
        variation_id=variation_id,
        campaign_name=texts["campaign_name"],
        variation_name=texts["variation_name"],
        channel=texts["channel"],
        type_of_campaign=texts["type_of_campaign"],
        template_name_whatsapp=texts["template_name_whatsapp"],
        start_date=parse_start_date(raw.get("Start Date")),
        integers=integers,
        decimals=decimals,
    )
