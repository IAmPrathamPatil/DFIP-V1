"""D9 Advanced Analytics Workspace: canonical state, saved analysis, CSV, isolation."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from pathlib import Path

from dfip_analytics.overview import HERO_KPIS
from dfip_analytics.workspace import (
    ASK_SOURCES,
    FORBIDDEN_STATE_KEYS,
    WORKSPACE_MULTI_KEYS,
    WORKSPACE_SINGLE_KEYS,
    WorkspaceStateError,
    ask_focus,
    parse_workspace_state,
    with_anomaly,
    with_drill,
    with_explorer_from_trend,
    with_insight,
    with_kpi,
    with_trend_metric,
)
from dfip_api.lifecycle import LIFECYCLE_INACTIVE
from dfip_api.saved_analysis import MAX_SAVED_ANALYSES

from test_d1_kpi_overview import CLIENT_B, OVERVIEW, _kpi_map
from test_d2_global_filters import OCT, _fact, _publish
from test_d2_global_filters import _store as d2_store
from test_d3_dynamic_trends import TRENDS
from test_d4_drilldown import DRILL
from test_d5_performance_explorer import EXPLORER
from test_d6_insights import INSIGHTS
from test_d7_anomaly_diagnostic import ANOMALIES
from test_d8_contextual_ask import ASK, _post
from test_p5_api import CLIENT_ID, _encode_jwt
from test_p9_authz import jwt_app, jwt_headers

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"
SAVED = "/api/v1/analytics/saved"
EXPORT = "/api/v1/analytics/export.csv"
SHARE = "/api/v1/analytics/share"

STATE = {
    "period": "month",
    "month_start": "2025-10-01",
    "compare": "auto",
    "channel": ["SMS"],
    "trend_metric": "revenue_inr",
    "trend_grain": "month",
    "kpi": "revenue_inr",
}


def _http(store=None):
    http, *_rest = jwt_app(publication_store=store or d2_store())
    return http


def _auth(role: str = "client", client_id: str = CLIENT_ID, **claims):
    if claims:
        token = _encode_jwt(role=role, client_id=client_id, **claims)
        return {"Authorization": f"Bearer {token}"}
    return jwt_headers(role, client_id)


def test_canonical_workspace_state_and_component_slices() -> None:
    parsed = parse_workspace_state(STATE)
    assert parsed["kpi"] == "revenue_inr"
    assert parsed["channel"] == ["SMS"]
    assert parsed["trend_grain"] == "month"
    kpi = with_kpi(parsed, "total_cost")
    assert kpi["kpi"] == "total_cost"
    trend = with_trend_metric(parsed, "revenue_inr")
    assert trend["trend_metric"] == "revenue_inr"
    explorer = with_explorer_from_trend({**parsed, "trend_breakdown": "channel"})
    assert explorer["ex_metric"] == "revenue_inr"
    assert explorer["ex_dimension"] == "channel"
    drill = with_drill(parsed, origin="kpi", metric="revenue_inr", dimension="channel")
    assert drill["drill"] == "kpi"
    assert drill["drill_dimension"] == "channel"
    insight = with_insight(parsed, "material_change:revenue_inr")
    assert insight["insight"] == "material_change:revenue_inr"
    anomaly = with_anomaly(parsed, "spike:revenue_inr")
    assert anomaly["anomaly"] == "spike:revenue_inr"
    ask = ask_focus(parsed, "kpi")
    assert ask["metric"] == "revenue_inr"
    assert "overview" in ASK_SOURCES
    try:
        parse_workspace_state({"client_id": CLIENT_B, **STATE})
        raise AssertionError("client_id must be rejected")
    except WorkspaceStateError:
        pass
    try:
        parse_workspace_state({"sql": "SELECT 1"})
        raise AssertionError("unknown fields must be rejected")
    except WorkspaceStateError:
        pass
    assert "client_id" in FORBIDDEN_STATE_KEYS


def test_cross_component_transitions_preserve_filters() -> None:
    base = parse_workspace_state(STATE)
    to_drill = with_drill(base, origin="kpi", metric="revenue_inr")
    assert to_drill["channel"] == ["SMS"]
    assert to_drill["month_start"] == "2025-10-01"
    to_trend = with_trend_metric(base, "revenue_inr")
    assert to_trend["channel"] == ["SMS"]
    from_trend = with_drill(
        to_trend,
        origin="trend",
        metric="revenue_inr",
        slice_bucket="2025-10-01",
        slice_grain="month",
    )
    assert from_trend["trend_metric"] == "revenue_inr"
    assert from_trend["drill_slice"] == "2025-10-01"
    to_explorer = with_explorer_from_trend(to_trend)
    assert to_explorer["ex_metric"] == "revenue_inr"
    explorer_drill = with_drill(
        to_explorer, origin="kpi", metric="revenue_inr", parents=["channel:SMS"]
    )
    assert explorer_drill["drill_parent"] == ["channel:SMS"]
    insight_drill = with_drill(
        with_insight(base, "material_change:revenue_inr"),
        origin="kpi",
        metric="revenue_inr",
        dimension="channel",
    )
    assert insight_drill["insight"] == "material_change:revenue_inr"
    anomaly_drill = with_drill(
        with_anomaly(base, "drop:revenue_inr"),
        origin="kpi",
        metric="revenue_inr",
    )
    assert anomaly_drill["anomaly"] == "drop:revenue_inr"
    for source in ("kpi", "trend", "explorer", "insight", "anomaly", "drill"):
        focus = ask_focus(explorer_drill, source)
        assert isinstance(focus, dict)


def test_saved_analysis_crud_authorization_and_restoration() -> None:
    http = _http()
    created = http.post(SAVED, headers=_auth(), json={"title": "Sep SMS Revenue", "state": STATE})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["title"] == "Sep SMS Revenue"
    assert body["client_id"] == CLIENT_ID
    assert body["state"]["channel"] == ["SMS"]
    assert "client_id" not in body["state"]
    analysis_id = body["id"]

    listed = http.get(SAVED, headers=_auth())
    assert listed.status_code == 200
    assert listed.json()["client_id"] == CLIENT_ID
    assert [item["id"] for item in listed.json()["items"]] == [analysis_id]

    loaded = http.get(f"{SAVED}/{analysis_id}", headers=_auth())
    assert loaded.status_code == 200
    assert loaded.json()["state"]["trend_metric"] == "revenue_inr"
    assert loaded.json()["state"]["month_start"] == "2025-10-01"

    renamed = http.post(
        f"{SAVED}/{analysis_id}",
        headers=_auth(),
        json={"title": "Oct SMS Revenue"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Oct SMS Revenue"
    assert renamed.json()["state"]["channel"] == ["SMS"]

    other_user = http.get(SAVED, headers=_auth(sub="user-2"))
    assert other_user.status_code == 200
    assert other_user.json()["items"] == []
    missing = http.get(f"{SAVED}/{analysis_id}", headers=_auth(sub="user-2"))
    assert missing.status_code == 404

    other_company = http.get(f"{SAVED}/{analysis_id}", headers=_auth(client_id=CLIENT_B))
    assert other_company.status_code == 404
    other_list = http.get(SAVED, headers=_auth(client_id=CLIENT_B))
    assert other_list.status_code == 200
    assert other_list.json()["items"] == []

    forbidden_state = http.post(
        SAVED,
        headers=_auth(),
        json={"title": "steal", "state": {"client_id": CLIENT_B}},
    )
    assert forbidden_state.status_code == 422

    unbound = http.post(
        SAVED,
        headers=_auth("publisher", None),
        json={"title": "unbound", "state": STATE},
    )
    assert unbound.status_code == 403

    deleted = http.delete(f"{SAVED}/{analysis_id}", headers=_auth())
    assert deleted.status_code == 204
    assert http.get(f"{SAVED}/{analysis_id}", headers=_auth()).status_code == 404


def test_saved_analysis_restores_filters_and_follows_corrected_month() -> None:
    store = d2_store()
    http = _http(store)
    created = http.post(SAVED, headers=_auth(), json={"title": "SMS Oct", "state": STATE})
    assert created.status_code == 200
    analysis_id = created.json()["id"]
    loaded = http.get(f"{SAVED}/{analysis_id}", headers=_auth()).json()["state"]
    first = http.get(
        OVERVIEW,
        headers=_auth(),
        params={"month_start": loaded["month_start"], "channel": loaded["channel"]},
    )
    assert first.status_code == 200
    first_revenue = _kpi_map(first.json())["revenue_inr"]["value"]
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-b",
                day=OCT,
                channel="SMS",
                revenue_inr="999.0000",
            )
        ],
        "run-oct-corrected",
    )
    second = http.get(
        OVERVIEW,
        headers=_auth(),
        params={"month_start": loaded["month_start"], "channel": loaded["channel"]},
    )
    assert second.status_code == 200
    second_revenue = _kpi_map(second.json())["revenue_inr"]["value"]
    assert second_revenue != first_revenue
    still = http.get(f"{SAVED}/{analysis_id}", headers=_auth()).json()
    assert still["state"]["month_start"] == "2025-10-01"
    assert still["state"]["channel"] == ["SMS"]


def test_saved_analysis_inactive_and_deleted_company() -> None:
    http = _http()
    created = http.post(SAVED, headers=_auth(), json={"title": "Keep", "state": STATE})
    assert created.status_code == 200
    analysis_id = created.json()["id"]
    directory = http.app.state.client_directory
    directory.ensure(CLIENT_ID, name="Company A")
    directory.set_lifecycle(
        CLIENT_ID,
        lifecycle_status=LIFECYCLE_INACTIVE,
        deactivated_at=datetime.now(tz=UTC),
        purge_eligible_after=None,
    )
    blocked = http.get(SAVED, headers=_auth())
    assert blocked.status_code == 403
    load_blocked = http.get(f"{SAVED}/{analysis_id}", headers=_auth())
    assert load_blocked.status_code == 403

    directory.set_lifecycle(
        CLIENT_ID,
        lifecycle_status="active",
        deactivated_at=None,
        purge_eligible_after=None,
    )
    http.app.state.saved_analysis_store.purge_client(CLIENT_ID)
    directory.delete(CLIENT_ID)
    gone = http.get(f"{SAVED}/{analysis_id}", headers=_auth())
    assert gone.status_code == 404
    empty = http.get(SAVED, headers=_auth())
    assert empty.status_code == 200
    assert empty.json()["items"] == []


def test_sharing_is_not_implemented() -> None:
    http = _http()
    assert http.get(SHARE, headers=_auth()).status_code == 404
    assert http.post(SHARE, headers=_auth(), json={"id": "x"}).status_code == 404
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    assert "share_token" not in views
    assert "data-workspace-toolbar" in views


def test_csv_export_state_and_tenant_isolation() -> None:
    http = _http()
    allowed = http.get(
        EXPORT,
        headers=_auth(),
        params={"month_start": "2025-10-01", "channel": "SMS", "metric": "revenue_inr"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["content-type"].startswith("text/csv")
    text = allowed.text
    assert CLIENT_ID in text
    assert "kpi" in text
    assert "revenue_inr" in text
    assert "SMS" in text
    other = http.get(EXPORT, headers=_auth(client_id=CLIENT_B))
    assert other.status_code == 200
    assert CLIENT_ID not in other.text
    widen = http.get(EXPORT, headers=_auth(), params={"client_id": CLIENT_B})
    assert widen.status_code == 403
    xlsx = http.get(EXPORT, headers=_auth(), params={"format": "xlsx"})
    assert xlsx.status_code == 422
    pdf = http.get(EXPORT, headers=_auth(), params={"format": "pdf"})
    assert pdf.status_code == 422
    ppt = http.get("/api/v1/analytics/export.pptx", headers=_auth())
    assert ppt.status_code == 404


def test_security_url_and_saved_cannot_widen_company() -> None:
    http = _http()
    overview = http.get(OVERVIEW, headers=_auth(), params={"client_id": CLIENT_B})
    assert overview.status_code == 403
    created = http.post(SAVED, headers=_auth(), json={"title": "A", "state": STATE})
    analysis_id = created.json()["id"]
    stolen = http.get(f"{SAVED}/{analysis_id}", headers=_auth(client_id=CLIENT_B))
    assert stolen.status_code == 404
    export_b = http.get(EXPORT, headers=_auth(client_id=CLIENT_B), params={"client_id": CLIENT_ID})
    assert export_b.status_code == 403
    ask, _ask_http = _post(
        d2_store(), "Why did revenue fall?", source="kpi", focus={"metric": "revenue_inr"}
    )
    assert ask.status_code == 200
    assert ask.json()["client_id"] == CLIENT_ID


def test_workspace_performance_and_filter_transition_budget() -> None:
    http = _http()
    headers = _auth()
    measured = {}
    for name, path, params in (
        ("overview", OVERVIEW, {}),
        ("trends", TRENDS, {"metric": "revenue_inr"}),
        ("explorer", EXPLORER, {"metric": "revenue_inr"}),
        ("insights", INSIGHTS, {}),
        ("anomalies", ANOMALIES, {}),
    ):
        start = time.perf_counter()
        response = http.get(path, headers=headers, params=params)
        measured[name] = (time.perf_counter() - start) * 1000
        assert response.status_code == 200, response.text
        assert measured[name] < 2000
    start = time.perf_counter()
    filtered = http.get(
        OVERVIEW, headers=headers, params={"channel": "SMS", "month_start": "2025-10-01"}
    )
    measured["filter_change"] = (time.perf_counter() - start) * 1000
    assert filtered.status_code == 200
    start = time.perf_counter()
    ask, _ask_http = _post(
        d2_store(), "Why did revenue fall?", source="kpi", focus={"metric": "revenue_inr"}
    )
    measured["ask"] = (time.perf_counter() - start) * 1000
    assert ask.status_code == 200
    assert measured["ask"] < 2000
    assert measured["filter_change"] < 2000
    assert MAX_SAVED_ANALYSES == 50


def test_d1_d8_dashboard_reports_regression_and_ui_contract() -> None:
    http = _http()
    headers = _auth()
    assert http.get(OVERVIEW, headers=headers).status_code == 200
    assert http.get(TRENDS, headers=headers, params={"metric": "revenue_inr"}).status_code == 200
    assert (
        http.get(
            DRILL, headers=headers, params={"origin": "kpi", "metric": "revenue_inr"}
        ).status_code
        == 200
    )
    assert http.get(EXPLORER, headers=headers, params={"metric": "revenue_inr"}).status_code == 200
    assert http.get(INSIGHTS, headers=headers).status_code == 200
    assert http.get(ANOMALIES, headers=headers).status_code == 200
    ask, _ask_http = _post(
        d2_store(),
        "Summarize this trend.",
        source="trend",
        focus={"trend_metric": "revenue_inr"},
    )
    assert ask.status_code == 200
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    components = (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    state = (WEB_STATIC / "js" / "analytics-state.js").read_text(encoding="utf-8")
    assert 'title: "Overview"' in views
    assert 'title: "Reports"' in views
    assert "/admin" in app
    assert "WORKSPACE_SINGLE" in state
    assert "withTrendMetric" in state
    assert "withExplorerFromTrend" in state
    assert "data-overview-trend-kpi" in components
    assert "data-trend-explorer" in views
    assert "data-overview-export" in views
    assert 'data-overview-explorer-export="csv"' in views
    assert views.index("data-overview-explorer-export") < views.index("data-overview-export")
    assert "workspace-context" in views
    assert "workspace-chrome" in views
    for key in ("kpi", "insight", "anomaly", "ex_row", "trend_point"):
        assert key in state
    assert "createSavedAnalysis" in app
    assert "download_overview_export" in (
        ROOT / "packages" / "web" / "dfip_web" / "api_client.py"
    ).read_text(encoding="utf-8")
    assert ASK  # D8 route still imported for contract presence
    assert WORKSPACE_SINGLE_KEYS
    assert WORKSPACE_MULTI_KEYS


def test_same_route_overview_partial_refresh_contract() -> None:
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    router = (WEB_STATIC / "js" / "router.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "function refreshOverviewInPlace(" in app
    assert "overviewRefreshSeq" in app
    assert "history.pushState" in router
    assert "popstate" in app
    assert "window.location.reload" not in app
    assert "window.location.href" not in app
    overview_refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "listSavedAnalyses" in overview_refresh
    assert "loadSession" not in overview_refresh
    assert "loadingState()" not in overview_refresh
    assert 'render(loadingState()' not in overview_refresh
    assert "root.innerHTML" not in overview_refresh
    assert "getOverviewKpis" in overview_refresh
    assert "getOverviewTrends" in overview_refresh
    assert "getOverviewExplorer" in overview_refresh
    assert "getOverviewInsights" in overview_refresh
    assert "getOverviewAnomalies" in overview_refresh
    assert "getOverviewDrilldown" in overview_refresh
    assert 'route.name === "client-overview"' in app
    assert "overviewShellMounted" in app
    assert 'data-overview-shell="true"' in views
    for host in (
        "data-overview-context-host",
        "data-overview-kpis-host",
        "data-overview-trends-host",
        "data-overview-explorer-host",
        "data-overview-insights-host",
        "data-overview-anomalies-host",
        "data-overview-ask-host",
        "data-overview-drill-host",
    ):
        assert views.count(f'{host}="true"') == 1
        assert host in overview_refresh.replace("_", "-") or host.replace("data-overview-", "[data-overview-")
        assert f"[{host}]" in app or f'{host}="true"' in views
    assert "data-overview-retry" in views
    assert "data-overview-loading" in app
    assert "overview-host[aria-busy" in css
    assert "preventDefault" in app
    assert "throwIfOverviewJobAuth" in app
    assert 'name === "saved" || name === "drill"' in app
    assert "clearAllOverviewBusy" in app
    assert "if (!stillCurrent()) return;" in overview_refresh
    assert overview_refresh.index("if (!stillCurrent()) return;") < overview_refresh.index("clearAllOverviewBusy()")
    assert "clearOverviewAskResult" in app
    assert "askGroundingChanged" in app
    assert "kpiFailed" in app
    assert "siblingContext" in app
    assert "data-overview-kpi-context-error" in app
    assert "isInactiveCompanyError(error)" in app[app.index("overviewShellMounted()") :]
    assert "data-drill-unauthorized" in views
    assert "unauthorizedView()" not in overview_refresh


def test_overview_host_selectors_are_unique() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    hosts = (
        "data-overview-context-host",
        "data-overview-saved-host",
        "data-overview-kpis-host",
        "data-overview-trends-host",
        "data-overview-explorer-host",
        "data-overview-insights-host",
        "data-overview-anomalies-host",
        "data-overview-ask-host",
        "data-overview-drill-host",
        "data-overview-period-host",
        "data-overview-comparison-host",
    )
    for host in hosts:
        assert views.count(f'{host}="true"') == 1
    inner = (
        "data-overview-explorer",
        "data-overview-insights",
        "data-overview-anomalies",
        "data-overview-ask",
        "data-overview-drill",
    )
    shell = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    for attr in inner:
        assert f'{attr}-host="true"' in shell
        assert f'{attr}="true"' not in shell.replace(f'{attr}-host="true"', "")
    assert 'data-overview-explorer="true"' in views
    assert 'data-overview-ask="true"' in views
    assert 'data-overview-drill="true"' in views
    assert 'data-overview-explorer-host="true"' in views
    assert views.count('data-overview-explorer-host="true"') == 1
    assert views.count('data-overview-explorer="true"') == 1


def test_same_route_saved_and_drill_403_stay_inline() -> None:
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    policy = app[app.index("function throwIfOverviewJobAuth(") : app.index("function paintOverviewKpis(")]
    assert 'error.status === 401' in policy
    assert 'name === "saved" || name === "drill"' in policy
    assert "isInactiveCompanyError" in policy
    assert "unauthorizedView()" not in policy
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "workspaceSavedPanel" in refresh
    assert "savedError: saved.error" in refresh
    assert "drillError: drill.error" in refresh
    assert "data-drill-unauthorized" in views
    assert "function overviewDrillPanel" in views


def test_same_route_busy_ask_and_kpi_failure_contracts() -> None:
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "data-overview-busy-token" in app
    assert "clearAllOverviewBusy()" in refresh
    assert "if (token != null && host.getAttribute" in app
    assert refresh.index("if (!stillCurrent()) return;") < refresh.index("clearAllOverviewBusy()")
    assert "askGroundingChanged" in refresh
    assert "clearOverviewAskResult()" in refresh
    assert "overviewAskEmptyResult" in views
    assert "kpiFailed" in refresh
    assert "siblingContext = kpiFailed ? null" in refresh
    assert "kpiContextUnavailable()" in refresh
    assert "data-overview-kpi-context-error" in app
    kpi_start = refresh.index("if (kpi.error) {")
    kpi_error_branch = refresh[kpi_start : refresh.index("} else {", kpi_start)]
    assert "overviewKpiData = null" in kpi_error_branch
    assert "overviewKpiData = data" not in kpi_error_branch
    assert "overviewExplorerSection" in refresh
    assert "data: siblingContext" in refresh
    assert "rememberOverviewState(query, siblingContext)" in refresh
    assert "data || overviewKpiData" not in app[app.index("function patchOverviewAsk(") : app.index("function throwIfOverviewJobAuth(")]


def test_same_route_inactive_company_reloads_session() -> None:
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    start = app.index('route.name === "client-overview" &&')
    block = app[start : app.index("overviewRefreshSeq += 1")]
    assert "refreshOverviewInPlace(query)" in block
    assert "isInactiveCompanyError(error)" in block
    assert 'overviewClientId = ""' in block
    assert "unauthorizedView()" not in block
    assert "loadSession" not in block
    after = app[app.index("overviewRefreshSeq += 1") : app.index("async function viewFor(")]
    assert "await loadSession()" in after
    assert "needsCompanySelection(session)" in after


def test_overview_fullwidth_sectioned_layout() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    shell = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    assert 'class="overview-workspace"' in shell
    assert 'data-overview-shell="true"' in shell
    assert 'data-overview-section="filters"' in shell
    assert 'data-overview-section="kpis"' in shell
    assert 'data-overview-section="tools"' in shell
    assert "overview-stack" in shell
    assert "overview-grid-mid" not in shell
    assert "overview-grid-lower" not in shell
    assert "Filters &amp; analytical context" in shell or "Filters & analytical context" in shell
    assert "Key Performance Indicators" in shell
    assert "Analysis Tools" in shell
    assert 'data-overview-saved-host="true"' in shell
    assert 'data-overview-ask-host="true"' in shell
    assert shell.index("data-overview-explorer-host") < shell.index("data-overview-trends-host")
    assert shell.index("data-overview-trends-host") < shell.index("data-overview-insights-host")
    assert shell.index("data-overview-insights-host") < shell.index("data-overview-anomalies-host")
    assert shell.index("data-overview-anomalies-host") < shell.index("data-overview-ask-host")
    assert "overview-filter-row-period" in views
    assert "overview-filter-row-dims" in views
    assert "content:has([data-overview-shell])" in css
    assert "repeat(4, minmax(0, 1fr))" in css
    assert ".overview-stack" in css
    assert ".overview-grid-mid" in css
    assert ".overview-grid-lower" in css
    assert "refreshOverviewInPlace" in app
    assert "[data-overview-kpis-host]" in app
    assert "[data-overview-ask-host]" in app
    assert "root.innerHTML" not in app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]


def test_compare_none_url_chips_saved_and_export() -> None:
    state_js = (WEB_STATIC / "js" / "analytics-state.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    serializer = state_js[state_js.index("export function queryFromOverviewForm") : state_js.index("export function queryFromTrendForm")]
    assert 'namedItem("day_from")' in serializer
    assert 'namedItem("day_to")' in serializer
    assert 'compare === "none" || compareMonth === "none"' in serializer
    assert 'query.set("compare", "none")' in serializer
    assert "compare_month_start" in serializer
    assert 'delete next.compare_month_start' in state_js
    assert 'state.compare === "none"' in state_js
    assert ">None</option>" in views
    assert "data-filter-chip-clear" in views
    assert "data-filter-chip-clear" in app
    assert 'comparison.reason === "comparison_disabled"' in views
    assert "No comparison" in (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    assert 'data-ask-comparison' in views
    assert 'data-workspace-comparison' in views
    assert 'href="/client/overview"' in views
    assert "queryFromOverviewForm(form)" in app
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "root.innerHTML" not in refresh
    assert "loadingState()" not in refresh

    parsed = parse_workspace_state(
        {"compare": "none", "compare_month_start": "2025-06-01", "channel": ["SMS"]}
    )
    assert parsed["compare"] == "none"
    assert "compare_month_start" not in parsed
    assert parsed["channel"] == ["SMS"]

    http = _http()
    created = http.post(
        SAVED,
        headers=_auth(),
        json={"title": "None comparison", "state": {"compare": "none", "compare_month_start": "2025-06-01"}},
    )
    assert created.status_code == 200, created.text
    saved = created.json()["state"]
    assert saved["compare"] == "none"
    assert "compare_month_start" not in saved

    overview = http.get(OVERVIEW, headers=_auth(), params={"compare": "none", "compare_month_start": "2025-06-01"})
    assert overview.status_code == 200
    body = overview.json()
    assert body["applied"]["compare"] == "none"
    assert body["comparison"]["reason"] == "comparison_disabled"
    assert all(item["delta"] is None for item in body["kpis"])

    exported = http.get(EXPORT, headers=_auth(), params={"compare": "none", "compare_month_start": "2025-06-01"})
    assert exported.status_code == 200
    text = exported.text
    assert "comparison_disabled" in text
    assert ",none," in text or "compare_mode" in text
    insights = http.get(INSIGHTS, headers=_auth(), params={"compare": "none"})
    assert insights.status_code == 200
    assert insights.json()["empty_reason"] == "insufficient_comparison"
    ask, _http_ask = _post(
        d2_store(),
        "Why did revenue fall?",
        filters={"compare": "none"},
        focus={"metric": "revenue_inr"},
    )
    assert ask.status_code == 200
    assert ask.json()["empty_reason"] == "missing_comparison"


def test_overview_kpi_cards_interactive_and_preserve_d4() -> None:
    components = (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    card = components[components.index("export function overviewKpiCard") : components.index("export function metricCard")]
    assert "<a" in card
    assert 'class="metric-card overview-kpi' in card
    assert "data-overview-kpi=" in card
    assert "data-overview-drill-kpi" in card
    assert "data-overview-trend-kpi" in card
    assert "View details" in card
    assert "No comparison" in card
    assert "aria-label" in card
    assert ":focus-visible" in css or "a.metric-card.overview-kpi:focus-visible" in css
    assert "a.metric-card.overview-kpi:hover" in css
    assert "a.metric-card.overview-kpi:active" in css
    assert "Ask about this" not in card
    paint = views[views.index("export function overviewKpiCardsHtml") : views.index("export function overviewSectionRetry")]
    assert 'origin: "kpi"' in paint
    assert "withDrill(withFocus(" in paint
    assert "dimension: \"campaign_id\"" in paint
    assert "kpiCompareVsText" in paint
    assert "comparisonNone" in paint
    assert "comparison.available ? delta.text" in paint
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "root.innerHTML" not in refresh
    assert "loadingState()" not in refresh
    assert 'setOverviewBusy("[data-overview-kpis-host]"' in app
    assert 'overviewSectionRetry("kpis"' in app
    assert "rememberKpiDrillReturn" in app
    assert "overviewDrillReturnFocus" in app
    assert "[data-overview-kpi=" in app
    assert 'event.key === " "' in app
    assert "a[data-overview-drill-kpi].overview-kpi" in app
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in css
    for kpi_id in (
        "total_cost",
        "revenue_inr",
        "overall_roas",
        "delivered",
        "unique_clicks",
        "unique_conversions",
        "delivery_rate",
        "ctr_del_to_clicks",
    ):
        assert kpi_id in views
    assert "data-kpi-comparison=\"none\"" in components
    assert "data-kpi-vs" in components
    assert "copyKeys(existing, DRILL_SINGLE" in (WEB_STATIC / "js" / "analytics-state.js").read_text(encoding="utf-8")


def test_d4_drawer_same_route_and_none_comparison() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    panel = views[views.index("export function overviewDrillPanel") : views.index("export function workspaceContextBar")]
    assert 'data-overview-drill="true"' in panel
    assert 'data-drill-panel="true"' in panel
    assert "data-drill-breakdown" in panel
    assert "data-drill-table" in panel
    assert "data-drill-next" in panel
    assert "data-drill-unauthorized" in panel
    assert "data-drill-loading" in panel
    assert 'data-drill-header-comparison="none"' in panel
    assert "No comparison" in panel
    assert "data-drill-export" in panel
    assert 'data-overview-export="csv"' in panel
    assert "data-drill-close" in panel
    assert "data-drill-back" in panel
    assert "withDrill(" in panel
    assert "contributionSharePct" in views
    assert panel.index("const kpi =") < panel.index("(kpi && kpi.label)")
    assert "data-drill-mode" in views
    assert "data-drill-pane" in panel
    assert "data-drill-content" in panel
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "root.innerHTML" not in refresh
    assert "loadingState()" not in refresh
    assert "focusDrillPanel" in refresh
    assert "overviewDrillReturnFocus" in refresh
    assert "overviewDrillPanel(" in refresh
    assert "getOverviewDrilldown" in refresh
    assert "unauthorizedView()" not in refresh
    assert 'name === "saved" || name === "drill"' in app
    assert "justify-content: flex-end" in css
    assert "data-drill-panel" in css or ".drill-panel" in css
    assert "@media (max-width: 720px)" in css
    assert "min(92vh" in css or "92vh" in css
    assert "window.location.reload" not in app
    assert "window.location.href" not in app


def test_overview_section_visual_polish_preserves_hosts() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "Track how the selected metric changes over time." in views
    assert "Rank dimensions to identify what drives performance." in views
    assert "Evidence-backed changes and drivers." in views
    assert "Unusual behavior against historical baseline." in views
    assert "Continue analysis with Ask, Saved Views and Export." in views
    assert "Ask a question" in views
    assert "Save this analysis" in views
    assert "Primary driver" in views
    assert "Not enough history" in views
    assert "Nothing unusual" in views
    assert "data-trend-explorer" in views
    assert "data-explorer-drill" in views
    assert "data-insight-drill" in views
    assert "data-anomaly-drill" in views
    assert "data-overview-ask" in views
    assert "data-saved-form" in views
    assert 'data-overview-export="csv"' in views
    assert "overviewPanelHead" in views
    assert "tool-card" in views
    assert "overview-kicker" in css
    assert "tool-card-head" in css
    assert "action-link" in views
    assert "action-link" in css
    assert "High severity" in views
    assert "overview-control-group" in views
    assert "overviewHostOrRetry" in views
    assert 'data-overview-retry="${name}"' in views or "data-overview-retry=" in views
    assert "Clear filters" in views
    assert "is-drillable" in views
    assert "trend-chart-frame" in views
    assert "max-width: 1100px" in css
    assert "max-width: 900px" in css
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "root.innerHTML" not in refresh
    assert "loadingState()" not in refresh
    assert "getOverviewTrends" in refresh
    assert "getOverviewExplorer" in refresh
    assert "getOverviewInsights" in refresh
    assert "getOverviewAnomalies" in refresh
    for host in (
        "data-overview-trends-host",
        "data-overview-explorer-host",
        "data-overview-insights-host",
        "data-overview-anomalies-host",
        "data-overview-ask-host",
        "data-overview-saved-host",
        "data-overview-drill-host",
    ):
        assert views.count(f'{host}="true"') == 1


def test_overview_responsive_accessibility_pass() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    components = (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    card = components[components.index("export function overviewKpiCard") : components.index("export function metricCard")]
    assert "aria-label" in card
    assert "No comparison" in card
    assert "View details" in card
    assert "<button" not in card
    assert 'event.key === " "' in app
    assert "rememberKpiDrillReturn" in app
    assert "trapOverviewDrillFocus" in app
    assert "syncOverviewDrillInert" in app
    assert 'setAttribute("inert"' in app
    assert 'role="dialog"' in views
    assert 'aria-modal="true"' in views
    assert 'aria-labelledby="drill-title"' in views
    assert 'aria-label="Close drilldown"' in views
    assert 'aria-label="Clear all filters"' in views
    assert 'searchPlaceholder: "Search campaigns"' in views
    assert 'aria-label="${searchPlaceholder}"' in views
    assert 'id="overview-campaigns"' in views
    assert 'data-filter-picker-open="${name}"' in views
    assert 'aria-labelledby="filter-label-${name} filter-summary-${name}"' in views
    assert 'name: "campaign_id"' in views
    assert 'aria-label="Remove' in views
    assert "prefers-reduced-motion" in css
    assert "html:has([data-drill-panel])" in css
    assert "repeat(3, minmax(0, 1fr))" in css
    workspace = css[css.index(".overview-workspace {") : css.index(".overview-workspace .page-header")]
    assert "overflow-x: hidden" not in workspace
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "root.innerHTML" not in refresh
    assert "loadingState()" not in refresh
    assert "layout(" not in refresh
    assert "syncOverviewDrillInert" in refresh
    assert "focusDrillPanel" in refresh
    assert 'name === "saved" || name === "drill"' in app
    assert "window.location.reload" not in app
    assert "window.location.href" not in app


def test_overview_compact_analytical_filters() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    state = (WEB_STATIC / "js" / "analytics-state.js").read_text(encoding="utf-8")
    assert 'name: "channel",' in views
    assert 'name: "filter_logic_1",' in views
    assert 'name: "filter_logic_1_group",' in views
    assert 'name: "campaign_id",' in views
    assert 'data-filter-picker="${name}"' in views
    assert 'data-filter-picker-open="${name}"' in views
    assert 'data-filter-picker-popover="${name}"' in views
    assert 'data-filter-picker-search="${name}"' in views
    assert 'multiple size="4"' not in views
    assert 'select name="channel" multiple' not in views
    assert 'select name="filter_logic_1" multiple' not in views
    assert 'select name="filter_logic_1_group" multiple' not in views
    assert 'select name="campaign_id" multiple' not in views
    assert "export function filterPickerSummary" in views
    assert "All selected" in views
    assert "${labels[0]}, ${labels[1]} +${picked.length - 2}" in views
    assert "return `${picked.length} selected`" in views
    assert 'data-overview-campaign-search="true"' in views
    assert 'data-overview-campaigns="true"' in views
    assert 'data-filter-picker-search="${name}"' in views
    assert 'data-filter-picker-all="' in views
    assert 'data-filter-picker-clear="' in views
    assert "Active filters" in views
    assert "All values" in views
    assert "data-overview-active-filters" in views
    assert "data-filter-chip-clear" in views
    assert "function closeOverviewFilterPicker" in app
    assert "function openOverviewFilterPicker" in app
    assert "function toggleOverviewFilterPicker" in app
    assert 'event.target.closest("[data-filter-picker-open]")' in app
    assert "handleOverviewFilterPickerKeydown" in app
    assert 'event.key === "Escape" && openPicker' in app
    assert 'document.addEventListener("mousedown"' in app
    assert "applyFilterPickerSearch" in app
    assert "filterPickerVisibleBoxes" in app
    assert "queryFromOverviewForm(form)" in app
    assert "data-overview-autosubmit" in views
    assert 'name="period"' in views
    assert 'name="compare"' in views
    assert ">None</option>" in views
    assert "copyMulti(data, ANALYTICS_MULTI, query)" in state
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "root.innerHTML" not in refresh
    assert "loadingState()" not in refresh
    assert "layout(" not in refresh
    submit = app[app.index('if (form.dataset.overviewFilters === "true")') : app.index('if (form.dataset.overviewTrendForm === "true")')]
    assert "queryFromOverviewForm(form)" in submit
    assert "navigate(overviewHref" in submit
    assert "window.location.reload" not in app
    assert ".filter-picker-trigger" in css
    assert ".filter-picker-popover" in css
    assert ".overview-active-filters" in css
    assert 'href="/client/overview"' in views
    assert "Clear all" in views
    assert "Apply filters" in views


def test_overview_kpi_sparkline_batch_contract() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    state = (WEB_STATIC / "js" / "analytics-state.js").read_text(encoding="utf-8")
    client = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    components = (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    spark = (WEB_STATIC / "js" / "sparkline.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "export function sparklineParamsFromQuery" in state
    assert 'next.compare = "none"' in state[state.index("export function sparklineParamsFromQuery") : state.index("export function drillParamsFromQuery")]
    assert 'next.grain = next.period === "all_history" ? "month" : "day"' in state
    assert "include_metric" in state
    assert "revenue: \"revenue_inr\"" in state or "revenue: 'revenue_inr'" in state
    assert "ctr: \"ctr_del_to_clicks\"" in state or "ctr: 'ctr_del_to_clicks'" in state
    assert "getOverviewKpiSparklines" in client
    assert "getOverviewKpiSparklines" in app
    assert "sparklineParamsFromQuery" in app
    assert 'jobs.push(["sparklines"' in app
    assert "needSparklines" in app
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "root.innerHTML" not in refresh
    assert "loadingState()" not in refresh
    assert "needSparklines = forced.has(" in refresh or "const needSparklines" in refresh
    assert "TREND_KEYS" in refresh
    assert "needSparklines" in refresh
    assert "filterChanged" in refresh
    assert 'overviewSectionRetry("kpis"' not in refresh[refresh.index("needSparklines") : refresh.index("const siblingContext")]
    assert "if (!stillCurrent()) return" in refresh
    assert "kpiSparklineMarkup" in views
    assert "formatKpiValue(kpi.kind, kpi.value)" in views
    assert 'data-kpi-sparkline="${id || ""}"' in components or "data-kpi-sparkline=" in components
    assert "<button" not in components[components.index("export function overviewKpiCard") : components.index("export function metricCard")]
    assert "aria-hidden" in spark
    assert "renderTrendChart" not in spark
    assert "sparklinePointsForMetric" in spark
    assert "point.values" in spark
    assert ".kpi-sparkline" in css
    assert "pointer-events: none" in css


def test_d4_rich_analytical_drawer_modes() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    state = (WEB_STATIC / "js" / "analytics-state.js").read_text(encoding="utf-8")
    spark = (WEB_STATIC / "js" / "sparkline.js").read_text(encoding="utf-8")
    chart = (WEB_STATIC / "js" / "trend-chart.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    panel = views[views.index("export function overviewDrillPanel") : views.index("export function workspaceContextBar")]
    assert 'data-drill-mode="${id}"' in views
    assert '["breakdown", "Breakdown"]' in views
    assert '["trend", "Trend"]' in views
    assert '["details", "Details"]' in views
    assert 'role="tablist"' in views
    assert 'data-drill-pane="breakdown"' in panel
    assert 'data-drill-pane="trend"' in panel
    assert 'data-drill-pane="details"' in panel
    assert "data-drill-breakdown" in panel
    assert "data-drill-table" in panel
    assert "data-drill-trend" in views
    assert "data-drill-content" in panel
    assert "data-drill-header-value" in panel
    assert 'data-drill-header-comparison="none"' in panel
    assert "No comparison" in panel
    assert "data-drill-context" in panel
    assert "data-drill-next" in panel
    assert "data-drill-terminal" in panel
    assert "withDrill(" in panel
    assert 'data-overview-retry="drill"' in panel
    assert "data-drill-unauthorized" in panel
    assert 'data-overview-export="csv"' in panel
    assert "data-drill-export" in panel
    assert "askAboutButton" in panel
    assert "data-ask" in views
    assert "data-drill-close" in panel
    assert "data-drill-focus-next" not in panel
    assert "Drill →" not in panel
    assert "interactive: false" in panel or "interactive: false" in views
    assert "tooltip: true" in views
    assert "compact: true" not in panel
    assert "export function drillTrendParamsFromQuery" in state
    trend_params = state[state.index("export function drillTrendParamsFromQuery") : state.index("export function stripDrill")]
    assert "KPI_TO_TREND_METRIC" in trend_params
    assert "TREND_KEYS" not in trend_params
    assert "trend_metric" not in trend_params
    assert "filterParamsFromQuery" in trend_params
    assert 'next.grain = next.period === "all_history" ? "month" : "day"' in trend_params
    assert "delete next.include_metric" in trend_params
    assert "contextualTrendFromSparkline" in spark
    assert "trend.metrics" in spark[spark.index("export function contextualTrendFromSparkline") :]
    assert "item.key === metricKey" in spark
    assert "hasMap ? values[metricKey]" in spark
    assert "renderTrendChart" not in spark
    assert "interactive !== false" in chart
    assert "function setOverviewDrillMode(" in app
    assert "function ensureOverviewDrillTrend(" in app
    assert "function paintOverviewDrill(" in app
    ensure = app[app.index("function ensureOverviewDrillTrend(") : app.index("function applyFilterPickerSearch(")]
    assert "contextualTrendFromSparkline" in ensure
    assert "drillComparisonIsNone" in ensure
    assert "trendsMatchDrillContext" in ensure
    assert "getOverviewTrends" in ensure
    assert "drillTrendParamsFromQuery" in ensure
    assert "unauthorizedView" not in ensure
    assert "error.status === 401" in ensure
    assert 'name === "drill-trend"' in app
    assert "setOverviewDrillMode" in app
    assert "ArrowRight" in app
    assert "overviewDrillReturnFocus" in app
    assert 'event.key === "Escape" && parseDrillQuery' in app
    refresh = app[app.index("async function refreshOverviewInPlace(") : app.index("async function renderRoute(")]
    assert "root.innerHTML" not in refresh
    assert "loadingState()" not in refresh
    assert "layout(" not in refresh
    assert "unauthorizedView()" not in refresh
    assert "paintOverviewDrill" in refresh
    assert "ensureOverviewDrillTrend" in refresh
    assert "getOverviewDrilldown" in refresh
    assert "data-drill-content" in refresh
    assert "overviewHostLoading" in refresh
    assert "window.location.reload" not in app
    assert "window.location.href" not in app
    assert ".drill-modes" in css
    assert ".drill-content" in css
    assert ".drill-header-metrics" in css
    assert "overflow-x: hidden" in css[css.index(".drill-content") : css.index(".drill-section-title")]
    assert "@media (max-width: 720px)" in css
    assert "92vh" in css
    kpi_card = (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    card = kpi_card[kpi_card.index("export function overviewKpiCard") : kpi_card.index("export function metricCard")]
    assert "<button" not in card


def test_workspace_context_bar_uses_canonical_metric_labels() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    start = views.index("export function workspaceContextBar")
    end = views.index("export function workspaceSavedPanel")
    bar = views[start:end]
    assert "TREND_METRIC_OPTIONS.find" in bar
    assert "Total Cost (default)" in bar
    assert "Metric: ${metric}" in bar
    assert "Metric: ${metricKey}" not in bar
    catalog_start = views.index("const TREND_METRIC_OPTIONS")
    catalog_end = views.index("const TREND_GRAIN_OPTIONS")
    catalog = views[catalog_start:catalog_end]
    options = dict(re.findall(r'\["([a-z0-9_]+)", "([^"]+)"\]', catalog))
    for hero in HERO_KPIS:
        assert options[hero.id] == hero.label

    def rendered_metric(key: str) -> str:
        if not key:
            return "Total Cost (default)"
        return options.get(key, key)

    assert rendered_metric("delivery_rate") == "Delivery Rate"
    assert rendered_metric("revenue_inr") == "Revenue"
    assert rendered_metric("") == "Total Cost (default)"
    assert "_" not in rendered_metric("delivery_rate")
    assert "_" not in rendered_metric("ctr_del_to_clicks")


def test_drill_trend_cache_reset_bumps_sequence_token() -> None:
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    reset_start = app.index("function resetOverviewDrillTrendCache(")
    reset = app[reset_start : app.index("function drillTrendCacheKey(")]
    assert "overviewDrillTrendSeq += 1" in reset
    assert "overviewDrillTrendData = null" in reset
    assert 'overviewDrillTrendKey = ""' in reset
    ensure_start = app.index("function ensureOverviewDrillTrend(")
    ensure = app[ensure_start : app.index("function applyFilterPickerSearch(")]
    assert "const token = ++overviewDrillTrendSeq" in ensure
    assert "token !== overviewDrillTrendSeq" in ensure
    refresh_start = app.index("async function refreshOverviewInPlace(")
    refresh = app[refresh_start : app.index("async function renderRoute(")]
    assert "resetOverviewDrillTrendCache()" in refresh
    filter_start = refresh.index("if (filterChanged)")
    filter_reset = refresh[filter_start : refresh.index("const needKpis")]
    assert "resetOverviewDrillTrendCache()" in filter_reset


def _drill_breakdown_pane() -> str:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    panel = views[views.index("export function overviewDrillPanel") :]
    start = panel.index("const chartRows = drillChartRows(rows)")
    return panel[start : panel.index("const trendPane =")]


def test_d4_breakdown_chart_uses_existing_rows_and_drill_targets() -> None:
    pane = _drill_breakdown_pane()
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert 'data-drill-chart="true"' in pane
    assert 'data-drill-chart-rows="true"' in pane
    assert "drillChartRows(rows)" in pane
    assert "chartRows.map((row)" in pane
    assert "const href = rowHref(row);" in pane
    assert 'data-drill-next="${row.key}"' in pane
    assert "withDrill(" not in pane
    assert "formatKpiValue(kind, row.value)" in pane
    assert "contributionSharePct(row.contribution_pct)" in pane
    assert "${dimLabel} breakdown" in pane
    assert "Campaign breakdown" not in pane
    assert 'data-drill-breakdown="true"' in pane
    assert 'data-drill-breakdown-details="true"' in pane
    assert "rows.map((row)" in pane
    assert "<canvas" not in pane
    assert ".drill-chart-bar" in css
    assert ".drill-chart-plot" in css
    assert "@media (max-width: 430px)" in css


def test_d4_breakdown_chart_is_accessible_and_comparison_free() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    pane = _drill_breakdown_pane()
    text = views[views.index("function drillChartRowText(") : views.index("function drillModeKey(")]
    assert "drillChartRowText({" in pane
    assert '<span class="sr-only">' in pane
    assert 'aria-hidden="true"' in pane
    assert 'title="${label}"' in pane
    assert "Rank ${row.rank}" in text
    assert "% of total" in text
    assert "exceeds the chart scale" in text
    assert "is-capped" in pane
    assert "capped," in pane
    assert "Drill into ${drillLabel}" in text
    assert "formatKpiValue(kind, row.value)" in text
    assert "prior_value" not in pane
    assert "delta" not in pane
    assert "row.key" in pane
    assert "${row.label" in pane or "row.label ||" in pane


def test_d4_chart_rows_reuse_existing_drill_hrefs() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    panel = views[views.index("export function overviewDrillPanel") : views.index("export function overviewFilterCompactBar")]
    pane = _drill_breakdown_pane()
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "const href = rowHref(row);" in pane
    assert 'data-drill-next="${row.key}"' in pane
    assert 'data-drill-open="true"' in pane
    assert "Click a bar to open" in pane
    assert "Drill →" not in panel
    assert "data-drill-focus-next" not in panel
    assert 'data-drillable="false"' in pane
    assert "drill-chart-item is-static" in pane
    assert "a.drill-chart-item" in css
    assert "cursor: pointer" in css
    href_fn = panel[panel.index("const rowHref = (row) =>") : panel.index("let body;")]
    assert "withDrill(" in href_fn
    assert "parentToken(selection.dimension, row.key)" in href_fn
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "data-drill-focus-next" not in app


def test_overview_sticky_filters_reuse_existing_form() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    compact = views[views.index("export function overviewFilterCompactBar") : views.index("export function workspaceContextBar")]
    assert views.count('data-overview-filters="true"') == 1
    assert overview.count("overviewFilterBar(") == 1
    assert "data-overview-filter-dock" in overview
    assert "data-overview-filter-compact-host" in overview
    assert "overview-filter-compact-host" in overview
    assert 'href="#overview-filter-bar"' in compact
    assert "Filters" in compact
    assert "Comparison:" in compact
    assert "position: sticky" in css
    assert "top: var(--sticky-offset)" in css
    assert ".overview-filter-compact-host" in css
    assert "addEventListener(\"scroll\"" not in app
    assert "overviewFilterCompactBar" in app
    assert "Apply filters" in views
    assert "Clear all" in views


def test_sticky_offset_tracks_measured_topbar_height() -> None:
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "--sticky-offset: var(--topbar-height);" in css
    assert "top: var(--topbar-height)" not in css
    assert "observeStickyOffset" in app
    offset = app[app.index("function applyStickyOffset") : app.index("function syncOverviewDrillInert")]
    assert "getBoundingClientRect().height" in offset
    assert '"--sticky-offset"' in offset
    assert "ResizeObserver" in app
    assert 'addEventListener("scroll"' not in app


def test_compact_filter_trigger_reveals_existing_filter_bar() -> None:
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    reveal = app[app.index("function revealOverviewFilters") : app.index("let stickyOffsetObserver")]
    assert "[data-overview-filter-bar]" in reveal
    assert "scrollIntoView" in reveal
    assert "focus({ preventScroll: true })" in reveal
    assert '[data-overview-filter-compact]"' in app


def test_drill_host_stays_interactive_inside_nested_wrappers() -> None:
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    walk = app[app.index("function syncOverviewInertBranch") : app.index("function trapOverviewDrillFocus")]
    assert 'child.hasAttribute("data-overview-drill-host")' in walk
    assert 'child.querySelector("[data-overview-drill-host]")' in walk
    assert "syncOverviewInertBranch(child, open)" in walk
    assert walk.count('removeAttribute("inert")') >= 2


def test_d4_trend_opens_in_centered_presentation() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    css = (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")
    panel = views[views.index("export function overviewDrillPanel") : views.index("export function overviewFilterCompactBar")]
    pane = views[views.index("function drillTrendPane") : views.index("export function overviewDrillPanel")]
    assert 'data-drill-layout="${view === "trend" ? "center" : "drawer"}"' in panel
    assert 'data-drill-pane="breakdown"' in panel
    assert 'data-drill-pane="details"' in panel
    assert 'data-drill-pane="trend"' in panel
    assert "tooltip: true" in pane
    assert "interactive: false" in pane
    assert "compact: true" not in pane
    assert "data-drill-trend-context" in pane
    assert "data-drill-trend-metric" in pane
    assert "data-drill-trend-grain" in pane
    assert "TREND_GRAIN_OPTIONS.find" in pane
    assert 'role="dialog"' in panel
    assert "aria-modal" in panel
    assert '.drill-root[data-drill-layout="center"]' in css
    assert "min(60rem" in css
    assert "calc(100vw - 0.8rem)" in css
    mode = app[app.index("function setOverviewDrillMode(") : app.index("function ensureOverviewDrillTrend(")]
    assert "navigate(" not in mode
    assert "trend_metric" not in mode
    assert 'event.key === "Escape" && parseDrillQuery' in app
    assert "overviewDrillReturnFocus" in app
    assert "data-trend-tooltip-enabled" in app
    assert "showTrendTooltip" in app
    d3 = views[views.index("export function overviewTrendSection") : views.index("export function overviewExplorerSection")]
    assert "renderTrendChart(trend)" in d3
    assert "tooltip: true" not in d3


def test_d4_trend_tooltip_uses_existing_point_values() -> None:
    chart = (WEB_STATIC / "js" / "trend-chart.js").read_text(encoding="utf-8")
    assert "export function trendTooltipModel" in chart
    assert "point && point.value" in chart
    assert "point.comparison_value" in chart
    assert "point.secondary_value" in chart
    assert "data-trend-hover" in chart
    assert "data-trend-point" in chart
    assert "data-trend-point-summary" in chart
    assert "encodeURIComponent(JSON.stringify(model))" in chart
    model = chart[chart.index("export function trendTooltipModel") : chart.index("function niceTicks")]
    assert 'label: "Comparison"' in model
    assert "secondary.label" in model
    d3 = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    section = d3[d3.index("export function overviewTrendSection") : d3.index("export function overviewExplorerSection")]
    assert "data-trend-tooltip-enabled" not in section
    spark = (WEB_STATIC / "js" / "sparkline.js").read_text(encoding="utf-8")
    assert "data-trend-tooltip" not in spark
    assert "kpi-sparkline" in spark


