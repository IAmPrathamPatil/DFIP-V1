"""R10: cumulative published history for the refreshable workbook.

Does not change publication_current, static snapshots, or working-set /facts.
Does not embed credentials. PostgreSQL cases require DFIP_TEST_DATABASE_URL.
"""

from __future__ import annotations

import io
import re
from datetime import date
from zipfile import ZipFile

from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.transform.fact import FactRecord
from dfip_web.client_report_download import (
    ARTIFACT_REFRESHABLE,
    ARTIFACT_STATIC,
    render_client_report_xlsx,
)
from dfip_web.client_workbook import FACT_HEADERS, XLSX_PATH, mashup_text
from dfip_web.daily_report import (
    MEASURE_HEADERS,
    OVERALL,
    REPORT_SHEET_NAMES,
    SERVICE_FILTER_LOGIC_1,
    _sheet_part_map,
)
from dfip_web.pivot_report import (
    PIVOT_CACHE_PART,
    PIVOT_CACHE_QUERY_NAME,
    cache_field_index,
    sum_measure_headers,
)
from dfip_web.published_facts_mashup import (
    DATAMASHUP_PART,
    HISTORY_FACTS_CSV_RELATIVE_PATH,
    extract_published_facts_section_m,
    _type_month_from_month_start,
    _revert_month_date_typing,
    _RENAMED_RESULT_TAIL,
    _SHRINKABLE_COMMENTS,
)
from dfip_web.slicer_defaults import selected_slicer_values
from fastapi.testclient import TestClient

from postgres_support import (
    postgres_only,
    requires_postgres,
    sample_fact,
    seed_working_set,
)
from test_p5_api import AUTH, CLIENT_ID, DEV_TOKEN, JWT_SECRET, RUN_A, _encode_jwt, make_settings
from test_p7_publication import _publish, jwt_app, publisher_app
from test_p8_client_report import _campaign_ids, _settings_value
from test_p11_pivot_report import _published_fact

CLIENT_B = "a0000000-0000-4000-8000-000000000002"
HISTORY_PATH = "/api/v1/publications/history/facts"
CURRENT_PATH = "/api/v1/publications/current/facts"


def _fact(
    *,
    client_id: str,
    campaign_id: str,
    day: date,
    month_label: str,
    sent: int,
    unique_conversions: int,
    unique_click_through_conversions: int,
    processing_run_id: str = RUN_A,
) -> FactRecord:
    return FactRecord(
        client_id=client_id,
        campaign_id=campaign_id,
        variation_id="var-1",
        variation_id_key="var-1",
        day=day,
        month_start=day.replace(day=1),
        month_label=month_label,
        sent=sent,
        unique_conversions=unique_conversions,
        unique_click_through_conversions=unique_click_through_conversions,
        processing_run_id=processing_run_id,
    )


def _publish_snapshot(store: InMemoryPublicationStore, facts: list[FactRecord], *, run_id: str):
    return store.create(
        client_id=facts[0].client_id,
        processing_run_id=run_id,
        period_start=min(item.day for item in facts),
        period_end=max(item.day for item in facts),
        published_by="publisher",
        notes=None,
        snapshot_facts=facts,
    )


def test_disk_and_packaged_mashup_page_history_facts() -> None:
    mashup = mashup_text()
    assert HISTORY_FACTS_CSV_RELATIVE_PATH in mashup
    assert CURRENT_PATH not in mashup
    assert "Date.From([month_start])" in mashup
    assert "Csv.Document" in mashup
    with ZipFile(XLSX_PATH) as archive:
        section = extract_published_facts_section_m(archive.read(DATAMASHUP_PART))
    assert HISTORY_FACTS_CSV_RELATIVE_PATH in section
    assert CURRENT_PATH not in section
    assert "CanonicalKeys = Record.FieldNames(HeaderMap)" in section
    assert "Csv.Document" in section
    assert "layout = \"table\"" not in section
    assert "Date.From([month_start])" in section
    assert "Chrono" in section


