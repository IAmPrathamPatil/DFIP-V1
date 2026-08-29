"""P9 PublishedFacts scale: pagination contract, formula range, no-secret, P8 cap."""

from __future__ import annotations

from datetime import date
from zipfile import ZipFile

from dfip_config.settings import Settings
from dfip_core.transform.fact import FactRecord
from dfip_web.client_workbook import FACT_HEADERS, M_PATH, XLSX_PATH, mashup_text
from dfip_web.daily_report import (
    CLIENT_WORKBOOK_SHEET_NAMES,
    REPORT_CONTRACTS,
    REPORT_SHEET_NAMES,
    derived_formula,
    report_formula,
)
from dfip_web.published_facts_pages import (
    PUBLISHED_FACTS_PAGE_LIMIT,
    PUBLISHED_FACTS_RELATIVE_PATH,
    published_facts_combine,
    published_facts_last_page_size,
    published_facts_page_offsets,
)
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_p5_api import AUTH, CLIENT_ID, RUN_A, make_settings
from test_p7_publication import (
    FORBIDDEN_TEMPLATE_TOKENS,
    PUBLISHED_FACTS_PATH,
    WORKING_SET_FACTS_PATH,
    _omits_working_set_facts_url,
    _publish,
    publisher_app,
)
from test_p8_client_report import _campaign_ids, _client_report_path, _sheet_names

AUGUST_PUBLISHED_FACTS_SCALE = 50_286


def test_publishedfacts_m_pages_current_facts_at_200() -> None:
    mashup = mashup_text()
    disk = M_PATH.read_text(encoding="utf-8")
    assert mashup == disk
    assert "PageLimit = 200" in mashup
    assert PUBLISHED_FACTS_RELATIVE_PATH in mashup
    assert PUBLISHED_FACTS_PATH in mashup
    assert _omits_working_set_facts_url(mashup)
    assert "offset" in mashup
    assert "RoundDown((Total - 1) / PageLimit)" in mashup
    assert WORKING_SET_FACTS_PATH not in mashup.replace(PUBLISHED_FACTS_PATH, "")
    assert "/qa-findings" not in mashup
    assert "/source-files" not in mashup
    assert "stg_source_row" not in mashup
    for token in FORBIDDEN_TEMPLATE_TOKENS:
        assert token not in mashup


def test_publishedfacts_page_plan_covers_august_scale_without_gaps_or_dupes() -> None:
    assert PUBLISHED_FACTS_PAGE_LIMIT == 200
    assert published_facts_page_offsets(0) == ()
    assert published_facts_page_offsets(200) == (0,)
    assert published_facts_page_offsets(201) == (0, 200)
    offsets = published_facts_page_offsets(AUGUST_PUBLISHED_FACTS_SCALE)
    assert offsets[0] == 0
    assert offsets[1] == 200
    assert offsets[-1] == 50_200
    assert len(offsets) == 252
    assert published_facts_last_page_size(AUGUST_PUBLISHED_FACTS_SCALE) == 86
    rows = list(range(AUGUST_PUBLISHED_FACTS_SCALE))
    combined = published_facts_combine(rows)
    assert combined == rows
    assert len(combined) == AUGUST_PUBLISHED_FACTS_SCALE
    assert len(set(combined)) == AUGUST_PUBLISHED_FACTS_SCALE


def test_report_formulas_span_full_publishedfacts_columns_not_a_fixed_row_cap() -> None:
    overall = REPORT_CONTRACTS[0]
    group = report_formula(overall)
    assert "PublishedFacts!$1:$1" in group
    assert "PublishedFacts!$A:$XFD" in group
    assert "SEQUENCE(nRows)" in group
    assert "1048576" not in group
    for contract in REPORT_CONTRACTS:
        ratio = derived_formula(contract, ("Delivery Rate", "ratio", "Delivered", "Sent"))
        assert "IFERROR" in ratio
        assert "#DIV/0!" not in ratio


def test_tracked_workbook_nine_sheets_token_free_and_query_bound() -> None:
    workbook = load_workbook(XLSX_PATH, read_only=True, data_only=False)
    assert workbook.sheetnames == list(CLIENT_WORKBOOK_SHEET_NAMES)
    assert workbook.sheetnames[2:] == list(REPORT_SHEET_NAMES)
    facts = workbook["Facts"]
    assert facts["A3"].value == "BearerToken"
    assert facts["B3"].value in {None, ""}
    workbook.close()
    with ZipFile(XLSX_PATH) as archive:
        connections = archive.read("xl/connections.xml").decode("utf-8")
        blob = b"".join(archive.read(name) for name in archive.namelist())
    assert "Location=PublishedFacts" in connections
    text = blob.decode("utf-8", errors="ignore")
    for token in FORBIDDEN_TEMPLATE_TOKENS:
        assert token not in text
    assert "DFIP_DEV_AUTH_TOKEN" not in text
    assert "DFIP_AUTH_SECRET" not in text
    headers = list(FACT_HEADERS)
    assert headers[10] == "Day"
    assert headers[12] == "Campaign ID"


def test_download_row_cap_covers_august_published_slice() -> None:
    settings = Settings(_env_file=None)
    assert settings.dfip_download_max_rows >= AUGUST_PUBLISHED_FACTS_SCALE
    assert make_settings().dfip_download_max_rows >= AUGUST_PUBLISHED_FACTS_SCALE


def test_http_published_facts_first_and_final_page_match_m_plan() -> None:
    app, _ingest, facts, _store = publisher_app()
    for index in range(201):
        record = FactRecord(
            client_id=CLIENT_ID,
            campaign_id=f"extra-{index:03d}",
            variation_id="var-x",
            variation_id_key="var-x",
            day=date(2025, 9, 1),
            processing_run_id=RUN_A,
        )
        facts.facts[record.key] = record
    http = TestClient(app)
    _publish(http)
    total = 204
    offsets = published_facts_page_offsets(total)
    assert offsets == (0, 200)
    first = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200, "offset": 0},
    ).json()
    last = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200, "offset": 200},
    ).json()
    assert first["pagination"]["total"] == total
    assert len(first["items"]) == 200
    assert len(last["items"]) == published_facts_last_page_size(total)
    keys = [
        (item["campaign_id"], item["day"], item["variation_id_key"])
        for item in first["items"] + last["items"]
    ]
    assert len(keys) == total
    assert len(set(keys)) == total
    working = http.get("/api/v1/facts?limit=200", headers=AUTH).json()
    assert working["pagination"]["total"] == total


def test_p8_client_report_still_downloads_and_stays_publication_scoped() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    _publish(http)
    report = http.get(
        _client_report_path(),
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    facts = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200},
    )
    assert report.status_code == 200
    assert facts.status_code == 200
    assert _sheet_names(report.content) == list(CLIENT_WORKBOOK_SHEET_NAMES)
    assert _campaign_ids(report.content) == ["camp-1", "camp-2", "camp-3"]
    assert facts.json()["pagination"]["total"] == 3
    with ZipFile(XLSX_PATH) as archive:
        mashup_names = archive.namelist()
    assert "xl/connections.xml" in mashup_names
