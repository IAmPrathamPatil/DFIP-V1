"""D4 KPI/chart drilldown: context, ranking, isolation, and D1–D3 regression."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from dfip_analytics.drill import (
    MAX_DRILL_DEPTH,
    apply_parents,
    next_dimensions,
    parse_drill_dimension,
    parse_parents,
    resolve_slice_windows,
    validate_drill_selection,
)
from dfip_analytics.filters import DimensionFilters, FilterValidationError, month_window
from dfip_analytics.kpis import compute_kpis
from dfip_analytics.trends import TREND_METRICS_BY_KEY
from dfip_api.errors import PersistenceUnavailableError
from dfip_api.publication_store import InMemoryPublicationStore

from test_d1_kpi_overview import CLIENT_B, OCT, OVERVIEW, _kpi_map, _publish
from test_d2_global_filters import _store as d2_store
from test_d3_dynamic_trends import TRENDS, _d3_store, _fact, _series
from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers

DRILL = "/api/v1/analytics/drilldown"


def _get(store, params=None, *, role: str = "client", client_id: str = CLIENT_ID):
    http, *_rest = jwt_app(publication_store=store)
    return http.get(DRILL, headers=jwt_headers(role, client_id), params=params or {})


def _row(body: dict, key: str) -> dict:
    return next(item for item in body["rows"] if item["key"] == key)


def test_kpi_opens_campaign_drill() -> None:
    body = _get(_d3_store(), {"origin": "kpi", "metric": "total_cost", "dimension": "campaign_id"}).json()
    assert body["selection"]["origin"] == "kpi"
    assert body["selection"]["dimension"] == "campaign_id"
    assert body["selection"]["max_depth"] == MAX_DRILL_DEPTH
    assert body["selection"]["can_go_deeper"] is True
    assert {item["key"] for item in body["rows"]} == {"camp-a", "camp-b"}
    assert _row(body, "camp-a")["value"] == "30.0000"
    assert _row(body, "camp-a")["drillable"] is True


def test_valid_dimension_and_rejections() -> None:
    store = _d3_store()
    http, *_rest = jwt_app(publication_store=store)
    headers = jwt_headers("client")
    channel = http.get(DRILL, headers=headers, params={"dimension": "channel"}).json()
    assert {item["key"] for item in channel["rows"]} == {"WhatsApp", "SMS"}
    invalid_dim = http.get(DRILL, headers=headers, params={"dimension": "brand"})
    assert invalid_dim.status_code == 422
    invalid_metric = http.get(DRILL, headers=headers, params={"metric": "orders", "dimension": "campaign_id"})
    assert invalid_metric.status_code == 422
    invalid_combo = http.get(
        DRILL,
        headers=headers,
        params={"dimension": "campaign_id", "parent": "campaign_id:camp-a"},
    )
    assert invalid_combo.status_code == 422
    invalid_parent = http.get(
        DRILL,
        headers=headers,
        params={"dimension": "channel", "parent": "campaign_id:does-not-exist"},
    )
    assert invalid_parent.status_code == 422


def test_inherited_period_filters_and_comparison() -> None:
    store = _d3_store()
    june = _get(store, {"month_start": "2025-06-01", "dimension": "campaign_id"}).json()
    assert june["applied"]["month_start"] == "2025-06-01"
    assert _row(june, "camp-a")["value"] == "10.0000"
    filtered = _get(
        store,
        {"dimension": "campaign_id", "channel": "SMS", "compare": "none"},
    ).json()
    assert filtered["applied"]["channels"] == ["SMS"]
    assert filtered["comparison"]["available"] is False
    assert filtered["rows"][0]["value"] == "30.0000"
    compared = _get(store, {"dimension": "campaign_id"}).json()
    assert compared["comparison"]["available"] is True
    camp_a = _row(compared, "camp-a")
    assert camp_a["prior_value"] == "10.0000"
    assert camp_a["delta"] == "20.0000"
    camp_b = _row(compared, "camp-b")
    assert camp_b["prior_value"] is None
    assert camp_b["delta"] is None


def test_nested_drill_and_depth_cap() -> None:
    store = _d3_store()
    nested = _get(
        store,
        {
            "metric": "total_cost",
            "dimension": "channel",
            "parent": ["campaign_id:camp-a"],
        },
    ).json()
    assert nested["selection"]["depth"] == 2
    assert nested["selection"]["parents"][0]["value"] == "camp-a"
    assert nested["applied"]["is_default"] is True
    assert nested["rows"][0]["key"] == "WhatsApp"
    assert nested["rows"][0]["value"] == "30.0000"
    too_deep = _get(
        store,
        {
            "dimension": "day",
            "parent": ["campaign_id:camp-a", "channel:WhatsApp", "filter_logic_1:Group A"],
        },
    )
    assert too_deep.status_code == 422


def test_ratio_delta_and_contribution() -> None:
    store = _d3_store()
    body = _get(store, {"metric": "delivery_rate", "dimension": "campaign_id"}).json()
    camp_a = _row(body, "camp-a")
    expected = compute_kpis(
        {
            "sent": Decimal(110),
            "delivered": Decimal(90),
            "unique_clicks": Decimal(12),
            "unique_conversions": Decimal(4),
            "total_cost": Decimal("30.0000"),
            "revenue_inr": Decimal("60.0000"),
        },
        namespace="client",
    )
    assert camp_a["value"] == format(expected["delivery_rate"], "f")
    assert camp_a["numerator"] == 90
    assert camp_a["denominator"] == 110
    cost = _get(store, {"metric": "total_cost", "dimension": "campaign_id"}).json()
    assert Decimal(_row(cost, "camp-a")["contribution_pct"]) == Decimal("0.500000")
    assert Decimal(_row(cost, "camp-b")["contribution_pct"]) == Decimal("0.500000")
    assert _row(cost, "camp-a")["delta"] == "20.0000"


def test_empty_error_unauthorized_isolation_newest_wins() -> None:
    empty = _get(
        _d3_store(),
        {"period": "range", "day_from": "2025-07-01", "day_to": "2025-07-31", "dimension": "campaign_id"},
    ).json()
    assert empty["empty"] is True
    assert empty["rows"] == []

    class BoomStore(InMemoryPublicationStore):
        def list_published_history_groups(self, *args, **kwargs):
            raise PersistenceUnavailableError("drill store failed")

    store = BoomStore()
    from test_d1_kpi_overview import _fact as d1_fact

    _publish(
        store,
        CLIENT_ID,
        [d1_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT)],
        "run-1",
    )
    assert _get(store).status_code == 503

    http, *_rest = jwt_app(publication_store=_d3_store())
    assert http.get(DRILL).status_code == 401
    denied = http.get(
        DRILL,
        headers=jwt_headers("client", CLIENT_ID),
        params={"client_id": CLIENT_B, "dimension": "campaign_id"},
    )
    assert denied.status_code == 403
    unbound = http.get(DRILL, headers=jwt_headers("publisher", None), params={"dimension": "campaign_id"})
    assert unbound.status_code == 403
    publisher = http.get(
        DRILL,
        headers=jwt_headers("publisher", CLIENT_ID),
        params={"dimension": "campaign_id"},
    )
    assert publisher.status_code == 200
    a_body = http.get(DRILL, headers=jwt_headers("client", CLIENT_ID), params={"dimension": "campaign_id"}).json()
    b_body = http.get(DRILL, headers=jwt_headers("client", CLIENT_B), params={"dimension": "campaign_id"}).json()
    assert "999.0000" not in str(a_body)
    assert _row(b_body, "camp-a")["value"] == "999.0000"

    wins = InMemoryPublicationStore()
    _publish(wins, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="10.0000")], "old")
    _publish(wins, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="40.0000")], "new")
    assert _get(wins, {"dimension": "campaign_id"}).json()["rows"][0]["value"] == "40.0000"


def test_trend_slice_and_d1_d2_d3_regression() -> None:
    store = _d3_store()
    sliced = _get(
        store,
        {
            "origin": "trend",
            "metric": "total_cost",
            "dimension": "campaign_id",
            "slice_grain": "day",
            "slice_bucket": "2025-10-08",
        },
    ).json()
    assert sliced["selection"]["origin"] == "trend"
    assert sliced["period"]["day_min"] == "2025-10-08"
    assert sliced["period"]["day_max"] == "2025-10-08"
    assert sliced["applied"]["month_start"] == "2025-10-01"
    assert sliced["rows"][0]["key"] == "camp-b"
    assert sliced["rows"][0]["value"] == "30.0000"

    d2 = d2_store()
    http, *_rest = jwt_app(publication_store=d2)
    headers = jwt_headers("client")
    overview = http.get(OVERVIEW, headers=headers).json()
    assert [item["id"] for item in overview["kpis"]] == list(TREND_METRICS_BY_KEY)
    assert _kpi_map(overview)["total_cost"]["value"] == "60.0000"
    trend = http.get(TRENDS, headers=headers, params={"grain": "month"}).json()
    assert _series(trend)["points"][0]["value"] == "60.0000"
    drill = http.get(DRILL, headers=headers, params={"dimension": "channel"}).json()
    assert drill["applied"]["is_default"] is True
    assert {item["key"] for item in drill["rows"]} == {"WhatsApp", "SMS"}
    sms = http.get(DRILL, headers=headers, params={"dimension": "campaign_id", "channel": "SMS"}).json()
    assert sms["applied"]["channels"] == ["SMS"]
    assert sms["rows"][0]["value"] == "20.0000"


def test_drill_helpers_preserve_path_and_slice() -> None:
    metric = TREND_METRICS_BY_KEY["total_cost"]
    campaign = parse_drill_dimension("campaign_id")
    channel = parse_drill_dimension("channel")
    parents = parse_parents(["campaign_id:camp-a"])
    validate_drill_selection(metric, channel, parents)
    assert next_dimensions(parents, channel)[0] == "filter_logic_1"
    with pytest.raises(FilterValidationError):
        validate_drill_selection(metric, campaign, parents)
    scoped = apply_parents(DimensionFilters(channels=("WhatsApp",)), parse_parents(["channel:WhatsApp"]))
    assert scoped.channels == ("WhatsApp",)
    with pytest.raises(FilterValidationError):
        apply_parents(DimensionFilters(channels=("SMS",)), parse_parents(["channel:WhatsApp"]))
    october = month_window(date(2025, 10, 1))
    june = month_window(date(2025, 6, 1))
    current, prior = resolve_slice_windows(
        october,
        june,
        origin="trend",
        slice_grain="day",
        slice_bucket=date(2025, 10, 8),
    )
    assert current.day_from == date(2025, 10, 8)
    assert current.day_to_exclusive == date(2025, 10, 9)
    assert prior is not None
    assert prior.day_from == date(2025, 6, 8)


def _drill_chart_helpers() -> str:
    views = (
        Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js" / "views.js"
    ).read_text(encoding="utf-8")
    start = views.index("const DRILL_CHART_MAX_BARS")
    return views[start : views.index("function drillChartRowText(")]


def _run_node(script: Path) -> dict:
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, str(script)], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_breakdown_chart_selection_preserves_backend_order(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    rows = [{"key": f"k{index}", "value": str(100 - index)} for index in range(12)]
    rows.append({"key": "__other__", "value": "7"})
    script = tmp_path / "chart_rows.mjs"
    script.write_text(
        _drill_chart_helpers()
        + "const rows = "
        + json.dumps(rows)
        + ";\n"
        + "const visible = drillChartRows(rows);\n"
        + "console.log(JSON.stringify({\n"
        + "  keys: visible.map((row) => row.key),\n"
        + "  count: visible.length,\n"
        + "  limit: DRILL_CHART_MAX_BARS,\n"
        + "  peak: drillChartPeak(visible).key,\n"
        + "}));\n",
        encoding="utf-8",
    )
    out = _run_node(script)
    assert out["limit"] == 10
    assert out["keys"] == [f"k{index}" for index in range(10)] + ["__other__"]
    assert out["keys"][:10] == [row["key"] for row in rows[:10]]
    assert out["count"] == 11
    assert out["peak"] == "k0"


def test_breakdown_chart_bar_scaling_handles_edge_values(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    rows = [
        {"key": "big", "value": "400"},
        {"key": "small", "value": "0.5"},
        {"key": "zero", "value": "0"},
        {"key": "null", "value": None},
        {"key": "blank", "value": ""},
        {"key": "negative", "value": "-200"},
    ]
    script = tmp_path / "chart_scale.mjs"
    script.write_text(
        _drill_chart_helpers()
        + "const rows = "
        + json.dumps(rows)
        + ";\n"
        + "const peak = drillChartPeak(rows);\n"
        + "const peakValue = drillChartNumber(peak.value);\n"
        + "console.log(JSON.stringify({\n"
        + "  peak: peak.key,\n"
        + "  widths: Object.fromEntries(rows.map((row) => "
        + "[row.key, drillChartBarPct(row.value, peakValue)])),\n"
        + "  emptyPeak: drillChartPeak([{ key: 'x', value: null }]),\n"
        + "}));\n",
        encoding="utf-8",
    )
    out = _run_node(script)
    widths = out["widths"]
    assert out["peak"] == "big"
    assert widths["big"] == 100
    assert widths["zero"] == 0
    assert widths["null"] is None
    assert widths["blank"] is None
    assert widths["small"] == pytest.approx(1.5)
    assert widths["negative"] == 50
    assert all(width is None or 0 <= width <= 100 for width in widths.values())
    assert out["emptyPeak"] is None


def test_breakdown_chart_scale_excludes_other_bucket(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    rows = [
        {"key": "top", "value": "187827.7400"},
        {"key": "second", "value": "132771.9000"},
        {"key": "__other__", "value": "1710945.6500"},
    ]
    script = tmp_path / "chart_other.mjs"
    script.write_text(
        _drill_chart_helpers()
        + "const rows = "
        + json.dumps(rows)
        + ";\n"
        + "const peak = drillChartPeak(rows);\n"
        + "const peakValue = drillChartNumber(peak.value);\n"
        + "const onlyOther = drillChartPeak([rows[2]]);\n"
        + "console.log(JSON.stringify({\n"
        + "  peak: peak.key,\n"
        + "  widths: Object.fromEntries(rows.map((row) => "
        + "[row.key, drillChartBarPct(row.value, peakValue)])),\n"
        + "  onlyOther: onlyOther.key,\n"
        + "}));\n",
        encoding="utf-8",
    )
    out = _run_node(script)
    assert out["peak"] == "top"
    assert out["widths"]["top"] == 100
    assert out["widths"]["second"] == pytest.approx(70.687, rel=1e-3)
    assert out["widths"]["__other__"] == 100
    assert out["onlyOther"] == "__other__"


def test_spa_preserves_drill_without_insights() -> None:
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    views = (root / "views.js").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    state = (root / "analytics-state.js").read_text(encoding="utf-8")
    components = (root / "components.js").read_text(encoding="utf-8")
    assert "data-overview-drill-kpi" in components
    assert "View details" in components
    assert 'class="metric-card overview-kpi' in components
    assert "rememberKpiDrillReturn" in app_js
    assert 'withDrill(withFocus(' in views
    assert "data-drill-panel" in views
    assert "data-drill-breakdown" in views
    assert "data-drill-chart" in views
    assert "data-drill-export" in views
    assert "data-drill-header-comparison" in views
    assert 'justify-content: flex-end' in (root.parent / "css" / "app.css").read_text(encoding="utf-8")
    assert "data-overview-drill" in views
    assert "data-drill-back" in views
    assert "data-drill-unauthorized" in views
    assert "data-drill-unsupported" in views
    assert "withDrill" in state
    assert "stripDrill" in state
    assert "copyKeys(existing, DRILL_SINGLE" in state
    assert "getOverviewDrilldown" in app_js
    assert "openTrendDrill" in app_js
    assert "data-trend-drill" in (root / "trend-chart.js").read_text(encoding="utf-8")
    assert 'href="/client/overview"' in views
    assert "Clear all" in views
    assert 'navItem("/admin", "Dashboard"' in components
    assert 'navItem("/client", "Reports"' in components
    assert "data-overview-explorer" in views
    assert "Performance Explorer" in views
    assert "data-overview-insights" in views
    assert "Insights" in views
    assert "data-overview-anomalies" in views
    assert "Anomalies" in views
    assert "data-ask" in views
    assert "data-overview-ask" in views
    assert "data-drill-mode" in views
    assert "data-drill-pane" in views
    assert "drillTrendParamsFromQuery" in state
    assert "ensureOverviewDrillTrend" in app_js
    assert "setOverviewDrillMode" in app_js


def test_d4_trend_tooltip_model_uses_payload_point_values(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    js_dir = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    (tmp_path / "format.mjs").write_text((js_dir / "format.js").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "trend-chart.mjs").write_text(
        (js_dir / "trend-chart.js").read_text(encoding="utf-8").replace("./format.js", "./format.mjs"),
        encoding="utf-8",
    )
    script = tmp_path / "tooltip.mjs"
    script.write_text(
        """