def test_month_date_typing_keeps_section1_length() -> None:
    body = (
        b"section Section1;\r\nshared PublishedFacts = // x\r\n"
        + _SHRINKABLE_COMMENTS
        + b"let\r\n                Renamed = Table.RenameColumns(\r\n"
        b"                    Decimals,\r\n                    "
        + _RENAMED_RESULT_TAIL
        + b"in\r\n    Facts\r\n;"
    )
    typed = _type_month_from_month_start(body)
    assert len(typed) == len(body)
    assert b"Date.From([month_start])" in typed
    reverted = _revert_month_date_typing(typed)
    assert reverted == body
    assert _type_month_from_month_start(typed) == typed


def test_history_unions_months_and_current_stays_latest() -> None:
    app, _ingest, _facts, store = publisher_app()
    oct_fact = _fact(
        client_id=CLIENT_ID,
        campaign_id="camp-oct",
        day=date(2025, 10, 1),
        month_label="Oct-25",
        sent=10,
        unique_conversions=2,
        unique_click_through_conversions=1,
        processing_run_id="run-oct",
    )
    sep_fact = _fact(
        client_id=CLIENT_ID,
        campaign_id="camp-sep",
        day=date(2025, 9, 2),
        month_label="Sep-25",
        sent=20,
        unique_conversions=4,
        unique_click_through_conversions=3,
        processing_run_id="run-sep",
    )
    _publish_snapshot(store, [oct_fact], run_id="run-oct")
    _publish_snapshot(store, [sep_fact], run_id="run-sep")
    http = TestClient(app)
    current = http.get(CURRENT_PATH, headers=AUTH, params={"client_id": CLIENT_ID, "limit": 200})
    history = http.get(HISTORY_PATH, headers=AUTH, params={"client_id": CLIENT_ID, "limit": 200})
    assert current.status_code == 200
    assert history.status_code == 200
    assert current.json()["pagination"]["total"] == 1
    assert current.json()["items"][0]["campaign_id"] == "camp-sep"
    assert history.json()["pagination"]["total"] == 2
    campaigns = {item["campaign_id"] for item in history.json()["items"]}
    months = {item["month_label"] for item in history.json()["items"]}
    assert campaigns == {"camp-oct", "camp-sep"}
    assert months == {"Oct-25", "Sep-25"}
    by_campaign = {item["campaign_id"]: item for item in history.json()["items"]}
    assert by_campaign["camp-sep"]["unique_click_through_conversions"] == 3
    assert by_campaign["camp-sep"]["unique_conversions"] == 4
    assert by_campaign["camp-oct"]["unique_click_through_conversions"] == 1
    assert by_campaign["camp-oct"]["unique_conversions"] == 2


def test_history_republish_keeps_one_row_per_grain() -> None:
    app, _ingest, _facts, store = publisher_app()
    first = _fact(
        client_id=CLIENT_ID,
        campaign_id="camp-sep",
        day=date(2025, 9, 1),
        month_label="Sep-25",
        sent=10,
        unique_conversions=1,
        unique_click_through_conversions=1,
    )
    restated = _fact(
        client_id=CLIENT_ID,
        campaign_id="camp-sep",
        day=date(2025, 9, 1),
        month_label="Sep-25",
        sent=99,
        unique_conversions=8,
        unique_click_through_conversions=7,
        processing_run_id="run-sep-b",
    )
    _publish_snapshot(store, [first], run_id=RUN_A)
    _publish_snapshot(store, [restated], run_id="run-sep-b")
    http = TestClient(app)
    history = http.get(HISTORY_PATH, headers=AUTH, params={"client_id": CLIENT_ID, "limit": 200})
    assert history.status_code == 200
    items = history.json()["items"]
    assert history.json()["pagination"]["total"] == 1
    assert items[0]["sent"] == 99
    assert items[0]["unique_conversions"] == 8
    assert items[0]["unique_click_through_conversions"] == 7


