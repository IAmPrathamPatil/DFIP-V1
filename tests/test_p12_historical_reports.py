"""P12 historical reports / recovery on the existing publication catalog.

Does not add a history engine, retention, or a historical refreshable workbook.
PostgreSQL cases require DFIP_TEST_DATABASE_URL and skip otherwise.
"""

from __future__ import annotations

import io
import re
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from dfip_api.app import create_app
from dfip_api.publication_store import (
    SNAPSHOT_STATUS_COMPLETE,
    SNAPSHOT_STATUS_NONE,
    InMemoryPublicationStore,
    PublicationCurrentRecord,
    PublicationRecord,
)
from dfip_core.ingest.store import ProcessingRunRecord
from dfip_core.transform.fact import FactRecord
from dfip_db.local_demo_guard import COMPANY_2_CLIENT_ID, DEFAULT_CLIENT_ID
from dfip_web.client_report_download import (
    ARTIFACT_REFRESHABLE,
    CLIENT_REPORT_DOWNLOAD_NAME,
    client_report_download_filename,
    publication_short_id,
)
from dfip_web.client_workbook import FACT_HEADERS
from dfip_web.daily_report import REPORT_SHEET_NAMES
from dfip_web.pivot_report import PIVOT_CACHE_PART, PIVOT_TABLE_NAMES, assert_native_pivot_package
from dfip_web.report_format import RUN006_FONT
from dfip_web.slicer_defaults import assert_slicer_package, latest_month_label
from fastapi.testclient import TestClient

from postgres_support import postgres_only, requires_postgres
from test_company_registry import (
    CLIENT2_PASSWORD,
    CLIENT_PASSWORD,
    DEMO_CLIENT_2_SUBJECT,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
    PUBLISHER2_PASSWORD,
    PUBLISHER_PASSWORD,
    _bearer,
    _publish_synthetic,
    _token,
)
from test_company_workbook import _custom_props
from test_p5_api import (
    AUTH,
    BATCH_A,
    CLIENT_ID,
    JWT_SECRET,
    MISSING_ID,
    RUN_A,
    _encode_jwt,
    make_settings,
    seed_stores,
)
from test_p7_publication import _error, _publish, publisher_app, publisher_settings
from test_p8_client_report import (
    CLIENT_B,
    RUN_CLIENT_B,
    RUN_D,
    _add_later_run,
    _campaign_ids,
    _client_report_path,
    _scan_workbook_text,
    _seed_client_b,
    _two_publications,
)
from test_p11_pivot_report import _published_fact

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"
FIELDN = re.compile(r"^Field\d+$")
RUN_YEAR = "d0000000-0000-4000-8000-0000000000e1"
RUN_C_PUB = "d0000000-0000-4000-8000-0000000000c1"
HISTORICAL_FILENAME_RE = re.compile(
    r'^attachment; filename="DFIP_[A-Za-z0-9._-]+_\d{4}-\d{2}-\d{2}'
    r'_[0-9a-f]{8}_Client_Report\.xlsx"$'
)
CURRENT_FILENAME_RE = re.compile(
    r'^attachment; filename="DFIP_[A-Za-z0-9._-]+_\d{4}-\d{2}-\d{2}'
    r'_Client_Report\.xlsx"$'
)


def _assert_frozen_workbook(body: bytes) -> None:
    assert_native_pivot_package(body)
    assert_slicer_package(body)
    with ZipFile(io.BytesIO(body)) as archive:
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
        styles = archive.read("xl/styles.xml").decode("utf-8")
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
        slicers = [
            n for n in archive.namelist() if n.startswith("xl/slicerCaches/") and n.endswith(".xml")
        ]
        pivots = [n for n in archive.namelist() if n.startswith("xl/pivotTables/pivotTable")]
    names = re.findall(r'<cacheField name="([^"]+)"', cache)
    assert names[:45] == list(FACT_HEADERS)
    assert not any(FIELDN.match(name) for name in names[:45])
    assert len(slicers) == 35
    assert len(pivots) == 9
    assert RUN006_FONT in styles
    assert 'state="hidden"' in workbook_xml
    assert "veryHidden" not in workbook_xml


