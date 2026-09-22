"""P3-E1 Analytics Studio narrative: existing insights + anomalies payloads only."""

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


def _studio_narrative_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioNarrativeInsights")
    end = views.index("export function clientStudioView")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_narrative_renders_existing_insight_and_anomaly_copy() -> None:
    studio = _studio_view()
    helper = _studio_narrative_helper()
    css = _css()
    assert "studioNarrativeZone({ query, insights, insightsError, anomalies, anomaliesError, loading: zoneLoading })" in studio
    assert 'data-studio-zone="narrative"' in helper
    assert 'data-studio-narrative-zone="true"' in helper
    assert 'data-studio-narrative-insights="true"' in helper
    assert 'data-studio-narrative-anomalies="true"' in helper
    assert "item.headline" in helper
    assert "item.explanation" in helper
    assert "item.metric_label" in helper
    assert "item.current_value" in helper
    assert "item.prior_value" in helper
    assert "item.baseline_value" in helper
    assert "item.driver_label" in helper
    assert "item.affected_label" in helper
    assert "INSIGHT_CATEGORY_LABELS[item.category]" in helper
    assert "ANOMALY_KIND_LABELS[item.kind]" in helper
    assert "ANOMALY_SEVERITY_LABELS[item.severity]" in helper
    assert "formatKpiValue(kind, item.current_value)" in helper
    assert "formatKpiValue(kind, item.baseline_value)" in helper
    assert "insightEmptyCopy(reason)" in helper
    assert "anomalyEmptyCopy(reason)" in helper
    assert "overviewInsightsSection" not in helper
    assert "overviewAnomaliesSection" not in helper
    assert "withDrill(" not in helper
    assert "withFocus(" not in helper
    assert "askAboutButton(" not in helper
    assert "overviewHref(" not in helper
    assert "findingContribution(" not in helper
    assert "formatDelta(" not in helper
    assert "* 100" not in helper
    assert ".studio-narrative-zone" in css
    assert ".studio-narrative-frame" in css
    assert "grid-column: 1 / -1" in css[css.index(".studio-narrative-zone") : css.index(".studio-narrative-body")]


def test_studio_narrative_uses_existing_contracts_without_extra_engine() -> None:
    studio_branch = _studio_branch()
    state = _js("analytics-state.js")
    assert "insightsParamsFromQuery(query, publishedParams({}))" in studio_branch
    assert "anomaliesParamsFromQuery(query, publishedParams({}))" in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert studio_branch.count("getOverviewKpis") == 1
    assert studio_branch.count("getOverviewTrends") == 2
    assert studio_branch.count("getOverviewExplorer") == 1
    assert "Promise.allSettled" in studio_branch
    assert "getOverviewDrilldown" not in studio_branch
    assert "getOverviewKpiSparklines" not in studio_branch
    assert "downloadExplorerExport" not in studio_branch
    assert "setInterval" not in studio_branch
    assert "export function insightsParamsFromQuery" in state
    assert "export function anomaliesParamsFromQuery" in state
    assert 'next.grain = query.get("finding_grain") || "month"' in state


def test_studio_previous_visuals_remain_with_narrative() -> None:
    studio = _studio_view()
    helper = _studio_narrative_helper()
    views = _js("views.js")
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    assert "overviewKpiCardsHtml({ data, query, interactive: false })" in studio
    assert "studioTrendZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioDistributionZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioStackedBarZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert "studioShareStackedZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert "studioComboZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioDetailZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioNarrativeZone" not in overview
    assert "data-studio-narrative-zone" not in overview
    assert "overviewInsightsSection({ query, data, insights, insightsError })" in overview
    assert "overviewAnomaliesSection({ query, data, anomalies, anomaliesError })" in overview
    assert "askAboutButton(" in views
    assert "findingGrainSelector(query)" in views
    assert 'data-studio-insights-error="true"' in helper
    assert 'data-studio-anomalies-error="true"' in helper
    assert "studioZoneLoading(\"Loading narrative.\")" in helper