def test_history_is_tenant_scoped() -> None:
    app, _ingest, _facts, store = publisher_app()
    _publish_snapshot(
        store,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=date(2025, 10, 1),
                month_label="Oct-25",
                sent=1,
                unique_conversions=1,
                unique_click_through_conversions=1,
            )
        ],
        run_id="run-a",
    )
    _publish_snapshot(
        store,
        [
            _fact(
                client_id=CLIENT_B,
                campaign_id="camp-b",
                day=date(2025, 10, 1),
                month_label="Oct-25",
                sent=2,
                unique_conversions=2,
                unique_click_through_conversions=2,
            )
        ],
        run_id="run-b",
    )
    http = TestClient(app)
    own = http.get(HISTORY_PATH, headers=AUTH, params={"client_id": CLIENT_ID, "limit": 200})
    other = http.get(HISTORY_PATH, headers=AUTH, params={"client_id": CLIENT_B, "limit": 200})
    assert {item["campaign_id"] for item in own.json()["items"]} == {"camp-a"}
    assert {item["campaign_id"] for item in other.json()["items"]} == {"camp-b"}
    from dfip_api.app import create_app

    bound = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=_ingest,
        fact_store=_facts,
        publication_store=store,
    )
    headers = {"Authorization": f"Bearer {_encode_jwt(role='client', client_id=CLIENT_ID)}"}
    jwt_http = TestClient(bound)
    leaked = jwt_http.get(HISTORY_PATH, headers=headers, params={"client_id": CLIENT_B})
    assert leaked.status_code == 403
    scoped = jwt_http.get(HISTORY_PATH, headers=headers)
    assert scoped.status_code == 200
    assert {item["campaign_id"] for item in scoped.json()["items"]} == {"camp-a"}


def test_history_omits_working_set_and_requires_auth() -> None:
    app, _ingest, facts, store = publisher_app()
    http = TestClient(app)
    assert http.get(HISTORY_PATH).status_code == 401
    empty = http.get(HISTORY_PATH, headers=AUTH, params={"client_id": CLIENT_ID})
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["pagination"]["total"] == 0
    created = _publish(http)
    assert created.status_code == 201
    published = http.get(HISTORY_PATH, headers=AUTH, params={"client_id": CLIENT_ID, "limit": 200})
    working = http.get("/api/v1/facts", headers=AUTH, params={"limit": 200})
    assert published.json()["pagination"]["total"] == 3
    assert working.json()["pagination"]["total"] == 3
    assert "/api/v1/facts" not in mashup_text().replace(HISTORY_FACTS_CSV_RELATIVE_PATH, "")


