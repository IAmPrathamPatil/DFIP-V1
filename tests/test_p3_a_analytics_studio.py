"""P3-A2 Analytics Studio shell: route, nav, shared D2 filters, reserved zones."""

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


def test_studio_route_renders_instead_of_not_found() -> None:
    app = _js("app.js")
    views = _js("views.js")
    assert '{ pattern: /^\\/client\\/studio$/, name: "client-studio", access: "client" }' in app
    assert app.count('name: "client-studio"') == 1
    assert 'if (name === "client-studio")' in app
    assert "clientStudioView(" in app
    assert "export function clientStudioView" in views
    studio_branch = app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]
    assert "notFoundView()" not in studio_branch
    assert "getOverviewKpis" in studio_branch
    assert studio_branch.count("getOverviewExplorer") == 1
    assert studio_branch.count("getOverviewTrends") == 2
    assert studio_branch.count("getOverviewKpis") == 1


def test_studio_nav_item_exists_and_overview_nav_is_unchanged() -> None:
    components = _js("components.js")
    assert 'navItem("/client/overview", "Overview"' in components
    assert 'navItem("/client/studio", "Analytics Studio"' in components
    assert components.count('navItem("/client/studio", "Analytics Studio"') == 1
    assert 'if (path === "/client/studio") return "Analytics Studio"' in components
    assert 'navItem("/client", "Reports"' in components
    assert 'navItem("/client/facts", "Published data"' in components


def test_studio_reuses_shared_d2_filter_state() -> None:
    state = _js("analytics-state.js")
    app = _js("app.js")
    studio = _studio_view()
    for key in (
        "period",
        "month_start",
        "day_from",
        "day_to",
        "compare",
        "compare_month_start",
        "campaign_id",
        "channel",
        "filter_logic_1",
        "filter_logic_1_group",
    ):
        assert f'"{key}"' in state
    assert "export function queryFromStudioForm" in state
    assert "export function studioHref" in state
    assert "export function studioParamsFromQuery" in state
    assert "filterParamsFromQuery" in state
    assert "queryFromStudioForm(form)" in app
    assert "studioHref(queryFromStudioForm" in app
    assert "studioParamsFromQuery(query, publishedParams({}))" in app
    assert "overviewFilterBar(data, query, { clearHref: studioHref() })" in studio
    views = _js("views.js")
    assert 'data-overview-filters="true"' in views
    assert 'data-studio-filter-bar="true"' in studio
    assert "/analytics/explorer" not in studio
    assert "overviewExplorerSection" not in studio
    assert "overviewTrendSection" not in studio
    assert "queryFromExplorerForm" not in studio


def test_studio_shell_has_filter_rail_and_reserved_zones() -> None:
    studio = _studio_view()
    css = _css()
    assert 'data-studio-shell="true"' in studio
    assert 'data-studio-header="true"' in studio
    assert 'data-studio-filter-rail="true"' in studio
    assert 'data-studio-canvas="true"' in studio
    assert 'data-studio-kpi-zone="true"' in studio
    views = _js("views.js")
    helper = views[views.index("function studioReservedZone") : views.index("export function clientStudioView")]
    assert 'data-studio-zone="${zone}"' in helper
    assert 'data-studio-zone="trend"' in helper
    assert 'data-studio-zone="distribution"' in helper
    assert 'data-studio-zone="detail"' in helper
    assert "studioDetailZone({ query, mix, mixError, loading: zoneLoading })" in studio
    assert "studioNarrativeZone({ query, insights, insightsError, anomalies, anomaliesError, loading: zoneLoading })" in studio
    assert "overviewKpiCardsHtml({ data, query, interactive: false })" in studio
    assert "data-overview-explorer" not in studio
    assert ".studio-workspace" in css
    assert ".studio-filter-rail" in css
    assert ".studio-kpi-zone" in css
    assert "data-studio-shell" in css
    assert "overflow-x: hidden" in css[css.index(".studio-workspace") : css.index(".studio-header")]


def test_overview_workspace_is_not_replaced_by_studio() -> None:
    views = _js("views.js")
    app = _js("app.js")
    components = _js("components.js")
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    assert 'data-overview-shell="true"' in overview
    assert "overviewKpiCardsHtml" in overview
    assert "overviewExplorerSection" in overview
    assert 'name: "client-overview"' in app
    assert "refreshOverviewInPlace" in app
    assert 'navItem("/client/overview", "Overview"' in components
    assert "getOverviewExplorer" in app
    assert "downloadExplorerExport" in app
    assert 'overviewKpiCardsHtml({ data, query, sparkline })' in overview
    assert "interactive: false" not in overview


def test_studio_renders_canonical_overview_kpi_band() -> None:
    studio = _studio_view()
    views = _js("views.js")
    components = _js("components.js")
    app = _js("app.js")
    paint = views[views.index("export function overviewKpiCardsHtml") : views.index("export function overviewSectionRetry")]
    card = components[components.index("export function overviewKpiCard") : components.index("export function metricCard")]
    studio_branch = app[app.index('if (name === "client-studio")') : app.index('if (name === "client-overview")')]
    for kpi_id in (
        "total_cost",
        "revenue_inr",
        "overall_roas",
        "delivered",
        "unique_clicks",
        "unique_conversions",
        "delivery_rate",
        "ctr_del_to_clicks",
    ):
        assert kpi_id in views
    assert "data.kpis" in paint
    assert "kpis.map((kpi)" in paint
    assert "formatKpiValue(kpi.kind, kpi.value)" in paint
    assert "kpi.delta" in paint
    assert "kpi.prior_value" in paint
    assert "kpi.definition" in paint
    assert "interactive: false" in studio
    assert 'data-studio-kpis-host="true"' in studio
    assert 'data-studio-kpi="${id || ""}"' in card
    assert "<article" in card
    assert "studioParamsFromQuery(query, publishedParams({}))" in studio_branch
    assert "getOverviewKpis(params)" in studio_branch
    assert studio_branch.count("getOverviewKpis") == 1
    assert "getOverviewKpiSparklines" not in studio_branch
    assert "queryFromStudioForm(form)" in app
    assert "studioHref(queryFromStudioForm" in app


def test_studio_kpi_cards_do_not_open_overview_drill() -> None:
    studio = _studio_view()
    views = _js("views.js")
    components = _js("components.js")
    app = _js("app.js")
    card = components[components.index("export function overviewKpiCard") : components.index("export function metricCard")]
    paint = views[views.index("export function overviewKpiCardsHtml") : views.index("export function overviewSectionRetry")]
    overview = views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")]
    assert "interactive: false" in studio
    assert "withDrill(" not in studio
    assert "withFocus(" not in studio
    assert "data-overview-drill-kpi" not in studio
    assert 'href="${detailsHref || "#"}"' not in studio
    assert "if (!linked)" in card
    assert 'data-overview-drill-kpi="${id || ""}"' in card
    assert "withDrill(withFocus(" in paint
    assert 'origin: "kpi"' in paint
    assert 'overviewKpiCardsHtml({ data, query, sparkline })' in overview
    assert 'a[data-overview-drill-kpi].overview-kpi' in app
    assert "refreshOverviewInPlace" in app