def test_publication_short_id_is_stable_hex_prefix() -> None:
    pub_id = "a1b2c3d4-e5f6-4000-8000-000000000001"
    assert publication_short_id(pub_id) == "a1b2c3d4"
    assert publication_short_id(pub_id.upper()) == "a1b2c3d4"


def test_current_filename_omits_publication_id() -> None:
    published = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    name = client_report_download_filename("default", published)
    assert name == "DFIP_default_2026-09-01_Client_Report.xlsx"
    refreshable = client_report_download_filename(
        "default",
        published,
        artifact=ARTIFACT_REFRESHABLE,
        publication_id="a1b2c3d4-e5f6-4000-8000-000000000001",
    )
    assert refreshable == "DFIP_default_2026-09-01_Client_Report_Refreshable.xlsm"


def test_historical_filename_includes_short_id_not_display_name() -> None:
    published = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
    pub_id = "a1b2c3d4-e5f6-4000-8000-000000000001"
    name = client_report_download_filename("default", published, publication_id=pub_id)
    assert name == "DFIP_default_2026-09-01_a1b2c3d4_Client_Report.xlsx"
    assert "Alpha" not in name
    assert name.endswith(CLIENT_REPORT_DOWNLOAD_NAME)


def test_same_day_historical_filenames_do_not_collide() -> None:
    published = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    first = client_report_download_filename(
        "default", published, publication_id="aaaaaaaa-0000-4000-8000-000000000001"
    )
    second = client_report_download_filename(
        "default", published, publication_id="bbbbbbbb-0000-4000-8000-000000000002"
    )
    current = client_report_download_filename("default", published)
    assert first != second
    assert first != current
    assert second != current
    assert first.endswith("_aaaaaaaa_Client_Report.xlsx")
    assert second.endswith("_bbbbbbbb_Client_Report.xlsx")


def test_history_lists_current_and_historical_metadata() -> None:
    http, _ingest, _facts, store, pub_a, pub_b = _two_publications()
    listed = http.get(
        "/api/v1/publications",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 50, "offset": 0},
    )
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [item["publication_id"] for item in items] == [pub_b, pub_a]
    current = http.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()["publication"]
    assert current["publication_id"] == pub_b
    for item in items:
        assert item["snapshot_status"] == SNAPSHOT_STATUS_COMPLETE
        assert item["snapshot_row_count"] is not None
        assert "period_start" in item
        assert "period_end" in item
        assert "notes" in item
        assert "published_at" in item
    assert store.get_current(CLIENT_ID)[0].id == pub_b


def test_historical_download_filename_and_current_filename_contracts() -> None:
    http, _ingest, _facts, _store, pub_a, pub_b = _two_publications()
    params = {"client_id": CLIENT_ID}
    historical = http.get(_client_report_path(pub_a), headers=AUTH, params=params)
    current = http.get(_client_report_path(), headers=AUTH, params=params)
    assert historical.status_code == 200
    assert current.status_code == 200
    hist_name = historical.headers["content-disposition"]
    current_name = current.headers["content-disposition"]
    assert HISTORICAL_FILENAME_RE.match(hist_name)
    assert CURRENT_FILENAME_RE.match(current_name)
    assert publication_short_id(pub_a) in hist_name
    assert publication_short_id(pub_a) not in current_name
    assert _campaign_ids(historical.content) == ["camp-1", "camp-2", "camp-3"]
    assert _campaign_ids(current.content) == ["camp-hist-b"]
    _assert_frozen_workbook(historical.content)
    _assert_frozen_workbook(current.content)
    assert _custom_props(historical.content)["publication_id"] == pub_a
    assert _custom_props(current.content)["publication_id"] == pub_b


