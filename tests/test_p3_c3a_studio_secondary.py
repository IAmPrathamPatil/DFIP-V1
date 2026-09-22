"""P3-C3a Studio trends: request existing secondary metric on the primary trends call."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"


def _js(name: str) -> str:
    return (WEB_STATIC / "js" / name).read_text(encoding="utf-8")


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def _studio_trend_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioTrendZone")
    end = views.index("function studioDonutSlices")
    return views[start:end]


def test_studio_requests_supported_secondary_on_existing_trends_call() -> None:
    studio_branch = _studio_branch()
    assert 'const trendParams = { ...params, secondary: "revenue_inr" }' in studio_branch
    assert "getOverviewTrends(trendParams)" in studio_branch
    assert "studioParamsFromQuery(query, publishedParams({}))" in studio_branch
    assert studio_branch.index("studioParamsFromQuery") < studio_branch.index("getOverviewTrends(trendParams)")
    assert 'const stackParams = { ...params, breakdown: "channel" }' in studio_branch


def test_studio_secondary_does_not_add_a_request_or_js_math() -> None:
    studio_branch = _studio_branch()
    helper = _studio_trend_helper()
    assert studio_branch.count("getOverviewKpis") == 1
    assert studio_branch.count("getOverviewTrends") == 2
    assert studio_branch.count("getOverviewExplorer") == 1
    assert "Promise.allSettled" in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert "setInterval" not in studio_branch
    assert "secondary_value /" not in helper
    assert "point.value /" not in helper
    assert "* 100" not in helper


def test_studio_primary_trend_renderer_ignores_secondary() -> None:
    helper = _studio_trend_helper()
    views = _js("views.js")
    overview = views[views.index("function overviewTrendSection") : views.index("const DRILL_DIM_LABELS")]
    assert "renderTrendChart(primaryOnly, { interactive: false, tooltip: true })" in helper
    assert "secondary: null" in helper
    assert "dual_axis: false" in helper
    assert "${renderTrendChart(trend)}" in overview
    assert "primaryOnly" not in overview
    assert 'data-studio-stacked-zone="true"' in views
    assert 'data-studio-share-stack-zone="true"' in views
    assert "studioDistributionZone({ query, mix, mixError, loading" in views
