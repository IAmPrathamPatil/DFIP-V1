"""P3 header contract for the 57 Web Engage source columns (Excel K:BO)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from dfip_db.catalog import (
    APPROVED_EXTRA_SOURCE_HEADERS,
    WEB_ENGAGE_SOURCE_COLUMNS,
    WEB_ENGAGE_SOURCE_HEADERS,
)
from openpyxl.utils import get_column_letter

PREFERRED_SHEET = "Web-Engage Raw"
HEADER_SCAN_ROWS = 10


@dataclass(frozen=True)
class HeaderMatch:
    header_row: int
    start_index: int  # 0-based
    headers: tuple[str, ...]
    extra_headers: tuple[str, ...] = ()

    @property
    def start_column_letter(self) -> str:
        return get_column_letter(self.start_index + 1)

    @property
    def source_kind(self) -> str:
        # Column K is excel_position 11 → 0-based index 10.
        if self.start_index == 10:
            return "legacy_workbook"
        return "native_export"


class HeaderContractError(Exception):
    def __init__(
        self,
        reason_code: str,
        detail: str,
        missing: tuple[str, ...] = (),
        unexpected: tuple[str, ...] = (),
        duplicates: tuple[str, ...] = (),
        observed: tuple[str | None, ...] = (),
    ) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail
        self.missing = missing
        self.unexpected = unexpected
        self.duplicates = duplicates
        self.observed = observed


def expected_source_headers() -> tuple[str, ...]:
    assert len(WEB_ENGAGE_SOURCE_HEADERS) == 57
    assert WEB_ENGAGE_SOURCE_COLUMNS[0].excel_letter == "K"
    assert WEB_ENGAGE_SOURCE_COLUMNS[-1].excel_letter == "BO"
    return WEB_ENGAGE_SOURCE_HEADERS


def _as_header(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value  # exact, no trim
    return str(value)


def duplicate_headers(headers: list[str | None]) -> tuple[str, ...]:
    counts = Counter(h for h in headers if h is not None)
    return tuple(name for name, count in counts.items() if count > 1)


def locate_source_headers(row_values: list[object]) -> int:
    """Return 0-based start index of the exact 57-header sequence.

    Does not shift or fuzzy-match. The sequence must be contiguous and exact.
    """
    expected = list(expected_source_headers())
    observed = [_as_header(value) for value in row_values]
    found: list[int] = []
    if len(observed) < 57:
        raise HeaderContractError(
            "INSUFFICIENT_COLUMNS",
            f"header row has {len(observed)} cells; 57 source headers are required",
            observed=tuple(observed),
        )
    for start in range(0, len(observed) - 56):
        window = observed[start : start + 57]
        if window == expected:
            found.append(start)
    if len(found) == 1:
        dups = duplicate_headers(observed[found[0] : found[0] + 57])
        if dups:
            raise HeaderContractError(
                "DUPLICATE_HEADERS",
                "duplicate headers inside the source region: " + ", ".join(dups),
                duplicates=dups,
                observed=tuple(observed),
            )
        return found[0]
    if len(found) > 1:
        raise HeaderContractError(
            "AMBIGUOUS_REGION",
            "the 57 source headers appear more than once on the header row",
            observed=tuple(observed),
        )
    kbo = observed[10:67] if len(observed) >= 67 else observed
    missing = tuple(h for h in expected if h not in observed)
    unexpected = tuple(h for h in kbo if h is not None and h not in expected)
    dups = duplicate_headers(observed)
    reason = "HEADER_CONTRACT"
    if dups:
        reason = "DUPLICATE_HEADERS"
    detail = (
        "exact 57-column source header sequence was not found. "
        f"missing={list(missing)} unexpected={list(unexpected)} duplicates={list(dups)}"
    )
    raise HeaderContractError(
        reason,
        detail,
        missing=missing,
        unexpected=unexpected,
        duplicates=dups,
        observed=tuple(kbo),
    )


def classify_trailing_headers(observed: list[str | None], start_index: int) -> tuple[str, ...]:
    """Headers immediately after the locked 57-column region.

    Trailing blank cells are ignored. Identity is the exact header text, not
    Excel letter. Approved extras are optional. Anything else fails closed.
    """
    rest = list(observed[start_index + 57 :])
    while rest and rest[-1] is None:
        rest.pop()
    extras: list[str] = []
    for header in rest:
        if header is None or header == "":
            raise HeaderContractError(
                "EMPTY_COLUMN_NAME",
                "Empty/invalid source column header",
                observed=tuple(observed[start_index : start_index + 57] + rest),
            )
        extras.append(header)
    if not extras:
        return ()
    dups = duplicate_headers(extras)
    clash = tuple(name for name in extras if name in expected_source_headers())
    if dups or clash:
        raise HeaderContractError(
            "DUPLICATE_HEADERS",
            "duplicate headers in or against the source region: "
            + ", ".join(dict.fromkeys((*dups, *clash))),
            duplicates=tuple(dict.fromkeys((*dups, *clash))),
            observed=tuple(observed),
        )
    unknown = tuple(name for name in extras if name not in APPROVED_EXTRA_SOURCE_HEADERS)
    if unknown:
        raise HeaderContractError(
            "UNAPPROVED_SOURCE_COLUMN",
            "Unapproved source column: " + ", ".join(unknown),
            unexpected=unknown,
            observed=tuple(observed),
        )
    return tuple(extras)


_SPECIFIC_HEADER_ERRORS = frozenset(
    {
        "DUPLICATE_HEADERS",
        "UNAPPROVED_SOURCE_COLUMN",
        "EMPTY_COLUMN_NAME",
        "AMBIGUOUS_REGION",
    }
)


def _prefer_header_error(
    current: HeaderContractError | None, new: HeaderContractError
) -> HeaderContractError:
    """Keep a specific contract error instead of a later data-row HEADER_CONTRACT."""
    if current is None:
        return new
    if (
        current.reason_code in _SPECIFIC_HEADER_ERRORS
        and new.reason_code not in _SPECIFIC_HEADER_ERRORS
    ):
        return current
    return new


def match_header_rows(rows: list[tuple[int, list[object]]]) -> HeaderMatch:
    last_error: HeaderContractError | None = None
    for excel_row, values in rows:
        try:
            start = locate_source_headers(values)
            observed = [_as_header(value) for value in values]
            extras = classify_trailing_headers(observed, start)
        except HeaderContractError as exc:
            last_error = _prefer_header_error(last_error, exc)
            continue
        return HeaderMatch(
            header_row=excel_row,
            start_index=start,
            headers=expected_source_headers(),
            extra_headers=extras,
        )
    if last_error is not None:
        raise last_error
    raise HeaderContractError("HEADER_CONTRACT", "no header row found in the scanned region")