def test_abc_snapshot_pointer_does_not_rewrite_history() -> None:
    http, ingest, facts, store, pub_a, pub_b = _two_publications()
    params = {"client_id": CLIENT_ID}
    hist_a = http.get(_client_report_path(pub_a), headers=AUTH, params=params)
    hist_b = http.get(_client_report_path(pub_b), headers=AUTH, params=params)
    assert _campaign_ids(hist_a.content) == ["camp-1", "camp-2", "camp-3"]
    assert _campaign_ids(hist_b.content) == ["camp-hist-b"]
    ingest.processing_runs[RUN_C_PUB] = ProcessingRunRecord(
        id=RUN_C_PUB,
        batch_id=BATCH_A,
        campaign_label_version_id="a0000000-0000-4000-8000-000000000022",
        template_label_version_id="a0000000-0000-4000-8000-000000000034",
        rate_card_version_id="a0000000-0000-4000-8000-000000000012",
        label_group_version_id="a0000000-0000-4000-8000-000000000041",
        engine_version="0.4.0",
        started_at=datetime(2025, 8, 1, 9, tzinfo=UTC),
        finished_at=datetime(2025, 8, 1, 10, tzinfo=UTC),
        status="succeeded",
        qa_verdict="pass",
    )
    later = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-hist-c",
        variation_id="var-c",
        variation_id_key="var-c",
        day=date(2025, 10, 1),
        campaign_name="publication-c",
        processing_run_id=RUN_C_PUB,
        batch_id=BATCH_A,
        total_cost=Decimal("3.00"),
        first_seen_at=datetime(2025, 8, 1, 9, tzinfo=UTC),
        last_seen_at=datetime(2025, 8, 1, 9, tzinfo=UTC),
    )
    facts.facts[later.key] = later
    third = _publish(http, processing_run_id=RUN_C_PUB)
    assert third.status_code == 201
    pub_c = third.json()["publication"]["publication_id"]
    again_a = http.get(_client_report_path(pub_a), headers=AUTH, params=params)
    again_b = http.get(_client_report_path(pub_b), headers=AUTH, params=params)
    current = http.get(_client_report_path(), headers=AUTH, params=params)
    assert _campaign_ids(again_a.content) == ["camp-1", "camp-2", "camp-3"]
    assert _campaign_ids(again_b.content) == ["camp-hist-b"]
    assert _campaign_ids(current.content) == ["camp-hist-c"]
    assert "camp-hist-c" not in _campaign_ids(again_a.content)
    assert store.get_current(CLIENT_ID)[0].id == pub_c


