"""P3-C4A Analytics Studio detail table: existing explorer mix rows only."""

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


def _studio_detail_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioDetailZone")
    end = views.index("export function clientStudioView")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_detail_table_renders_existing_mix_rows() -> None:
    studio = _studio_view()
    helper = _studio_detail_helper()
    css = _css()
    assert "studioDetailZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert 'data-studio-zone="detail"' in helper
    assert 'data-studio-detail-zone="true"' in helper
    assert 'data-studio-detail-table="true"' in helper
    assert "Array.isArray(mix && mix.rows) ? mix.rows : []" in helper
    assert "drillChartRows(" not in helper
    assert "rows.map((row)" in helper
    assert "row.rank" in helper
    assert "row.label" in helper
    assert "formatKpiValue(kind, row.value)" in helper
    assert "formatKpiValue(kind, row.prior_value)" in helper
    assert "formatKpiValue(kind, row.delta)" in helper
    assert "contributionSharePct(row.contribution_pct)" in helper
    assert "formatDelta(" not in helper
    assert "* 100" not in helper
    assert "overall_roas" not in helper
    assert "data-explorer-drill" not in helper
    assert "data-explorer-sort" not in helper
    assert "data-drill-next" not in helper
    assert "overviewHref(" not in helper
    assert ".studio-detail-zone" in css
    assert ".studio-detail-table" in css
    assert ".studio-detail-wrap" in css
    assert "position: sticky" in css[css.index(".explorer-table thead th") : css.index(".explorer-table .num")]


def test_studio_detail_adds_no_request() -> None:
    studio_branch = _studio_branch()
    assert studio_branch.count("getOverviewKpis") == 1
    assert studio_branch.count("getOverviewTrends") == 2
    assert studio_branch.count("getOverviewExplorer") == 1
    assert "Promise.allSettled" in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert "getOverviewDrilldown" not in studio_branch
    assert "downloadExplorerExport" not in studio_branch
    assert "setInterval" not in studio_branch


def test_studio_previous_visuals_remain_with_detail_table() -> None:
    studio = _studio_view()
    views = _js("views.js")
    helper = _studio_detail_helper()
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    assert "overviewKpiCardsHtml({ data, query, interactive: false })" in studio
    assert "studioTrendZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioDistributionZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioStackedBarZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert "studioShareStackedZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert "studioComboZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioNarrativeZone({ query, insights, insightsError, anomalies, anomaliesError, loading: zoneLoading })" in studio
    assert "studioDetailZone" not in overview
    assert "data-studio-detail-table" not in overview
    assert "overviewExplorerSection({ query, data, explorer, explorerError })" in overview
    assert "data-explorer-table" not in helper
    assert "rows.sort" not in helper
    assert ".slice(" not in helper