import { renderTrendChart, trendTooltipModel } from "./trend-chart.mjs";
const point = {
  bucket: "2025-10-14",
  bucket_label: "Oct 14, 2025",
  value: "8472350.00",
  comparison_value: "10210410.00",
  secondary_value: "0.805",
  comparison_secondary_value: "0.7516",
};
const model = trendTooltipModel(point, {
  primary: { label: "Revenue", kind: "money" },
  secondary: { label: "Delivery Rate", kind: "rate" },
  comparisonShown: true,
});
const none = trendTooltipModel(point, {
  primary: { label: "Revenue", kind: "money" },
  comparisonShown: false,
});
const trend = {
  metric: { label: "Revenue", kind: "money" },
  secondary: { label: "Delivery Rate", kind: "rate" },
  comparison_shown: true,
  selection: { grain: "day", chart: "line", dual_axis: false },
  series: [{ key: "total", label: "Company total", points: [point] }],
};
const markup = String(renderTrendChart(trend, { interactive: false, tooltip: true }));
const encoded = markup.match(/data-trend-point="([^"]+)"/)[1];
const fromChart = JSON.parse(decodeURIComponent(encoded.replaceAll("&amp;", "&")));
console.log(JSON.stringify({
  rawValue: model.rows.find((row) => row.key === "metric").raw,
  formattedValue: model.rows.find((row) => row.key === "metric").value,
  comparisonRaw: model.rows.find((row) => row.key === "comparison").raw,
  secondaryRaw: model.rows.find((row) => row.key === "secondary").raw,
  noneKeys: none.rows.map((row) => row.key),
  chartRaw: fromChart.rows.find((row) => row.key === "metric").raw,
  chartComparison: fromChart.rows.find((row) => row.key === "comparison").raw,
  chartSecondary: fromChart.rows.find((row) => row.key === "secondary").raw,
  inspectable: markup.includes("data-trend-tooltip-enabled"),
  summary: markup.includes("data-trend-point-summary"),
  noDrill: !markup.includes("data-trend-drill"),
  wide: markup.includes("viewBox=\\"0 0 1000 400\\""),
}));
""",
        encoding="utf-8",
    )
    out = _run_node(script)
    assert out["rawValue"] == "8472350.00"
    assert out["formattedValue"] == "8,472,350.00"
    assert out["comparisonRaw"] == "10210410.00"
    assert out["secondaryRaw"] == "0.805"
    assert out["noneKeys"] == ["metric"]
    assert out["chartRaw"] == "8472350.00"
    assert out["chartComparison"] == "10210410.00"
    assert out["chartSecondary"] == "0.805"
    assert out["inspectable"] is True
    assert out["summary"] is True
    assert out["noDrill"] is True
    assert out["wide"] is True