def test_complete_snapshot_ignores_mutated_working_set() -> None:
    http, _ingest, facts, store, pub_a, _pub_b = _two_publications()
    record = store.get(pub_a)
    assert record is not None
    assert record.snapshot_status == SNAPSHOT_STATUS_COMPLETE
    for key, item in list(facts.facts.items()):
        if item.processing_run_id == RUN_A:
            facts.facts[key] = replace(item, campaign_id="mutated-live")
    response = http.get(
        _client_report_path(pub_a),
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert response.status_code == 200
    ids = _campaign_ids(response.content)
    assert ids == ["camp-1", "camp-2", "camp-3"]
    assert "mutated-live" not in ids
    assert "mutated-live" not in _scan_workbook_text(response.content)


def test_legacy_none_snapshot_is_not_labeled_frozen_in_api_or_ui() -> None:
    ingest, facts = seed_stores()
    store = InMemoryPublicationStore()
    pub_id = str(uuid4())
    store.publications[pub_id] = PublicationRecord(
        id=pub_id,
        client_id=CLIENT_ID,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_at=datetime(2025, 8, 1, tzinfo=UTC),
        published_by="legacy",
        notes="pre-snapshot",
        fact_scope="processing_run",
        snapshot_status=SNAPSHOT_STATUS_NONE,
        snapshot_row_count=None,
    )
    store.current[CLIENT_ID] = PublicationCurrentRecord(
        client_id=CLIENT_ID,
        publication_id=pub_id,
        updated_at=datetime(2025, 8, 1, tzinfo=UTC),
    )
    app = create_app(
        settings=publisher_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    http = TestClient(app)
    listed = http.get(
        "/api/v1/publications",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["snapshot_status"] == SNAPSHOT_STATUS_NONE
    assert item["snapshot_row_count"] is None
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    assert "Not a frozen snapshot" in views
    assert "Current publication" in views
    assert "Historical publication" in views


def test_year_crossing_history_keeps_snapshot_months() -> None:
    ingest, facts = seed_stores()
    store = InMemoryPublicationStore()
    app = create_app(
        settings=publisher_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    http = TestClient(app)
    first = _publish(
        http,
        processing_run_id=RUN_A,
        period_start="2026-12-01",
        period_end="2026-12-31",
        notes="December FY-2027 start",
    )
    assert first.status_code == 201
    pub_dec = first.json()["publication"]["publication_id"]
    ingest.processing_runs[RUN_YEAR] = ProcessingRunRecord(
        id=RUN_YEAR,
        batch_id=BATCH_A,
        campaign_label_version_id="a0000000-0000-4000-8000-000000000022",
        template_label_version_id="a0000000-0000-4000-8000-000000000034",
        rate_card_version_id="a0000000-0000-4000-8000-000000000012",
        label_group_version_id="a0000000-0000-4000-8000-000000000041",
        engine_version="0.4.0",
        started_at=datetime(2027, 1, 1, tzinfo=UTC),
        finished_at=datetime(2027, 1, 1, tzinfo=UTC),
        status="succeeded",
        qa_verdict="pass",
    )
    january = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-jan-27",
        variation_id="var-jan",
        variation_id_key="var-jan",
        day=date(2027, 1, 5),
        month_start=date(2027, 1, 1),
        month_label="Jan-27",
        campaign_name="january-2027",
        processing_run_id=RUN_YEAR,
        batch_id=BATCH_A,
        total_cost=Decimal("4.00"),
        first_seen_at=datetime(2027, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2027, 1, 1, tzinfo=UTC),
    )
    facts.facts[january.key] = january
    second = _publish(
        http,
        processing_run_id=RUN_YEAR,
        period_start="2027-01-01",
        period_end="2027-01-31",
        notes="January FY-2027",
    )
    assert second.status_code == 201
    pub_jan = second.json()["publication"]["publication_id"]
    listed = http.get(
        "/api/v1/publications",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()["items"]
    assert [item["publication_id"] for item in listed] == [pub_jan, pub_dec]
    assert listed[0]["period_start"] == "2027-01-01"
    assert listed[1]["period_start"] == "2026-12-01"
    params = {"client_id": CLIENT_ID}
    hist = http.get(_client_report_path(pub_dec), headers=AUTH, params=params)
    current = http.get(_client_report_path(), headers=AUTH, params=params)
    assert hist.status_code == 200
    assert current.status_code == 200
    assert "camp-jan-27" not in _campaign_ids(hist.content)
    assert _campaign_ids(current.content) == ["camp-jan-27"]
    dec_rows = [_published_fact(month_label="Dec-26", month_start="2026-12-01")]
    jan_rows = [_published_fact(month_label="Jan-27", month_start="2027-01-01")]
    assert latest_month_label(dec_rows) == "Dec-26"
    assert latest_month_label(jan_rows) == "Jan-27"
    assert store.get_current(CLIENT_ID)[0].id == pub_jan


def test_company_b_cannot_list_or_download_company_a() -> None:
    ingest, facts = seed_stores()
    _seed_client_b(ingest, facts)
    store = InMemoryPublicationStore()
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    http = TestClient(app)
    pub_headers = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_ID)}"}
    pub_b_headers = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_B)}"}
    first = _publish(http, headers=pub_headers)
    _add_later_run(ingest, facts)
    _publish(http, headers=pub_headers, processing_run_id=RUN_D)
    created_b = _publish(
        http, headers=pub_b_headers, client_id=CLIENT_B, processing_run_id=RUN_CLIENT_B
    )
    assert first.status_code == 201
    assert created_b.status_code == 201
    pub_a = first.json()["publication"]["publication_id"]
    pub_b = created_b.json()["publication"]["publication_id"]
    client_a = {"Authorization": f"Bearer {_encode_jwt(role='client', client_id=CLIENT_ID)}"}
    client_b = {"Authorization": f"Bearer {_encode_jwt(role='client', client_id=CLIENT_B)}"}
    list_a = http.get("/api/v1/publications", headers=client_a).json()["items"]
    list_b = http.get("/api/v1/publications", headers=client_b).json()["items"]
    assert pub_b not in {item["publication_id"] for item in list_a}
    assert pub_a not in {item["publication_id"] for item in list_b}
    denied = http.get(_client_report_path(pub_b), headers=client_a)
    assert denied.status_code == 404
    assert _error(denied)["code"] == "NOT_FOUND"
    denied_current = http.get(
        _client_report_path(),
        headers=client_a,
        params={"client_id": CLIENT_B},
    )
    assert denied_current.status_code == 403
    own = http.get(_client_report_path(), headers=client_b)
    assert own.status_code == 200
    assert _campaign_ids(own.content) == ["camp-b-only"]


