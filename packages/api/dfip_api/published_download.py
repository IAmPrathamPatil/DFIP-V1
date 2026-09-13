"""Render published facts as CSV or XLSX bytes.

``render_published_csv`` / ``render_published_xlsx`` are publication_current
downloads. ``render_history_facts_csv`` is the cumulative newest-wins history
used by Excel Refresh All. Neither changes JSON pagination contracts.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime

from openpyxl import Workbook

from dfip_api.schemas import FACT_TABLE_COLUMNS, FactResponse
from dfip_core.transform.fact import FactRecord

FACT_DOWNLOAD_COLUMNS: tuple[str, ...] = tuple(FactResponse.model_fields)
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def published_download_filename(client_id: str, publication_id: str | None, suffix: str) -> str:
    safe_client = client_id
    if publication_id:
        return f"published-facts-{safe_client}-{publication_id}.{suffix}"
    return f"published-facts-{safe_client}.{suffix}"


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def fact_download_row(item: FactResponse) -> list[str]:
    payload = item.model_dump()
    return [_cell(payload[name]) for name in FACT_DOWNLOAD_COLUMNS]


def render_published_csv(items: list[FactResponse]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FACT_DOWNLOAD_COLUMNS)
    for item in items:
        writer.writerow(fact_download_row(item))
    return buffer.getvalue().encode("utf-8")


def render_history_facts_csv(records: Sequence[FactRecord]) -> bytes:
    """Header row is FACT_TABLE_COLUMNS so PublishedFacts HeaderMap still matches."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FACT_TABLE_COLUMNS)
    for record in records:
        writer.writerow([_cell(getattr(record, name)) for name in FACT_TABLE_COLUMNS])
    return buffer.getvalue().encode("utf-8")


def render_published_xlsx(items: list[FactResponse]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Facts"
    sheet.append(list(FACT_DOWNLOAD_COLUMNS))
    for item in items:
        sheet.append(fact_download_row(item))
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
