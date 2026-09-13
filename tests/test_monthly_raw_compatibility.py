"""Monthly Web-Engage Raw Prod workbooks vs ZIP/header contract.

Does not modify the workbooks. Files are gitignored; tests skip when absent.
Does not publish.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dfip_api.upload_service import _assert_xlsx_payload
from dfip_core.ingest.headers import PREFERRED_SHEET, expected_source_headers
from dfip_core.ingest.reader import inspect_workbook
from dfip_db.paths import repo_root

PROD_MONTHLY = (
    ("Apr-May", "11. WE Report Raw Data Apr toi May 25 VJ Prod.xlsx"),
    ("June", "12. WE Report Raw Data June 25 VJ Prod.xlsx"),
    ("July", "13. WE Report Raw Data July 25 VJ Prod.xlsx"),
    ("August", "14. WE Report Raw Data Aug-25 VJ Prod.xlsx"),
    ("September", "14. WE Report Raw Data Sept-25 VJ Prod.xlsx"),
    ("October", "14. WE Report Raw Data Oct-25 VJ Prod.xlsx"),
)


def _prod_path(filename: str) -> Path:
    return repo_root() / filename


@pytest.mark.parametrize("month, filename", PROD_MONTHLY)
def test_monthly_prod_zip_and_run009_headers(month: str, filename: str) -> None:
    path = _prod_path(filename)
    if not path.is_file():
        pytest.skip(f"monthly workbook not present: {filename}")
    _assert_xlsx_payload(path.read_bytes())
    layout = inspect_workbook(path)
    assert layout.worksheet_name == PREFERRED_SHEET
    assert layout.match.header_row == 1
    assert layout.match.start_index == 10
    assert layout.match.headers == expected_source_headers()
    assert layout.match.extra_headers == ()
    assert PREFERRED_SHEET in layout.sheet_names
