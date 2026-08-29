"""Builders for Logic/Labels catalog workbooks (in-memory tests only)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook

LOGIC_HEADERS = (
    "Campaign Name",
    "Filter Logic 1",
    "Filter Logic 2",
    "AMC Status - Filter Logic 3",
    "AMC Device Category -  Filter Logic 4",
    "AMC Product Cat -  Filter Logic 5",
    "Manual Or Automated",
)
LABEL_HEADERS = ("Group Name", "Filter Logic 1")

V2C_CAMPAIGN = "V2C Unique Campaign"
V2C_FL1 = "V2C FL1 Unique"
V2C_FL2 = "V2C FL2"
V2C_FL3 = "V2C FL3"
V2C_FL4 = "V2C FL4"
V2C_FL5 = "V2C FL5"
V2C_MANUAL = "Manual"
V2C_GROUP = "V2C-Group"


def logic_rows(**overrides: Any) -> list[dict[str, object]]:
    row: dict[str, object] = {
        "Campaign Name": V2C_CAMPAIGN,
        "Filter Logic 1": V2C_FL1,
        "Filter Logic 2": V2C_FL2,
        "AMC Status - Filter Logic 3": V2C_FL3,
        "AMC Device Category -  Filter Logic 4": V2C_FL4,
        "AMC Product Cat -  Filter Logic 5": V2C_FL5,
        "Manual Or Automated": V2C_MANUAL,
    }
    row.update(overrides)
    return [row]


def labels_rows(**overrides: Any) -> list[dict[str, object]]:
    row: dict[str, object] = {
        "Group Name": V2C_GROUP,
        "Filter Logic 1": V2C_FL1,
    }
    row.update(overrides)
    return [row]


def catalog_xlsx(
    headers: tuple[str, ...],
    rows: list[dict[str, object]],
    path: Path | None = None,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(headers))
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    if path is not None:
        workbook.save(path)
        workbook.close()
        return path.read_bytes()
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def logic_xlsx(rows: list[dict[str, object]] | None = None, path: Path | None = None) -> bytes:
    return catalog_xlsx(LOGIC_HEADERS, rows if rows is not None else logic_rows(), path)


def labels_xlsx(rows: list[dict[str, object]] | None = None, path: Path | None = None) -> bytes:
    return catalog_xlsx(LABEL_HEADERS, rows if rows is not None else labels_rows(), path)