def test_invalid_and_missing_publications() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    missing_current = http.get(
        _client_report_path(),
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert missing_current.status_code == 404
    unknown = http.get(
        _client_report_path(MISSING_ID), headers=AUTH, params={"client_id": CLIENT_ID}
    )
    assert unknown.status_code == 404
    assert _error(unknown)["code"] == "NOT_FOUND"
    bogus = http.get("/api/v1/publications/not-a-uuid/client-report.xlsx", headers=AUTH)
    assert bogus.status_code == 422
    refreshable_hist = http.get(
        f"/api/v1/publications/{MISSING_ID}/refreshable-client-report.xlsx",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert refreshable_hist.status_code == 404


def test_download_does_not_move_publication_current() -> None:
    http, ingest, facts, store, pub_a, pub_b = _two_publications()
    before_current = store.get_current(CLIENT_ID)
    before_snaps = {key: list(value) for key, value in store.snapshots.items()}
    before_facts = {key: item for key, item in facts.facts.items()}
    before_runs = {key: (run.status, run.qa_verdict) for key, run in ingest.processing_runs.items()}
    params = {"client_id": CLIENT_ID}
    assert http.get(_client_report_path(), headers=AUTH, params=params).status_code == 200
    assert http.get(_client_report_path(pub_a), headers=AUTH, params=params).status_code == 200
    assert store.get_current(CLIENT_ID) == before_current
    assert store.get_current(CLIENT_ID)[0].id == pub_b
    assert {key: list(value) for key, value in store.snapshots.items()} == before_snaps
    assert facts.facts == before_facts
    assert {
        key: (run.status, run.qa_verdict) for key, run in ingest.processing_runs.items()
    } == before_runs


def test_spa_catalog_copy_keeps_history_distinct() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    assert "Current publication" in views
    assert "Historical publication" in views
    assert "Not a frozen snapshot" in views
    assert "Download Client Report" in views
    assert "data-download-refreshable-client-report" in views
    assert "publication history" in views
    assert "client publication history" in views
    assert "This is not Publications report history" in views
    assert "downloadRefreshableClientReport" in app_js
    assert "/publications/current/refreshable-client-report.xlsx" in client_js
    assert "${this.prefix}/publications/${publicationId}/refreshable-client-report.xlsx" not in (
        client_js
    )


@requires_postgres
@postgres_only
def test_postgres_historical_catalog_isolation(pg_conn, postgres_url: str, tmp_path: Path) -> None:
    from dfip_api.local_demo_seed import seed_company2_identities, seed_demo_identities

    seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password=CLIENT_PASSWORD,
        iterations=1000,
    )
    seed_company2_identities(
        pg_conn,
        publisher_password=PUBLISHER2_PASSWORD,
        client_password=CLIENT2_PASSWORD,
        iterations=1000,
    )
    pg_conn.commit()
    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url=postgres_url,
            dfip_password_pbkdf2_iterations=1000,
            dfip_env="test",
        )
    )
    with TestClient(app) as http:
        pub_a = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID))
        pub_b = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
        first = _publish_synthetic(
            http, tmp_path, publisher=pub_a, client_id=DEFAULT_CLIENT_ID, campaign_id="camp-p12-a1"
        )
        second = _publish_synthetic(
            http, tmp_path, publisher=pub_a, client_id=DEFAULT_CLIENT_ID, campaign_id="camp-p12-a2"
        )
        other = _publish_synthetic(
            http, tmp_path, publisher=pub_b, client_id=COMPANY_2_CLIENT_ID, campaign_id="camp-p12-b"
        )
        pub_a1 = first["publication"]["publication_id"]
        pub_a2 = second["publication"]["publication_id"]
        pub_b_id = other["publication"]["publication_id"]
        listed = http.get("/api/v1/publications", headers=pub_a).json()["items"]
        ids = {item["publication_id"] for item in listed}
        assert pub_a1 in ids
        assert pub_a2 in ids
        assert pub_b_id not in ids
        hist = http.get(f"/api/v1/publications/{pub_a1}/client-report.xlsx", headers=pub_a)
        current = http.get("/api/v1/publications/current/client-report.xlsx", headers=pub_a)
        assert hist.status_code == 200
        assert current.status_code == 200
        assert HISTORICAL_FILENAME_RE.match(hist.headers["content-disposition"])
        assert CURRENT_FILENAME_RE.match(current.headers["content-disposition"])
        assert "camp-p12-a1" in _campaign_ids(hist.content)
        assert "camp-p12-a2" in _campaign_ids(current.content)
        denied = http.get(f"/api/v1/publications/{pub_b_id}/client-report.xlsx", headers=pub_a)
        assert denied.status_code == 404
        client_a = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
        client_b = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))
        client_list = http.get("/api/v1/publications", headers=client_a).json()["items"]
        assert pub_b_id not in {item["publication_id"] for item in client_list}
        other_hist = http.get(f"/api/v1/publications/{pub_a1}/client-report.xlsx", headers=client_b)
        assert other_hist.status_code == 404


