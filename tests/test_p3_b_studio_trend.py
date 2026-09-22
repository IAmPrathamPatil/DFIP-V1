"""P3-B1 Analytics Studio trend chart: existing trends contract, one request."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"


def _js(name: str) -> str:
    return (WEB_STATIC / "js" / name).read_text(encoding="utf-8")


def _css() -> str:
    return (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")


def _studio_view() -> str:
    views = _js("views.js")
    start = views.index("export function clientStudioView")
    end = views.index("export function clientFactListView")
    return views[start:end]


def _studio_trend_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioTrendZone")
    end = views.index("export function clientStudioView")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_trend_zone_renders_existing_chart() -> None:
    studio = _studio_view()
    helper = _studio_trend_helper()
    css = _css()
    assert 'data-studio-zone="trend"' in helper
    assert 'data-studio-trend-zone="true"' in helper
    assert "studioTrendZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "renderTrendChart(primaryOnly, { interactive: false, tooltip: true })" in helper
    assert 'data-trend-chart="true"' in _js("trend-chart.js")
    assert "overviewTrendSection" not in studio
    assert "data-overview-trend-form" not in helper
    assert "data-trend-explorer" not in helper
    assert "generateTrendControl" not in helper
    assert ".studio-trend-zone" in css
    assert ".studio-trend-body" in css
    assert "TREND_GRAIN_OPTIONS.find" in helper
    assert "selection.grain" in helper


def test_studio_trend_uses_existing_contract_and_shared_filters() -> None:
    app = _js("app.js")
    state = _js("analytics-state.js")
    client = _js("api-client.js")
    studio_branch = _studio_branch()
    assert 'this.request("GET", `${this.prefix}/analytics/trends`' in client
    assert "getOverviewTrends(trendParams)" in studio_branch
    assert "studioParamsFromQuery(query, publishedParams({}))" in studio_branch
    assert "export function studioParamsFromQuery" in state
    assert "filterParamsFromQuery" in state
    assert "queryFromStudioForm(form)" in app
    assert studio_branch.index("studioParamsFromQuery") < studio_branch.index("getOverviewTrends(trendParams)")
    assert studio_branch.index("getOverviewKpis(params)") < studio_branch.index("getOverviewTrends(trendParams)") or (
        "Promise.allSettled" in studio_branch
    )
    for key in (
        "period",
        "month_start",
        "day_from",
        "day_to",
        "compare",
        "campaign_id",
        "channel",
        "filter_logic_1",
        "filter_logic_1_group",
    ):
        assert f'"{key}"' in state


def test_studio_makes_one_trend_request_without_explorer() -> None:
    studio_branch = _studio_branch()
    helper = _studio_trend_helper()
    assert studio_branch.count("getOverviewTrends") == 2
    assert "getOverviewTrends(trendParams)" in studio_branch
    assert 'breakdown: "channel"' in studio_branch
    assert studio_branch.count("getOverviewKpis") == 1
    assert "Promise.allSettled" in studio_branch
    assert "getOverviewKpiSparklines" not in studio_branch
    assert studio_branch.count("getOverviewExplorer") == 1
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert "setInterval" not in studio_branch
    assert "interactive: false" in helper
    assert "isStudioRoute()" in _js("app.js")


def test_overview_trend_behavior_is_unchanged() -> None:
    views = _js("views.js")
    app = _js("app.js")
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    trend_section = views[views.index("function overviewTrendSection") : views.index("const DRILL_DIM_LABELS")]
    assert "overviewTrendSection({ query, trend, trendError })" in overview
    assert "${renderTrendChart(trend)}" in trend_section
    assert "data-overview-trend-form" in trend_section
    assert "data-trend-explorer" in trend_section
    assert "queryFromTrendForm" in app
    assert "refreshOverviewInPlace" in app
    assert "openTrendDrill" in app
    assert 'if (needTrends) jobs.push(["trends", api.getOverviewTrends(params)]);' in app
    assert "interactive: false" not in trend_section
