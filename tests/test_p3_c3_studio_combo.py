"""P3-C3 Analytics Studio combo bar + line: existing value and secondary_value."""

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


def _studio_combo_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioComboChart")
    end = views.index("export function clientStudioView")
    return views[start:end]


def _studio_trend_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioTrendZone")
    end = views.index("function studioDonutSlices")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_combo_reads_value_and_secondary_value() -> None:
    studio = _studio_view()
    helper = _studio_combo_helper()
    css = _css()
    assert "studioComboZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert 'data-studio-combo-zone="true"' in helper
    assert 'data-studio-combo-chart="true"' in helper
    assert "drillChartNumber(point && point.value)" in helper
    assert "drillChartNumber(point && point.secondary_value)" in helper
    assert "studio-combo-bar" in helper
    assert "studio-combo-line" in helper
    assert "point.value /" not in helper
    assert "secondary_value /" not in helper
    assert "* 100" not in helper
    assert "overall_roas" not in helper
    assert "var(--chart-1)" in helper
    assert "var(--chart-2)" in helper
    assert ".studio-combo-zone" in css
    assert ".studio-combo-svg" in css
    combo_css = css[css.index(".studio-combo-zone") : css.index(".studio-mix-zone")]
    assert "overflow-x: hidden" in combo_css
    assert "max-width: 100%" in combo_css


def test_studio_combo_adds_no_request() -> None:
    studio_branch = _studio_branch()
    assert studio_branch.count("getOverviewKpis") == 1
    assert studio_branch.count("getOverviewTrends") == 2
    assert studio_branch.count("getOverviewExplorer") == 1
    assert 'secondary: "revenue_inr"' in studio_branch
    assert "getOverviewTrends(trendParams)" in studio_branch
    assert "Promise.allSettled" in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert "setInterval" not in studio_branch


def test_studio_line_trend_and_other_visuals_remain() -> None:
    studio = _studio_view()
    trend_helper = _studio_trend_helper()
    views = _js("views.js")
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    assert "renderTrendChart(primaryOnly, { interactive: false, tooltip: true })" in trend_helper
    assert "studioTrendZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioStackedBarZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert "studioShareStackedZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert "studioDistributionZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioDetailZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioNarrativeZone({ query, insights, insightsError, anomalies, anomaliesError, loading: zoneLoading })" in studio
    assert "studioComboChart" not in overview
    assert "data-studio-combo-zone" not in overview
    assert "${renderTrendChart(trend)}" in views[views.index("function overviewTrendSection") : views.index("const DRILL_DIM_LABELS")]
