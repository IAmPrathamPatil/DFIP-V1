"""D2 global filter allowlist and period resolution.

Dimension names are server-side constants. Clients cannot inject SQL fields.
Period windows use `Day` (same month identity as derive.month_start).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from dfip_core.transform.derive import month_label, month_start as month_of

PeriodGrain = Literal["month", "range", "all_history"]
CompareMode = Literal["auto", "none"]

MAX_MULTI_VALUES = 500
BLANK_VALUE = ""

REASON_NO_HISTORY = "no_published_history"
REASON_NO_PRIOR = "no_prior_published_month"
REASON_COMPARISON_DISABLED = "comparison_disabled"
REASON_COMPARISON_NOT_APPLICABLE = "comparison_not_applicable"
REASON_NO_COMPARABLE_RANGE = "no_comparable_published_range"

ALLOWED_DIMENSIONS: tuple[str, ...] = (
    "campaign_id",
    "channel",
    "filter_logic_1",
    "filter_logic_1_group",
)

DIMENSION_COLUMNS: dict[str, str] = {name: name for name in ALLOWED_DIMENSIONS}


class FilterValidationError(ValueError):
    """422-class filter/period error. Message is safe for clients."""


@dataclass(frozen=True)
class DimensionFilters:
    campaign_ids: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    filter_logic_1: tuple[str, ...] = ()
    filter_logic_1_group: tuple[str, ...] = ()

    def any(self) -> bool:
        return bool(
            self.campaign_ids or self.channels or self.filter_logic_1 or self.filter_logic_1_group
        )


@dataclass(frozen=True)
class PeriodWindow:
    grain: PeriodGrain
    day_from: date
    day_to_exclusive: date
    month_start: date | None
    month_label: str | None

    @property
    def inclusive_to(self) -> date:
        return self.day_to_exclusive - timedelta(days=1)


@dataclass
class ResolvedOverviewQuery:
    period: PeriodWindow
    comparison: PeriodWindow | None
    compare_mode: CompareMode
    comparison_reason: str | None
    filters: DimensionFilters
    dropped: dict[str, tuple[str, ...]] = field(default_factory=dict)
    is_default: bool = True


def add_calendar_month(month: date) -> date:
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


def month_window(month: date) -> PeriodWindow:
    start = month_of(month)
    return PeriodWindow(
        grain="month",
        day_from=start,
        day_to_exclusive=add_calendar_month(start),
        month_start=start,
        month_label=month_label(start),
    )


def range_window(day_from: date, inclusive_to: date) -> PeriodWindow:
    if inclusive_to < day_from:
        raise FilterValidationError("Date range end must be on or after the start.")
    start_month = month_of(day_from)
    end_month = month_of(inclusive_to)
    single = start_month == end_month
    return PeriodWindow(
        grain="month" if single else "range",
        day_from=day_from,
        day_to_exclusive=inclusive_to + timedelta(days=1),
        month_start=start_month if single else None,
        month_label=month_label(start_month) if single else None,
    )


def normalize_multi(values: list[str] | None, *, label: str) -> tuple[str, ...]:
    if not values:
        return ()
    if len(values) > MAX_MULTI_VALUES:
        raise FilterValidationError(f"At most {MAX_MULTI_VALUES} {label} values can be selected.")
    seen: list[str] = []
    for item in values:
        text = "" if item is None else str(item)
        if text not in seen:
            seen.append(text)
    return tuple(seen)


def drop_unknown(
    selected: tuple[str, ...], allowed: set[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    kept = tuple(item for item in selected if item in allowed)
    dropped = tuple(item for item in selected if item not in allowed)
    return kept, dropped


def _clip(value: date, lo: date, hi: date) -> date:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def resolve_period(
    *,
    months: list[date],
    published_min: date | None,
    published_max: date | None,
    period: str | None,
    month_start: date | None,
    day_from: date | None,
    day_to: date | None,
) -> tuple[PeriodWindow, bool]:
    """Return the current window and whether it is the D1 default latest month."""
    unique = sorted({item for item in months if item is not None})
    if not unique or published_min is None or published_max is None:
        raise FilterValidationError("No published history is available.")
    grain = (period or "").strip().lower() or None
    if grain not in {None, "month", "range", "all", "all_history"}:
        raise FilterValidationError("period must be month, range, or all_history.")
    if grain == "all":
        grain = "all_history"

    if grain == "all_history":
        return (
            PeriodWindow(
                grain="all_history",
                day_from=published_min,
                day_to_exclusive=published_max + timedelta(days=1),
                month_start=None,
                month_label=None,
            ),
            False,
        )

    if grain == "range" or (
        grain is None and (day_from is not None or day_to is not None) and month_start is None
    ):
        if day_from is None or day_to is None:
            raise FilterValidationError("Date range requires both from and to.")
        start = _clip(day_from, published_min, published_max)
        end = _clip(day_to, published_min, published_max)
        window = range_window(start, end)
        if grain == "range":
            window = PeriodWindow(
                grain="range",
                day_from=window.day_from,
                day_to_exclusive=window.day_to_exclusive,
                month_start=window.month_start,
                month_label=window.month_label,
            )
        return window, False

    if month_start is not None:
        normalized = month_of(month_start)
        if normalized != month_start:
            raise FilterValidationError("month_start must be the first day of a published month.")
        if normalized not in unique:
            raise FilterValidationError("month_start is not a published month for this company.")
        return month_window(normalized), False

    if grain in {None, "month"}:
        return month_window(unique[-1]), grain is None and day_from is None and day_to is None

    raise FilterValidationError("period must be month, range, or all_history.")


def resolve_comparison(
    *,
    months: list[date],
    current: PeriodWindow,
    compare: str | None,
    compare_month_start: date | None,
    compare_from: date | None,
    compare_to: date | None,
    published_min: date | None,
    published_max: date | None,
) -> tuple[PeriodWindow | None, CompareMode, str | None]:
    unique = sorted({item for item in months if item is not None})
    mode_raw = (compare or "auto").strip().lower()
    if mode_raw not in {"auto", "none"}:
        raise FilterValidationError("compare must be auto or none.")
    mode: CompareMode = "none" if mode_raw == "none" else "auto"

    if mode == "none":
        return None, "none", REASON_COMPARISON_DISABLED

    if compare_month_start is not None or compare_from is not None or compare_to is not None:
        if compare_month_start is not None:
            normalized = month_of(compare_month_start)
            if normalized != compare_month_start or normalized not in unique:
                raise FilterValidationError(
                    "compare_month_start is not a published month for this company."
                )
            window = month_window(normalized)
            if (
                window.day_from == current.day_from
                and window.day_to_exclusive == current.day_to_exclusive
            ):
                raise FilterValidationError(
                    "Comparison period must differ from the selected period."
                )
            return window, mode, None
        if (
            compare_from is None
            or compare_to is None
            or published_min is None
            or published_max is None
        ):
            raise FilterValidationError(
                "Comparison range requires both compare_from and compare_to."
            )
        start = _clip(compare_from, published_min, published_max)
        end = _clip(compare_to, published_min, published_max)
        window = range_window(start, end)
        window = PeriodWindow(
            grain="range",
            day_from=window.day_from,
            day_to_exclusive=window.day_to_exclusive,
            month_start=window.month_start,
            month_label=window.month_label,
        )
        if (
            window.day_from == current.day_from
            and window.day_to_exclusive == current.day_to_exclusive
        ):
            raise FilterValidationError("Comparison period must differ from the selected period.")
        return window, mode, None

    if current.grain != "month" or current.month_start is None:
        return None, "auto", REASON_COMPARISON_NOT_APPLICABLE

    _, prior = _latest_and_prior(unique, current.month_start)
    if prior is None:
        return None, "auto", REASON_NO_PRIOR
    return month_window(prior), "auto", None


def _latest_and_prior(months: list[date], selected: date) -> tuple[date | None, date | None]:
    unique = sorted({item for item in months if item is not None})
    if selected not in unique:
        return selected, None
    index = unique.index(selected)
    prior = unique[index - 1] if index > 0 else None
    return selected, prior


def matches_text_filter(value: str | None, selected: tuple[str, ...]) -> bool:
    if not selected:
        return True
    text = "" if value is None else value
    return text in selected