def _dispatch_excel():
    win32com_client = pytest.importorskip("win32com.client")
    try:
        excel = win32com_client.DispatchEx("Excel.Application")
    except Exception as exc:
        pytest.skip(f"Excel COM not available: {exc}")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.AskToUpdateLinks = False
    excel.EnableEvents = False
    return excel


def test_desktop_excel_historical_and_current_static(tmp_path: Path) -> None:
    """Historical and current static workbooks: 9 pivots, 35 slicers. Not Refresh All."""
    http, _ingest, _facts, _store, pub_a, _pub_b = _two_publications()
    params = {"client_id": CLIENT_ID}
    historical = http.get(_client_report_path(pub_a), headers=AUTH, params=params)
    current = http.get(_client_report_path(), headers=AUTH, params=params)
    assert historical.status_code == 200
    assert current.status_code == 200
    hist_path = tmp_path / "p12_historical.xlsx"
    current_path = tmp_path / "p12_current.xlsx"
    hist_path.write_bytes(historical.content)
    current_path.write_bytes(current.content)
    xl_sheet_hidden = 0
    excel = _dispatch_excel()
    try:
        for path, expected in (
            (hist_path, ["camp-1", "camp-2", "camp-3"]),
            (current_path, ["camp-hist-b"]),
        ):
            workbook = excel.Workbooks.Open(str(path.resolve()), UpdateLinks=0, ReadOnly=True)
            assert workbook.Worksheets.Count == 11
            assert workbook.Worksheets("PublishedFacts").Visible == xl_sheet_hidden
            assert workbook.Worksheets("Facts").Visible == xl_sheet_hidden
            assert workbook.SlicerCaches.Count == 35
            assert sum(int(ws.PivotTables().Count) for ws in workbook.Worksheets) == 9
            overall = workbook.Worksheets(REPORT_SHEET_NAMES[0])
            assert str(overall.PivotTables(1).Name) in PIVOT_TABLE_NAMES.values()
            workbook.Close(False)
            assert _campaign_ids(path.read_bytes()) == expected
    finally:
        try:
            excel.Quit()
        except Exception:
            pass
        time.sleep(0.4)
