"""P3-D1 Analytics Studio empty/loading/error states and accessibility."""

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


def _studio_helpers() -> str:
    views = _js("views.js")
    start = views.index("function studioZoneLoading")
    end = views.index("export function clientStudioView")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_loading_clears_stale_zone_content() -> None:
    studio = _studio_view()
    app = _js("app.js")
    helper = _studio_helpers()
    assert "function studioZoneLoading" in helper
    assert 'data-studio-loading="true"' in helper
    assert "clientStudioView({ session, query, loading: true })" in app
    assert 'aria-busy="${zoneLoading ? "true" : "false"}"' in studio
    assert "Loading Analytics Studio." in studio
    assert "studioZoneLoading(\"Loading KPI band.\")" in studio
    assert "overviewKpiCardsHtml({ data, query, interactive: false })" in studio
    assert "studioTrendZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioDistributionZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioStackedBarZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert "studioShareStackedZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert "studioComboZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioDetailZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioNarrativeZone({ query, insights, insightsError, anomalies, anomaliesError, loading: zoneLoading })" in studio
    assert "if (loading) {" in helper
    assert "studioZoneLoading(\"Loading trend.\")" in helper
    assert "studioZoneLoading(\"Loading distribution.\")" in helper
    assert "studioZoneLoading(\"Loading stacked mix.\")" in helper
    assert "studioZoneLoading(\"Loading share mix.\")" in helper
    assert "studioZoneLoading(\"Loading cost and revenue.\")" in helper
    assert "studioZoneLoading(\"Loading detail table.\")" in helper
    assert "studioZoneLoading(\"Loading narrative.\")" in helper


def test_studio_empty_and_error_states_are_per_zone() -> None:
    helper = _studio_helpers()
    studio = _studio_view()
    assert 'data-studio-kpi-empty="true"' in studio
    assert 'data-studio-kpi-error="true"' in studio
    assert 'data-studio-trend-empty="true"' in helper
    assert 'data-studio-trend-error="true"' in helper
    assert 'data-studio-mix-empty="true"' in helper
    assert 'data-studio-mix-error="true"' in helper
    assert 'data-studio-donut-empty="true"' in helper
    assert 'data-studio-stack-empty="true"' in helper
    assert 'data-studio-stack-error="true"' in helper
    assert 'data-studio-share-stack-empty="true"' in helper
    assert 'data-studio-share-stack-error="true"' in helper
    assert 'data-studio-combo-empty="true"' in helper
    assert 'data-studio-combo-error="true"' in helper
    assert 'data-studio-detail-empty="true"' in helper
    assert 'data-studio-detail-error="true"' in helper
    assert 'data-studio-insights-empty="${reason}"' in helper
    assert 'data-studio-insights-error="true"' in helper
    assert 'data-studio-anomalies-empty="${reason}"' in helper
    assert 'data-studio-anomalies-error="true"' in helper
    assert "No published history is available for this company." in studio
    assert "No published history matches this trend." in helper
    assert "No published history matches this mix." in helper
    assert "No published history matches this ranking." in helper
    assert "No published history matches this stacked mix." in helper
    assert "No bucket share is available for this composition." in helper
    assert "No published cost and revenue series match this combo." in helper
    assert "Contribution share is not available for this mix." in helper


def test_studio_accessibility_labels_and_table_semantics() -> None:
    studio = _studio_view()
    helper = _studio_helpers()
    css = _css()
    assert 'aria-labelledby="studio-kpi-title"' in studio
    assert 'aria-labelledby="studio-filters-title"' in studio
    assert 'role="region"' in studio
    assert 'aria-label="Studio visuals"' in studio
    assert 'aria-labelledby="studio-trend-title"' in helper
    assert 'aria-labelledby="studio-distribution-title"' in helper
    assert 'aria-labelledby="studio-stacked-title"' in helper
    assert 'aria-labelledby="studio-share-title"' in helper
    assert 'aria-labelledby="studio-combo-title"' in helper
    assert 'aria-labelledby="studio-detail-title"' in helper
    assert 'aria-labelledby="studio-narrative-title"' in helper
    assert "<caption class=\"sr-only\">" in helper
    assert 'scope="row"' in helper
    assert 'id="studio-detail-title"' in helper
    assert ".studio-zone-status" in css
    assert "var(--bg-3)" in css[css.index(".skeleton") : css.index("@keyframes shimmer")]


def test_studio_states_do_not_add_requests_or_polling() -> None:
    studio_branch = _studio_branch()
    app = _js("app.js")
    helper = _studio_helpers()
    assert studio_branch.count("getOverviewKpis") == 1
    assert studio_branch.count("getOverviewTrends") == 2
    assert studio_branch.count("getOverviewExplorer") == 1
    assert "Promise.allSettled" in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert "setInterval" not in studio_branch
    assert "downloadPublishedFacts" not in studio_branch
    assert "downloadExplorerExport" not in studio_branch
    assert "data = kpiResult.value" in studio_branch
    assert "kpiError = kpiResult.reason" in studio_branch
    assert "refreshOverviewInPlace" not in studio_branch
    assert "* 100" not in helper[helper.index("function studioZoneLoading") : helper.index("function studioTrendZone")]
    assert "clientStudioView({ session, query, loading: true })" in app
    overview = _js("views.js")
    overview_view = overview[overview.index("export function clientOverviewView") : overview.index("export function clientHomeView")]
    assert "studioZoneLoading" not in overview_view
    assert "data-studio-loading" not in overview_view
