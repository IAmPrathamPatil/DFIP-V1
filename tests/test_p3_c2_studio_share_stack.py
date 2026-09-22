"""P3-C2 Analytics Studio 100% stacked visual: server bucket_share only."""

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


def _studio_share_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioShareStackedBarChart")
    end = views.index("function studioComboChart")
    return views[start:end]


def _studio_abs_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioStackedBarChart")
    end = views.index("function studioShareStackedBarChart")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_share_stack_reads_bucket_share_without_client_math() -> None:
    helper = _studio_share_helper()
    studio = _studio_view()
    css = _css()
    assert "studioShareStackedZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert 'data-studio-share-stack-zone="true"' in helper
    assert 'data-studio-share-stack-chart="true"' in helper
    assert "drillChartNumber(point && point.bucket_share)" in helper
    assert "part.share * innerH" in helper
    assert "point.value" not in helper
    assert "part.value" not in helper
    assert "/ yMax" not in helper
    assert "* 100" not in helper
    assert "contribution_pct" not in helper
    assert "100% stacked" in helper
    assert "var(--chart-1)" in helper
    assert ".studio-share-stack-zone" in css
    assert ".studio-share-stack-svg" in css
    share_css = css[css.index(".studio-share-stack-zone") : css.index(".studio-mix-zone")]
    assert "overflow-x: hidden" in share_css
    assert "max-width: 100%" in share_css


def test_studio_share_stack_adds_no_request() -> None:
    studio_branch = _studio_branch()
    assert studio_branch.count("getOverviewTrends") == 2
    assert studio_branch.count("getOverviewKpis") == 1
    assert studio_branch.count("getOverviewExplorer") == 1
    assert 'breakdown: "channel"' in studio_branch
    assert "getOverviewTrends(stackParams)" in studio_branch
    assert "Promise.allSettled" in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert "setInterval" not in studio_branch


def test_studio_absolute_stacked_bar_remains() -> None:
    studio = _studio_view()
    abs_helper = _studio_abs_helper()
    views = _js("views.js")
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    assert "studioStackedBarZone({ query, stack, stackError, loading: zoneLoading })" in studio
    assert 'data-studio-stacked-zone="true"' in abs_helper
    assert 'data-studio-stacked-chart="true"' in abs_helper
    assert "drillChartNumber(point && point.value)" in abs_helper
    assert "Absolute stacked values, not 100% share." in abs_helper
    assert "bucket_share" not in abs_helper
    assert "studioShareStackedBarChart" not in overview
    assert "data-studio-share-stack-zone" not in overview
    assert "studioTrendZone({ query, trend, trendError, loading: zoneLoading })" in studio
    assert "studioDistributionZone({ query, mix, mixError, loading: zoneLoading })" in studio
