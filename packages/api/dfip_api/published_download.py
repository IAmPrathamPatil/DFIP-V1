"""Render the published FactResponse slice as CSV or XLSX bytes.

This is a download of publication_current only. It is not a working-set dump
and does not change GET /publications/current/facts pagination.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from openpyxl import Workbook

from dfip_api.schemas import FactResponse

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
