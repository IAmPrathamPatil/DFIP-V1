"""RUN 005C: refreshable company workbook vs static recovery.

Does not implement a second pivot engine. Does not change fact_scope.
Does not open hosted databases. PostgreSQL cases require DFIP_TEST_DATABASE_URL.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import jwt
from dfip_api.app import create_app
from dfip_api.excel_grant import EXCEL_TOKEN_TYP
from dfip_db.local_demo_guard import COMPANY_2_CLIENT_ID, DEFAULT_CLIENT_ID
from dfip_web.client_report_download import (
    ARTIFACT_REFRESHABLE,
    ARTIFACT_STATIC,
    FACT_VALUE_FIELDS,
    QUERY_TABLE_PART,
    client_report_download_filename,
    query_table_field_names,
    render_client_report_xlsx,
)
from dfip_web.client_workbook import FACT_HEADERS, mashup_text
from dfip_web.pivot_report import PIVOT_CACHE_PART, assert_native_pivot_package
from dfip_web.published_facts_mashup import (
    extract_published_facts_section_from_package,
)
from fastapi.testclient import TestClient
from openpyxl import load_workbook

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
    WEB_STATIC,
    _bearer,
    _memory_app,
    _publish_synthetic,
    _select,
    _token,
)
from test_company_workbook import (
    CAMP_A,
    CAMP_B,
    FILENAME_RE,
    SECRET_BYTES,
    _assert_static_and_native,
    _cache_field_names,
    _custom_props,
    _part_count,
    _published_headers,
    _scan_workbook_text,
    _settings_value,
)
from test_p5_api import AUTH, CLIENT_ID, JWT_SECRET, _encode_jwt, make_settings
from test_p7_publication import _error, _publish, publisher_app
from test_p8_client_report import _session_jwt_stamped


def _excel_stamp_claims(token: object) -> dict:
    assert _session_jwt_stamped(token)
    assert isinstance(token, str)
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})


REFRESHABLE_FILENAME_RE = re.compile(
    r"^attachment; filename="
    r'"DFIP_[A-Za-z0-9._-]+_\d{4}-\d{2}-\d{2}_Client_Report_Refreshable\.xlsm"$'
)


def _query_names(body: bytes) -> set[str]:
    with ZipFile(io.BytesIO(body)) as archive:
        return set(archive.namelist())


def test_publishedfacts_m_uses_canonical_header_order() -> None:
    mashup = mashup_text()
    assert "CanonicalKeys = Record.FieldNames(HeaderMap)" in mashup
    assert "Csv.Document" in mashup
    assert "Table.PromoteHeaders" in mashup
    assert "Table.ReorderColumns(Renamed, DisplayHeaders)" in mashup
    assert "Table.FromRows(CombinedItems, TableColumns)" not in mashup
    assert "layout = \"table\"" not in mashup
    assert "PageLimit" not in mashup
    assert "/api/v1/publications/history/facts.csv" in mashup
    assert "/api/v1/auth/refresh" in mashup
    assert 'Authorization = "Bearer "' in mashup
    assert "List.Skip(PageIndexes, 1)" not in mashup
    assert "/api/v1/publications/current/facts" not in mashup
    assert "Timeout = #duration(0, 0, 0, 30)" in mashup
    assert "Date.From([month_start])" in mashup


def test_published_facts_json_key_order_matches_workbook_contract() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    created = _publish(http)
    assert created.status_code == 201
    body = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 1},
    ).json()
    assert body["items"]
    assert list(body["items"][0].keys()) == list(FACT_VALUE_FIELDS)


def test_filename_helper_distinguishes_artifacts() -> None:
    published = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    static = client_report_download_filename("default", published)
    live = client_report_download_filename("default", published, artifact=ARTIFACT_REFRESHABLE)
    assert static == "DFIP_default_2026-09-01_Client_Report.xlsx"
    assert live == "DFIP_default_2026-09-01_Client_Report_Refreshable.xlsm"


def test_static_artifact_still_strips_query_tables() -> None:
    body = render_client_report_xlsx(
        [{"campaign_id": "camp-static", "filter_logic_1": "x"}],
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
        client_id=CLIENT_ID,
        client_code="default",
        client_name="Default",
        publication_id="p0000000-0000-4000-8000-000000000001",
        artifact=ARTIFACT_STATIC,
    )
    _assert_static_and_native(body)
    assert QUERY_TABLE_PART not in _query_names(body)


def test_refreshable_artifact_authors_query_fields_and_keeps_query() -> None:
    body = render_client_report_xlsx(
        [
            {
                "campaign_id": "camp-live",
                "filter_logic_1": "Service | FMS & LMS | Campaigns",
            }
        ],
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
        client_id=CLIENT_ID,
        client_code="default",
        client_name="Default",
        publication_id="p0000000-0000-4000-8000-000000000001",
        artifact=ARTIFACT_REFRESHABLE,
        api_base_url="http://127.0.0.1:8000",
    )
    assert body.startswith(b"PK")
    assert_native_pivot_package(body)
    assert _part_count(body, "xl/pivotTables/") == 9
    assert _part_count(body, "xl/slicerCaches/") == 35
    assert _published_headers(body) == FACT_HEADERS
    assert query_table_field_names(body) == FACT_HEADERS
    assert _cache_field_names(body)[: len(FACT_HEADERS)] == list(FACT_HEADERS)
    assert _settings_value(body, "ApiBaseUrl") == "http://127.0.0.1:8000"
    assert _settings_value(body, "BearerToken") in {None, ""}
    assert _settings_value(body, "ClientId") in {None, ""}
    names = _query_names(body)
    assert QUERY_TABLE_PART in names
    with ZipFile(io.BytesIO(body)) as archive:
        connections = archive.read("xl/connections.xml").decode("utf-8")
        mashup = extract_published_facts_section_from_package(archive)
        cache = archive.read(PIVOT_CACHE_PART).decode("utf-8")
    assert 'refreshOnLoad="0"' in connections
    assert "Location=PublishedFacts" in connections
    # Runtime must not rewrite DataMashup; Excel skips a rewritten package.
    # Desktop-authored formula matches excel/PublishedFacts.m (CSV history).
    assert "CanonicalKeys = Record.FieldNames(HeaderMap)" in mashup
    assert "Csv.Document" in mashup
    assert "Table.PromoteHeaders" in mashup
    assert "layout = \"table\"" not in mashup
    assert "/api/v1/publications/history/facts.csv" in mashup
    assert "/api/v1/publications/current/facts" not in mashup
    assert "PageLimit" not in mashup
    assert "Date.From([month_start])" in mashup
    assert "Chrono" in mashup
    assert 'Authorization = "Bearer "' in mashup
    assert 'name="ExternalData_1"' in cache
    assert "worksheetSource ref=" not in cache
    assert "A1:AS1048576" not in cache
    with ZipFile(io.BytesIO(body)) as archive:
        query_table = archive.read(QUERY_TABLE_PART).decode("utf-8")
    assert re.search(r'(?<![A-Za-z])backgroundRefresh="0"', query_table)
    assert "overwriteClear" in query_table
    assert "Field2" not in cache
    text = _scan_workbook_text(body)
    for token in SECRET_BYTES:
        assert token not in text
    workbook = load_workbook(io.BytesIO(body), read_only=True, data_only=False)
    facts = workbook["Facts"]
    assert facts.sheet_state == "hidden"
    assert workbook["PublishedFacts"].sheet_state == "hidden"
    assert facts["B3"].value in {None, ""}
    assert facts["B4"].value in {None, ""}
    assert facts["G5"].value == "LAST SUCCESSFUL DATA"
    assert facts["I5"].value == "STATUS"
    assert facts["J5"].value == "REFRESH REQUIRED"
    workbook.close()
    assert _custom_props(body)["client_id"] == CLIENT_ID
    with ZipFile(io.BytesIO(body)) as archive:
        facts_xml = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
    row2 = re.search(r'<row r="2"[^>]*>.*?</row>', facts_xml, flags=re.DOTALL)
    assert row2 is not None
    assert row2.group(0).index('r="A2"') < row2.group(0).index('r="B2"')


def test_refreshable_http_download_and_tenant_isolation(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    pub_a = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    pub_b = _bearer(_select(http, unbound, COMPANY_2_CLIENT_ID).json()["access_token"])
    _publish_synthetic(
        http, tmp_path, publisher=pub_a, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_A
    )
    _publish_synthetic(
        http, tmp_path, publisher=pub_b, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_B
    )
    live_a = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=pub_a,
    )
    live_b = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=pub_b,
    )
    static_a = http.get("/api/v1/publications/current/client-report.xlsx", headers=pub_a)
    assert live_a.status_code == 200, live_a.text
    assert live_b.status_code == 200, live_b.text
    assert static_a.status_code == 200
    assert REFRESHABLE_FILENAME_RE.match(live_a.headers["content-disposition"])
    assert live_a.headers.get("cache-control") == "no-store"
    assert FILENAME_RE.match(static_a.headers["content-disposition"])
    assert live_a.content.startswith(b"PK")
    assert live_b.content.startswith(b"PK")
    assert live_a.content != live_b.content
    assert QUERY_TABLE_PART not in _query_names(static_a.content)
    claims_a = _excel_stamp_claims(_settings_value(live_a.content, "BearerToken"))
    claims_b = _excel_stamp_claims(_settings_value(live_b.content, "BearerToken"))
    assert claims_a["typ"] == EXCEL_TOKEN_TYP
    assert claims_b["typ"] == EXCEL_TOKEN_TYP
    assert claims_a["role"] == "client"
    assert claims_b["role"] == "client"
    assert claims_a["client_id"] == DEFAULT_CLIENT_ID
    assert claims_b["client_id"] == COMPANY_2_CLIENT_ID
    assert claims_a.get("jti") != claims_b.get("jti")
    assert claims_a.get("platform_admin") is not True
    assert claims_b.get("platform_admin") is not True
    assert isinstance(_settings_value(live_a.content, "BearerToken"), str)
    assert _settings_value(live_a.content, "BearerToken") not in live_b.content.decode("latin-1")
    assert _settings_value(live_b.content, "BearerToken") not in live_a.content.decode("latin-1")
    assert _settings_value(live_a.content, "ClientId") in {None, ""}
    assert _settings_value(static_a.content, "BearerToken") in {None, ""}
    text_a = live_a.content.decode("latin-1")
    assert "DFIP_AUTH_SECRET" not in text_a
    assert JWT_SECRET not in text_a
    assert "localhost" not in text_a
    assert "xl/model/item.data" in _query_names(live_a.content)
    assert "xl/vbaProject.bin" in _query_names(live_a.content)
    client = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    denied = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=client,
        params={"client_id": COMPANY_2_CLIENT_ID},
    )
    assert denied.status_code == 403
    own = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=client,
    )
    assert own.status_code == 200
    own_claims = _excel_stamp_claims(_settings_value(own.content, "BearerToken"))
    assert own_claims["typ"] == EXCEL_TOKEN_TYP
    assert own_claims["role"] == "client"
    assert own_claims["client_id"] == DEFAULT_CLIENT_ID
    assert _settings_value(own.content, "ClientId") in {None, ""}


def test_refreshable_historical_path_does_not_exist() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    created = _publish(http)
    publication_id = created.json()["publication"]["publication_id"]
    response = http.get(
        f"/api/v1/publications/{publication_id}/refreshable-client-report.xlsx",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert response.status_code == 404


def test_refreshable_unauthenticated_is_rejected() -> None:
    app, *_rest = publisher_app()
    http = TestClient(app)
    _publish(http)
    response = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        params={"client_id": CLIENT_ID},
    )
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTHENTICATION_FAILED"
    assert not response.content.startswith(b"PK")


def test_client_jwt_cannot_widen_refreshable_with_query_client_id() -> None:
    app, ingest, facts, store = publisher_app()
    pub_http = TestClient(app)
    _publish(pub_http)
    reader = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    headers = {"Authorization": f"Bearer {_encode_jwt(role='client', client_id=CLIENT_ID)}"}
    http = TestClient(reader)
    denied = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=headers,
        params={"client_id": "b0000000-0000-4000-8000-00000000000b"},
    )
    assert denied.status_code == 403


def test_spa_exposes_refreshable_download() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    assert "refreshable-client-report.xlsx" in client_js
    assert "downloadRefreshableClientReport" in client_js
    assert "onHeaders" in client_js
    assert "data-download-refreshable-client-report" in views
    assert "Download Refreshable Workbook" in views
    assert "data-refreshable-download-error" in views
    assert "refreshableWorkbookDownloadInFlight" in app_js
    assert "Generating…" in app_js
    assert "Downloading…" in app_js
    assert "showRefreshableDownloadError" in app_js
    assert "Refreshable company workbook downloaded." in app_js
    start = app_js.index("const refreshableReport = event.target.closest")
    handler = app_js[start : app_js.index("const catalogDownload = event.target.closest", start)]
    assert "refreshableWorkbookDownloadInFlight" in handler
    assert 'setRefreshableDownloadBusy("generating")' in handler
    assert 'setRefreshableDownloadBusy("downloading")' in handler
    assert "showRefreshableDownloadError(error)" in handler
    assert "isAuthError(error)" in handler
    assert ".catch((error) => handleError(error, path))" not in handler


@requires_postgres
@postgres_only
def test_postgres_refreshable_workbook_isolation(
    pg_conn, postgres_url: str, tmp_path: Path
) -> None:
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
        _publish_synthetic(
            http,
            tmp_path,
            publisher=pub_a,
            client_id=DEFAULT_CLIENT_ID,
            campaign_id="camp-005c-pg-a",
        )
        _publish_synthetic(
            http,
            tmp_path,
            publisher=pub_b,
            client_id=COMPANY_2_CLIENT_ID,
            campaign_id="camp-005c-pg-b",
        )
        client_a = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
        client_b = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))
        live_a = http.get(
            "/api/v1/publications/current/refreshable-client-report.xlsx",
            headers=client_a,
        )
        live_b = http.get(
            "/api/v1/publications/current/refreshable-client-report.xlsx",
            headers=client_b,
        )
        recovered = http.get(
            "/api/v1/publications/current/client-report.xlsx",
            headers=client_a,
        )
        assert live_a.status_code == 200, live_a.text
        assert live_b.status_code == 200, live_b.text
        assert recovered.status_code == 200
        assert live_a.content != live_b.content
        assert QUERY_TABLE_PART not in _query_names(recovered.content)
        assert "xl/model/item.data" in _query_names(live_a.content)
        pg_claims = _excel_stamp_claims(_settings_value(live_a.content, "BearerToken"))
        assert pg_claims["typ"] == EXCEL_TOKEN_TYP
        assert pg_claims["role"] == "client"
        assert pg_claims["client_id"] == DEFAULT_CLIENT_ID
        assert _session_jwt_stamped(_settings_value(live_b.content, "BearerToken"))
        assert _settings_value(live_a.content, "ClientId") in {None, ""}
        assert _settings_value(recovered.content, "BearerToken") in {None, ""}
        assert _settings_value(recovered.content, "ApiBaseUrl") in {None, ""}
        assert "DFIP_AUTH_SECRET" not in live_a.content.decode("latin-1")