def test_refreshable_stamp_keeps_conversion_metrics_and_growing_cache() -> None:
    rows = [
        _published_fact(
            campaign_id="camp-oct",
            month_label="Oct-25",
            unique_click_through_conversions=11,
            unique_conversions=13,
        )
    ]
    body = render_client_report_xlsx(
        rows,
        published_at=None,
        client_id=CLIENT_ID,
        client_code="eureka",
        client_name="Eureka Forbes",
        publication_id="p0000000-0000-4000-8000-000000000099",
        artifact=ARTIFACT_REFRESHABLE,
        api_base_url="http://127.0.0.1:8000",
    )
    with ZipFile(io.BytesIO(body)) as archive:
        published = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
        mashup = extract_published_facts_section_m(archive.read(DATAMASHUP_PART))
        facts = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
        overall = archive.read("xl/pivotTables/pivotTable1.xml").decode("utf-8")
        service = archive.read("xl/pivotTables/pivotTable9.xml").decode("utf-8")
        query_table = archive.read("xl/queryTables/queryTable1.xml").decode("utf-8")
    assert ">11<" in published
    assert ">13<" in published
    assert f'name="{PIVOT_CACHE_QUERY_NAME}"' in cache
    assert "worksheetSource ref=" not in cache
    assert "A1:AS1048576" not in cache
    with ZipFile(io.BytesIO(body)) as archive:
        wbxml = archive.read("xl/workbook.xml").decode("utf-8")
    assert "PublishedFacts!$A$1:$AS$2" in wbxml
    assert "PublishedFacts!$A$1</definedName>" not in wbxml
    assert "Unique Click-Through Conversions" in overall
    assert "Unique Conversions" in overall
    assert overall.count("<dataField ") == len(sum_measure_headers())
    assert overall.count('dataField="1"') == len(sum_measure_headers())
    assert 'ref="B9:L10"' in overall
    assert "Failed Rate SM" not in overall
    assert "Overall ROAS" not in overall
    with ZipFile(io.BytesIO(body)) as archive:
        overall_sheet = archive.read(
            _sheet_part_map(
                archive.read("xl/workbook.xml").decode("utf-8"),
                archive.read("xl/_rels/workbook.xml.rels").decode("utf-8"),
            )[OVERALL]
        ).decode("utf-8")
    assert 'r="O10"' in overall_sheet
    assert 'r="T10"' in overall_sheet
    assert 'r="X10"' in overall_sheet
    assert 't="shared"' in overall_sheet
    assert "ISNUMBER(I10)" in overall_sheet
    assert "ISNUMBER(K10)" in overall_sheet
    assert 'r="O100"' in overall_sheet
    assert f'<pageField fld="{cache_field_index("Filter Logic 1")}"' not in service
    assert "Service | FMS" in service
    assert re.search(r'(?<![A-Za-z])backgroundRefresh="0"', query_table)
    assert "overwriteClear" in query_table
    with ZipFile(io.BytesIO(body)) as archive:
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        rels_xml = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        parts = _sheet_part_map(workbook_xml, rels_xml)
        for index in range(1, 10):
            pivot_xml = archive.read(f"xl/pivotTables/pivotTable{index}.xml").decode("utf-8")
            assert pivot_xml.count("<dataField ") == len(sum_measure_headers())
            assert pivot_xml.count('dataField="1"') == len(sum_measure_headers())
            assert 'ref="B9:L10"' in pivot_xml
        for name in REPORT_SHEET_NAMES:
            sheet_xml = archive.read(parts[name]).decode("utf-8")
            assert 'r="O10"' in sheet_xml
            assert 'r="T10"' in sheet_xml
            assert "ISNUMBER(I10)" in sheet_xml
            assert 't="shared"' in sheet_xml
    with ZipFile(io.BytesIO(body)) as archive:
        names = set(archive.namelist())
        types = archive.read("[Content_Types].xml").decode("utf-8")
    assert "xl/vbaProject.bin" in names
    assert "macroEnabled" in types
    from dfip_web.measure_restore_vba import measure_restore_vba_source

    vba = measure_restore_vba_source()
    for header in MEASURE_HEADERS:
        assert header in vba
    assert "Public Sub Auto_Open()" in vba
    assert "DisableBackgroundQuery" in vba
    assert "BackgroundQuery = False" in vba
    assert "RefreshPivotCaches" in vba
    from dfip_web.measure_restore_vba import app_events_vba_source

    events = app_events_vba_source()
    assert "App_SheetPivotTableUpdate" in events
    assert "QT_AfterRefresh" in events
    assert "BackgroundQuery = False" in events or "DisableBackgroundQuery" in events
    assert HISTORY_FACTS_CSV_RELATIVE_PATH in mashup
    assert "password" not in facts.lower()
    assert "REFRESH REQUIRED" in facts
    assert "Oct-25" in facts
    static = render_client_report_xlsx(
        rows,
        published_at=None,
        client_id=CLIENT_ID,
        client_code="eureka",
        client_name="Eureka Forbes",
        publication_id="p0000000-0000-4000-8000-000000000099",
        artifact=ARTIFACT_STATIC,
    )
    with ZipFile(io.BytesIO(static)) as archive:
        static_cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
        static_names = set(archive.namelist())
        static_overall = archive.read("xl/pivotTables/pivotTable1.xml").decode("utf-8")
    assert "xl/vbaProject.bin" not in static_names
    assert static_overall.count("<dataField ") == len(MEASURE_HEADERS)
    assert "Failed Rate SM" in static_overall
    assert "Overall ROAS" in static_overall
    assert "xl/vbaProject.bin" not in static_names
    assert "A1:AS2" in static_cache
    assert 'name="ExternalData_1"' not in static_cache
    assert "A1:AS1048576" not in static_cache
    assert _campaign_ids(body) == ["camp-oct"]
    assert "Unique Click-Through Conversions" in FACT_HEADERS
    assert "Unique Conversions" in FACT_HEADERS
    assert len(REPORT_SHEET_NAMES) == 9


