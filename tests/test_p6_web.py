"""P6 Admin/Client web foundation tests.

The browser SPA is static files plus a Python origin server. Data still comes
from the P5 API. These tests exercise the API client, UI route-guard helpers,
static serving, and source-level security/scope checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from dfip_api.app import create_app
from dfip_api.errors import PersistenceUnavailableError
from dfip_config.settings import Settings
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from dfip_web.api_client import NETWORK_FAILURE, ApiClientError, DfipApiClient
from dfip_web.app import create_web_app
from dfip_web.roles import can_access_admin, can_access_client
from fastapi.testclient import TestClient

from test_p5_api import (
    BATCH_A,
    DEV_TOKEN,
    FILE_A,
    JWT_SECRET,
    RUN_A,
    inspector_settings,
    make_settings,
    seed_stores,
)

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"


def web_settings(**overrides: Any) -> Settings:
    values = {
        "dfip_env": "test",
        "dfip_api_base_url": "http://127.0.0.1:8000",
        "dfip_api_prefix": "/api/v1",
        "dfip_web_origin": "http://127.0.0.1:3000",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def api_app_with_data(**settings_overrides: Any):
    ingest, facts = seed_stores()
    return create_app(
        settings=make_settings(**settings_overrides),
        ingest_store=ingest,
        fact_store=facts,
    )


def api_client_for(app, token: str | None = DEV_TOKEN) -> DfipApiClient:
    return DfipApiClient("http://testserver", token=token, http_client=TestClient(app))


@pytest.fixture
def web_client() -> TestClient:
    return TestClient(create_web_app(web_settings()))


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_p6_static_layout_exists() -> None:
    required = [
        WEB_STATIC / "index.html",
        WEB_STATIC / "css" / "app.css",
        WEB_STATIC / "js" / "api-client.js",
        WEB_STATIC / "js" / "app.js",
        WEB_STATIC / "js" / "roles.js",
        WEB_STATIC / "js" / "views.js",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    assert missing == []
    index = (WEB_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'src="/js/app.js"' in index
    assert "dfip_core" not in index


def test_web_origin_serves_spa_and_config(web_client: TestClient) -> None:
    home = web_client.get("/")
    assert home.status_code == 200
    assert "text/html" in home.headers["content-type"]
    assert "DFIP" in home.text

    admin = web_client.get("/admin/facts")
    assert admin.status_code == 200
    assert "text/html" in admin.headers["content-type"]

    missing = web_client.get("/not-a-real-file.js")
    assert missing.status_code == 200
    assert "app.js" in missing.text

    css = web_client.get("/css/app.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]

    config = web_client.get("/config.json")
    assert config.status_code == 200
    body = config.json()
    assert body["apiBaseUrl"] == "http://127.0.0.1:8000"
    assert body["apiPrefix"] == "/api/v1"
    assert body["adminRoles"] == ["admin", "publisher"]
    dumped = json.dumps(body)
    assert "DFIP_AUTH_SECRET" not in dumped
    assert "DATABASE_URL" not in dumped
    assert DEV_TOKEN not in dumped


def test_web_health_does_not_claim_database(web_client: TestClient) -> None:
    body = web_client.get("/health").json()
    assert body == {"status": "ok", "application": "dfip-web"}
    assert "database" not in body


# ---------------------------------------------------------------------------
# Roles / UI guards
# ---------------------------------------------------------------------------


def test_admin_and_client_route_guards() -> None:
    assert can_access_admin("publisher")
    assert can_access_admin("admin")
    assert not can_access_admin("reader")
    assert not can_access_admin(None)
    assert can_access_client("reader")
    assert can_access_client("publisher")
    assert not can_access_client(None)


def test_javascript_declares_the_same_admin_roles() -> None:
    roles = (WEB_STATIC / "js" / "roles.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "admin" in roles and "publisher" in roles
    assert 'access: "admin"' in app_js
    assert 'access: "client"' in app_js
    assert "/admin/source-files" in views
    assert "/client/facts" in views
    assert "dfip_core" not in app_js


def _is_nav_current(href: str, path: str) -> bool:
    """Mirror of ``isNavCurrent`` in ``components.js`` (section roots are exact-only)."""
    if path == href:
        return True
    if href in {"/admin", "/client"}:
        return False
    return path.startswith(f"{href}/")


def test_nav_current_state_is_exclusive_per_route() -> None:
    source = (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    assert "export function isNavCurrent" in source
    assert 'href === "/admin"' in source
    assert 'href === "/client"' in source
    assert "isNavCurrent(href, current)" in source
    assert 'current.startsWith(`${href}/`) ? "page"' not in source

    nav_hrefs = [
        "/admin",
        "/admin/upload",
        "/admin/source-files",
        "/admin/batches",
        "/admin/processing-runs",
        "/admin/review",
        "/admin/facts",
        "/admin/history",
        "/admin/logic",
        "/admin/labels",
        "/admin/publications",
        "/admin/downloads",
        "/client",
        "/client/facts",
    ]
    paths = {
        "/admin": "/admin",
        "/admin/upload": "/admin/upload",
        "/admin/source-files": "/admin/source-files",
        "/admin/source-files/b0000000-0000-4000-8000-000000000001": "/admin/source-files",
        "/admin/batches": "/admin/batches",
        "/admin/batches/c0000000-0000-4000-8000-000000000001": "/admin/batches",
        "/admin/processing-runs": "/admin/processing-runs",
        "/admin/processing-runs/d0000000-0000-4000-8000-000000000001": "/admin/processing-runs",
        "/admin/review": "/admin/review",
        "/admin/facts": "/admin/facts",
        "/admin/facts/detail": "/admin/facts",
        "/admin/history": "/admin/history",
        "/admin/logic": "/admin/logic",
        "/admin/labels": "/admin/labels",
        "/admin/publications": "/admin/publications",
        "/admin/downloads": "/admin/downloads",
        "/client": "/client",
        "/client/facts": "/client/facts",
        "/client/facts/detail": "/client/facts",
    }
    for path, expected in paths.items():
        active = [href for href in nav_hrefs if _is_nav_current(href, path)]
        assert active == [expected], path


def test_dev_token_role_default_is_reader() -> None:
    app = api_app_with_data()
    with api_client_for(app) as client:
        session = client.session()
    assert session["role"] == "reader"
    assert not can_access_admin(session["role"])
    assert can_access_client(session["role"])


def test_dev_token_role_can_be_publisher_in_test() -> None:
    app = api_app_with_data(dfip_dev_auth_role="publisher")
    with api_client_for(app) as client:
        session = client.session()
    assert session["role"] == "publisher"
    assert can_access_admin(session["role"])


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


def test_api_client_health_is_public() -> None:
    with api_client_for(api_app_with_data(), token=None) as client:
        body = client.health()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "not_checked"


def test_api_client_401_without_token() -> None:
    with api_client_for(api_app_with_data(), token=None) as client:
        with pytest.raises(ApiClientError) as caught:
            client.session()
    assert caught.value.status_code == 401
    assert caught.value.code == "AUTHENTICATION_FAILED"


def test_api_client_401_invalid_token() -> None:
    with api_client_for(api_app_with_data(), token="wrong") as client:
        with pytest.raises(ApiClientError) as caught:
            client.list_facts()
    assert caught.value.status_code == 401
    assert DEV_TOKEN not in str(caught.value)


def test_api_client_403_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "AUTHORIZATION_FAILED",
                    "message": "Not authorized to access this resource.",
                }
            },
        )

    with DfipApiClient(
        "http://example.invalid",
        token="x",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ApiClientError) as caught:
            client.list_facts()
    assert caught.value.status_code == 403
    assert caught.value.code == "AUTHORIZATION_FAILED"


def test_api_client_404_and_422_and_pagination() -> None:
    app = api_app_with_data(dfip_dev_auth_role="publisher")
    with api_client_for(app) as client:
        with pytest.raises(ApiClientError) as missing:
            client.get_source_file("e0000000-0000-4000-8000-000000000099")
        assert missing.value.status_code == 404
        assert missing.value.code == "NOT_FOUND"

        with pytest.raises(ApiClientError) as invalid:
            client.get_source_file("not-a-uuid")
        assert invalid.value.status_code == 422

        page = client.list_facts(limit=1, offset=0)
        assert page["pagination"]["limit"] == 1
        assert page["pagination"]["total"] == 3
        assert len(page["items"]) == 1


def test_api_client_preserves_null_and_empty_string() -> None:
    app = api_app_with_data(dfip_dev_auth_role="publisher")
    with api_client_for(app) as client:
        facts = client.list_facts()["items"]
        leading = next(item for item in facts if item["campaign_id"] == "camp-1")
        assert leading["campaign_name"] == " leading"
        assert leading["filter_logic_1"] is None
        assert leading["template_status"] == ""
        assert leading["total_cost"] == "2.50"
        assert isinstance(leading["total_cost"], str)
        blank = next(item for item in facts if item["campaign_id"] == "camp-2")
        assert blank["variation_id"] is None
        assert blank["variation_id_key"] == ""


def test_api_client_reads_lineage_resources() -> None:
    app = api_app_with_data(dfip_dev_auth_role="publisher")
    with api_client_for(app) as client:
        source = client.get_source_file(FILE_A)
        assert source["original_filename"] == "alpha.xlsx"
        assert "storage_uri" not in source
        batch = client.get_batch(BATCH_A)
        assert batch["source_file_id"] == FILE_A
        run = client.get_processing_run(RUN_A)
        assert run["batch_id"] == BATCH_A
        rows = client.list_staged_rows(BATCH_A)
        assert rows["pagination"]["total"] == 3
        history = client.list_fact_history()
        assert history["pagination"]["total"] == 1


def test_api_client_503_and_network_failure() -> None:
    class DownRepository:
        def list_facts(self, **_kwargs):
            raise PersistenceUnavailableError()

    app = create_app(
        settings=inspector_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        repository=DownRepository(),  # type: ignore[arg-type]
    )
    with api_client_for(app) as client:
        with pytest.raises(ApiClientError) as caught:
            client.list_facts()
    assert caught.value.status_code == 503
    assert caught.value.code == "PERSISTENCE_UNAVAILABLE"

    class FailTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("failed", request=request)

    with DfipApiClient("http://example.invalid", token="x", transport=FailTransport()) as client:
        with pytest.raises(ApiClientError) as network:
            client.health()
    assert network.value.code == NETWORK_FAILURE


def test_javascript_api_client_uses_bearer_and_json_parse() -> None:
    source = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    assert "Authorization" in source
    assert "Bearer" in source
    assert "JSON.parse" in source
    assert "NETWORK_FAILURE" in source
    assert "AUTHENTICATION_FAILED" in source
    assert "/facts/history" in source
    assert "dfip_core" not in source
    assert "DFIP_AUTH_SECRET" not in source


def test_spa_upload_and_published_download_wiring() -> None:
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    assert "uploadWorkbook" in client_js
    assert "/uploads" in client_js
    assert "FormData" in client_js
    assert "multipart: true" in client_js
    assert "facts.csv" in client_js
    assert "facts.xlsx" in client_js
    assert "downloadPublishedFacts" in client_js
    assert "uploadWorkbook" in app_js
    assert "getBatch" in app_js
    assert "listProcessingRuns" in app_js
    assert "dfip.pendingRawUpload" in app_js
    assert "2000" in app_js
    assert "rawUploadLifecycle" in app_js
    assert "downloadPublishedFacts" in app_js
    assert 'data-upload-form="true"' in views
    assert "data-upload-status" in views
    assert "The website stays usable while this file is processed" in views
    assert "data-download-published" in views
    assert "does not publish" in views.lower()
    assert "excel export" not in client_js.lower()
    assert "excel export" not in app_js.lower()
    assert "excel export" not in views.lower()
    admin_list = app_js[
        app_js.index('if (name === "admin-facts")') : app_js.index(
            'if (name === "admin-fact-detail")'
        )
    ]
    assert "listFacts" in admin_list
    assert "listPublishedFacts" not in admin_list
    assert "uploadWorkbook" not in admin_list
    client_list = app_js[
        app_js.index('if (name === "client-facts")') : app_js.index(
            'if (name === "client-fact-detail")'
        )
    ]
    assert "listPublishedFacts" in client_list
    assert "listFacts" not in client_list
    assert "uploadWorkbook" not in client_list
    request_fn = client_js[client_js.index("async request(") :]
    multipart_idx = request_fn.index("if (multipart)")
    json_idx = request_fn.index('headers["Content-Type"] = "application/json"')
    assert multipart_idx < json_idx


def test_javascript_preserves_null_and_empty_display() -> None:
    source = (WEB_STATIC / "js" / "format.js").read_text(encoding="utf-8")
    assert "value === null" in source
    assert 'value === ""' in source
    assert "NULL" in source


def test_html_template_does_not_double_escape_nested_markup() -> None:
    source = (WEB_STATIC / "js" / "format.js").read_text(encoding="utf-8")
    assert "function interpolate(" in source
    assert "function isSafeHtml(" in source
    assert "function safeHtml(" in source
    assert "export function toHtml(" in source
    interpolate = source[source.index("function interpolate(") :]
    assert interpolate.index("isSafeHtml(value)") < interpolate.index("return escapeHtml(value)")
    assert "value.map(interpolate)" in interpolate
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert app_js.count("root.innerHTML = toHtml(") >= 4
    assert "root.innerHTML = layout(" not in app_js
    assert "root.innerHTML = body;" not in app_js
    assert "root.innerHTML = credentialView(" not in app_js
    assert "root.innerHTML = errorBanner(" not in app_js
    handle = app_js[app_js.index("function handleError(") :]
    assert "errorBanner(error)" in handle
    assert "notFoundView()" not in handle.split("root.addEventListener")[0]


def test_cost_cells_preserve_null_versus_empty_string() -> None:
    source = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    assert "function moneyCell(" in source
    assert "value === null" in source
    assert 'value === ""' in source
    assert "moneyCell(item.total_cost)" in source
    assert "moneyCell(item.revenue_inr)" in source
    assert 'html`<span class="mono">${item.total_cost}</span>`' not in source


# ---------------------------------------------------------------------------
# Security / scope
# ---------------------------------------------------------------------------


def test_frontend_source_has_no_secrets_or_p7_scope() -> None:
    forbidden = (
        "DFIP_AUTH_SECRET",
        "DATABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "postgresql://",
        DEV_TOKEN,
        JWT_SECRET,
        "power query",
        "row level security",
        "excel export",
        "kpi engine",
        "qa engine",
    )
    for path in WEB_STATIC.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in forbidden:
            assert token.lower() not in lowered, f"{path} contains {token}"


def test_p6_does_not_import_core_or_duplicate_transform() -> None:
    combined = ""
    for path in (ROOT / "packages" / "web" / "dfip_web").rglob("*.py"):
        combined += path.read_text(encoding="utf-8")
    assert "import dfip_core" not in combined
    assert "from dfip_core" not in combined
    assert "resolve_campaign_label" not in combined
    assert "calculate_total_cost" not in combined


def test_packages_web_does_not_duplicate_p5_routes() -> None:
    app_source = (ROOT / "packages" / "web" / "dfip_web" / "app.py").read_text(encoding="utf-8")
    assert "source-files" not in app_source
    assert "fact_campaign_day" not in app_source
    assert "create_web_app" in app_source
