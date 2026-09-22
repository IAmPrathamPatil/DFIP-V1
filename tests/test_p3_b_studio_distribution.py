"""P3-B2 Analytics Studio distribution/mix: existing explorer aggregate."""

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


def _studio_mix_helper() -> str:
    views = _js("views.js")
    start = views.index("function studioDistributionZone")
    end = views.index("export function clientStudioView")
    return views[start:end]


def _studio_branch() -> str:
    app = _js("app.js")
    return app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]


def test_studio_distribution_zone_renders() -> None:
    studio = _studio_view()
    helper = _studio_mix_helper()
    css = _css()
    assert "studioDistributionZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert 'data-studio-zone="distribution"' in helper
    assert 'data-studio-mix-zone="true"' in helper
    assert 'data-studio-mix-chart="true"' in helper
    assert "drillChartRows(rows)" in helper
    assert "contributionSharePct(row.contribution_pct)" in helper
    assert "formatKpiValue(kind, row.value)" in helper
    assert "overviewExplorerSection" not in studio
    assert "data-overview-explorer-form" not in helper
    assert "data-explorer-table" not in helper
    assert "data-explorer-drill" not in helper
    assert "data-drill-next" not in helper
    assert ".studio-mix-zone" in css
    assert ".studio-mix-body" in css


def test_studio_distribution_uses_explorer_contract_and_shared_filters() -> None:
    app = _js("app.js")
    state = _js("analytics-state.js")
    client = _js("api-client.js")
    studio_branch = _studio_branch()
    assert 'this.request("GET", `${this.prefix}/analytics/explorer`' in client
    assert "getOverviewExplorer(params)" in studio_branch
    assert "studioParamsFromQuery(query, publishedParams({}))" in studio_branch
    assert "explorerParamsFromQuery" not in studio_branch
    assert studio_branch.index("studioParamsFromQuery") < studio_branch.index("getOverviewExplorer(params)")
    assert "queryFromStudioForm(form)" in app
    assert "export function studioParamsFromQuery" in state
    assert "filterParamsFromQuery" in state
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


def test_studio_makes_one_distribution_request() -> None:
    studio_branch = _studio_branch()
    assert studio_branch.count("getOverviewExplorer") == 1
    assert studio_branch.count("getOverviewKpis") == 1
    assert studio_branch.count("getOverviewTrends") == 2
    assert "Promise.allSettled" in studio_branch
    assert "downloadExplorerExport" not in studio_branch
    assert studio_branch.count("getOverviewInsights") == 1
    assert studio_branch.count("getOverviewAnomalies") == 1
    assert "setInterval" not in studio_branch


def test_overview_explorer_behavior_is_unchanged() -> None:
    views = _js("views.js")
    app = _js("app.js")
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    section = views[views.index("function overviewExplorerSection") : views.index("const INSIGHT_CATEGORY_LABELS")]
    assert "overviewExplorerSection({ query, data, explorer, explorerError })" in overview
    assert "data-overview-explorer-form" in section
    assert "data-explorer-table" in section
    assert "data-explorer-drill" in section
    assert "queryFromExplorerForm" in app
    assert "explorerParamsFromQuery(query, publishedParams({}))" in app
    assert "downloadExplorerExport" in app
    assert "refreshOverviewInPlace" in app