def test_refreshable_does_not_embed_permanent_credentials() -> None:
    body = render_client_report_xlsx(
        [_published_fact(campaign_id="camp-x")],
        published_at=None,
        client_id=CLIENT_ID,
        client_code="default",
        client_name="Default",
        artifact=ARTIFACT_REFRESHABLE,
        api_base_url="http://127.0.0.1:8000",
    )
    text = body.decode("latin-1")
    assert "DFIP_AUTH_SECRET" not in text
    assert "eyJ" not in text
    assert _settings_value(body, "BearerToken") in {None, ""}


def test_refreshable_can_stamp_session_jwt_without_client_id_or_secret() -> None:
    body = render_client_report_xlsx(
        [_published_fact(campaign_id="camp-x")],
        published_at=None,
        client_id=CLIENT_ID,
        client_code="default",
        client_name="Default",
        artifact=ARTIFACT_REFRESHABLE,
        api_base_url="http://127.0.0.1:8000",
        refresh_bearer_token="aaa.bbb.ccc",
    )
    assert _settings_value(body, "BearerToken") == "aaa.bbb.ccc"
    assert _settings_value(body, "ClientId") in {None, ""}
    with ZipFile(io.BytesIO(body)) as archive:
        custom = archive.read("docProps/custom.xml").decode("utf-8")
        connections = archive.read("xl/connections.xml").decode("utf-8")
        facts_xml = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert "aaa.bbb.ccc" not in custom
    assert "aaa.bbb.ccc" not in connections
    assert "DFIP_AUTH_SECRET" not in body.decode("latin-1")
    row3 = re.search(r'<row r="3"[^>]*>.*?</row>', facts_xml, flags=re.DOTALL)
    assert row3 is not None
    assert row3.group(0).index('r="A3"') < row3.group(0).index('r="B3"')


def test_refreshable_http_download_does_not_embed_claim_jwt() -> None:
    app, _store, _facts, headers = jwt_app(role="publisher")
    http = TestClient(app)
    created = _publish(http, headers=headers)
    assert created.status_code == 201
    response = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=headers,
        params={"client_id": CLIENT_ID},
    )
    assert response.status_code == 403
    assert not response.content.startswith(b"PK")


def test_refreshable_dev_token_download_does_not_embed_dev_token() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    created = _publish(http)
    assert created.status_code == 201
    response = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert response.status_code == 200
    assert _settings_value(response.content, "BearerToken") in {None, ""}
    assert _settings_value(response.content, "ClientId") in {None, ""}
    embedded = DEV_TOKEN in response.content.decode("latin-1")
    assert not embedded, "dev token must not be written into the refreshable workbook"


