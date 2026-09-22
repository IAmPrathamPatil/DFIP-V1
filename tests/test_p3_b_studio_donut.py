"""P3-B3 Analytics Studio donut/pie: same explorer contribution payload."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"


def _js(name: str) -> str:
    return (WEB_STATIC / "js" / name).read_text(encoding="utf-8")


def _css() -> str:
    return (WEB_STATIC / "css" / "app.css").read_text(encoding="utf-8")


def _studio_mix_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioDonutSlices")
    end = views.index("export function clientStudioView")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_donut_renders_from_explorer_shares() -> None:
    helper = _studio_mix_helper()
    css = _css()
    assert "function studioDonutChart" in helper
    assert 'data-studio-donut="true"' in helper
    assert "contributionSharePct(row && row.contribution_pct)" in helper
    assert "formatKpiValue(kind, slice.value)" in helper
    assert "studioDonutSlices(chartRows)" in helper
    assert "chart.js" not in helper.lower()
    assert "d3." not in helper
    assert 'data-studio-mix-chart="true"' in helper
    assert "drillChartRows(rows)" in helper
    assert ".studio-donut" in css
    assert ".studio-donut-legend" in css
    assert "var(--chart-1)" in helper


def test_studio_donut_reuses_shared_filters_without_extra_request() -> None:
    app = _js("app.js")
    studio_branch = _studio_branch()
    assert "getOverviewExplorer(params)" in studio_branch
    assert studio_branch.count("getOverviewExplorer") == 1
    assert "studioParamsFromQuery(query, publishedParams({}))" in studio_branch
    assert "explorerParamsFromQuery" not in studio_branch
    assert "queryFromStudioForm(form)" in app
    assert "Promise.allSettled" in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1


def test_studio_donut_does_not_change_overview_or_mix_contract() -> None:
    views = _js("views.js")
    app = _js("app.js")
    helper = _studio_mix_helper()
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    trend = views[views.index("function overviewTrendSection") : views.index("const DRILL_DIM_LABELS")]
    assert "studioDonutChart" not in overview
    assert "data-studio-donut" not in overview
    assert "data-studio-donut" not in trend
    assert 'data-studio-mix-chart="true"' in helper
    assert "overviewExplorerSection({ query, data, explorer, explorerError })" in overview
    assert "${renderTrendChart(trend)}" in trend
    assert "refreshOverviewInPlace" in app
    assert "downloadExplorerExport" in app
