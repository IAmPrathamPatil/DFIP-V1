"""P3-C1 Analytics Studio stacked bar: existing trends breakdown contract."""

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


def _studio_stack_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioStackedBarChart")
    end = views.index("function studioShareStackedBarChart")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_stacked_bar_renders() -> None:
    studio = _studio_view()
    helper = _studio_stack_helper()
    css = _css()
    assert "studioStackedBarZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert 'data-studio-stacked-zone="true"' in helper
    assert 'data-studio-stacked-chart="true"' in helper
    assert "studioStackedBarChart(stack)" in helper
    assert "drillChartNumber(point && point.value)" in helper
    assert "formatKpiValue(kind, part.value)" in helper
    assert "var(--chart-1)" in helper
    assert "Absolute stacked values, not 100% share." in helper
    assert "renderTrendChart" not in helper
    assert "<table" not in helper
    assert "chart.js" not in helper.lower()
    assert "d3." not in helper
    assert ".studio-stack-zone" in css
    assert ".studio-stack-svg" in css
    assert "--chart-1:" in css
    assert 'html[data-theme="light"]' in css
    stack_css = css[css.index(".studio-stack-zone") : css.index(".studio-mix-body")]
    assert "overflow-x: hidden" in stack_css
    assert "max-width: 100%" in stack_css
    assert "min-width: 0" in stack_css


def test_studio_stacked_uses_existing_trends_breakdown_and_shared_filters() -> None:
    app = _js("app.js")
    state = _js("analytics-state.js")
    client = _js("api-client.js")
    studio_branch = _studio_branch()
    assert 'this.request("GET", `${this.prefix}/analytics/trends`' in client
    assert "studioParamsFromQuery(query, publishedParams({}))" in studio_branch
    assert "const stackParams = { ...params, breakdown: \"channel\" }" in studio_branch
    assert "getOverviewTrends(trendParams)" in studio_branch
    assert "getOverviewTrends(stackParams)" in studio_branch
    assert studio_branch.index("studioParamsFromQuery") < studio_branch.index("getOverviewTrends(stackParams)")
    assert "export function studioParamsFromQuery" in state
    assert "filterParamsFromQuery" in state
    assert "queryFromStudioForm(form)" in app
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


def test_studio_stacked_request_count_is_bounded() -> None:
    studio_branch = _studio_branch()
    assert studio_branch.count("getOverviewKpis") == 1
    assert studio_branch.count("getOverviewTrends") == 2
    assert studio_branch.count("getOverviewExplorer") == 1
    assert "Promise.allSettled" in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert "getOverviewDrilldown" not in studio_branch
    assert "getOverviewKpiSparklines" not in studio_branch
    assert "setInterval" not in studio_branch
    assert "downloadExplorerExport" not in studio_branch


def test_studio_existing_p3a_p3b_visuals_remain() -> None:
    studio = _studio_view()
    views = _js("views.js")
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    trend = views[views.index("function overviewTrendSection") : views.index("const DRILL_DIM_LABELS")]
    helper = _studio_stack_helper()
    assert "overviewKpiCardsHtml({ data, query, interactive: false })" in studio
    assert "studioTrendZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioDistributionZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert 'data-studio-donut="true"' in views
    assert 'data-studio-mix-chart="true"' in views
    assert "studioDetailZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioNarrativeZone({ query, insights, insightsError, anomalies, anomaliesError, loading: zoneLoading })" in studio
    assert "overviewExplorerSection" not in studio
    assert "overviewTrendSection" not in studio
    assert "studioStackedBarChart" not in overview
    assert "data-studio-stacked-zone" not in overview
    assert "data-studio-stacked-chart" not in trend
    assert "overviewExplorerSection({ query, data, explorer, explorerError })" in overview
    assert "${renderTrendChart(trend)}" in trend
    assert "contribution_pct" not in helper
    assert "* 100" not in helper
    assert "include_metric" not in _studio_branch()