def test_refreshable_month_slicer_selects_all_and_service_filter_needs_data() -> None:
    selected = selected_slicer_values(
        "Month",
        contract_name="AMC DayWise",
        uniques=["Aug-25", "Sep-25", "Oct-25"],
        present=["Aug-25", "Sep-25", "Oct-25"],
        latest_month="Oct-25",
    )
    assert selected == ("Aug-25", "Sep-25", "Oct-25")
    rows = [
        _published_fact(
            campaign_id="camp-oct",
            month_label="Oct-25",
            filter_logic_1="TAMC | D2C AMC|Manual Campaign",
            unique_click_through_conversions=11,
            unique_conversions=13,
        )
    ]
    body = render_client_report_xlsx(
        rows,
        published_at=None,
        client_id=CLIENT_ID,
        client_code="eureka",
        client_name="Eureka Forbes",
        publication_id="p0000000-0000-4000-8000-000000000099",
        artifact=ARTIFACT_REFRESHABLE,
        api_base_url="http://127.0.0.1:8000",
    )
    with ZipFile(io.BytesIO(body)) as archive:
        service = archive.read("xl/pivotTables/pivotTable9.xml").decode("utf-8")
        caches = [
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("xl/slicerCaches/") and name.endswith(".xml")
        ]
    assert SERVICE_FILTER_LOGIC_1 not in service
    assert f'fld="{cache_field_index("Filter Logic 1")}" item="' not in service
    month_caches = [xml for xml in caches if 'sourceName="Month"' in xml]
    assert month_caches
    for xml in month_caches:
        assert xml.count('s="1"') == xml.count("<i ")


@requires_postgres
@postgres_only
def test_postgres_history_unions_and_republish(pg_conn, pg_pool) -> None:
    from dfip_db.publication_store import PostgresPublicationStore
    from postgres_support import RUN_A as PG_RUN_A

    seed_working_set(pg_conn)
    store = PostgresPublicationStore(pg_pool)
    oct_row = sample_fact(
        campaign_id="camp-oct",
        day=date(2025, 10, 3),
        month_label="Oct-25",
        unique_conversions=2,
        unique_click_through_conversions=1,
        processing_run_id=PG_RUN_A,
    )
    sep_row = sample_fact(
        campaign_id="camp-sep",
        day=date(2025, 9, 4),
        month_label="Sep-25",
        unique_conversions=4,
        unique_click_through_conversions=3,
        processing_run_id=PG_RUN_A,
    )
    store.create(
        client_id=oct_row.client_id,
        processing_run_id=PG_RUN_A,
        period_start=oct_row.day,
        period_end=oct_row.day,
        published_by="publisher",
        notes=None,
        snapshot_facts=[oct_row],
    )
    store.create(
        client_id=sep_row.client_id,
        processing_run_id=PG_RUN_A,
        period_start=sep_row.day,
        period_end=sep_row.day,
        published_by="publisher",
        notes=None,
        snapshot_facts=[sep_row],
    )
    rows, total = store.list_published_history(oct_row.client_id, limit=200, offset=0)
    assert total == 2
    campaigns = {item.campaign_id for item in rows}
    assert campaigns == {"camp-oct", "camp-sep"}
    restated = sample_fact(
        campaign_id="camp-sep",
        day=date(2025, 9, 4),
        month_label="Sep-25",
        unique_conversions=40,
        unique_click_through_conversions=30,
        processing_run_id=PG_RUN_A,
    )
    store.create(
        client_id=restated.client_id,
        processing_run_id=PG_RUN_A,
        period_start=restated.day,
        period_end=restated.day,
        published_by="publisher",
        notes=None,
        snapshot_facts=[restated],
    )
    rows, total = store.list_published_history(oct_row.client_id, limit=200, offset=0)
    assert total == 2
    by_campaign = {item.campaign_id: item for item in rows}
    assert by_campaign["camp-sep"].unique_conversions == 40
    assert by_campaign["camp-sep"].unique_click_through_conversions == 30
    assert by_campaign["camp-oct"].unique_conversions == 2
