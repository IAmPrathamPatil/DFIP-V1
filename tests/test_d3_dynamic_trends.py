"""D3 Dynamic Trends: registry, grains, filters, isolation, and D1/D2 regression."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from dfip_analytics.kpis import compute_kpis
from dfip_analytics.trends import (
    TREND_DIMENSIONS_BY_KEY,
    TREND_GRAINS,
    TREND_METRICS_BY_KEY,
    bucket_start,
    iso_week_start,
)
from dfip_api.errors import PersistenceUnavailableError
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.transform.fact import FactRecord

from test_d1_kpi_overview import CLIENT_B, OCT, OVERVIEW, _kpi_map, _publish
from test_d2_global_filters import _store as d2_store
from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers

TRENDS = "/api/v1/analytics/trends"
JUN = date(2025, 6, 15)
OCT_MON = date(2025, 10, 6)
OCT_WED = date(2025, 10, 8)
OCT_NEXT_MON = date(2025, 10, 13)


def _fact(
    *,
    client_id: str,
    campaign_id: str,
    day: date,
    campaign_name: str | None = None,
    channel: str | None = "WhatsApp",
    filter_logic_1: str | None = "Group A",
    filter_logic_1_group: str | None = "A",
    sent: int = 100,
    delivered: int = 80,
    unique_clicks: int = 10,
    unique_conversions: int = 2,
    total_cost: str = "40.0000",
    revenue_inr: str = "80.0000",
) -> FactRecord:
    return FactRecord(
        client_id=client_id,
        campaign_id=campaign_id,
        campaign_name=campaign_name or campaign_id,
        variation_id="var-1",
        variation_id_key="var-1",
        day=day,
        month_start=date(day.year, day.month, 1),
        channel=channel,
        filter_logic_1=filter_logic_1,
        filter_logic_1_group=filter_logic_1_group,
        sent=sent,
        delivered=delivered,
        unique_clicks=unique_clicks,
        unique_conversions=unique_conversions,
        total_cost=Decimal(total_cost),
        revenue_inr=Decimal(revenue_inr),
        processing_run_id="run-d3",
    )


def _d3_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=JUN,
                sent=100,
                delivered=10,
                unique_clicks=1,
                total_cost="10.0000",
                revenue_inr="20.0000",
            )
        ],
        "run-jun",
    )
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=OCT_MON,
                sent=10,
                delivered=10,
                unique_clicks=2,
                total_cost="10.0000",
                revenue_inr="20.0000",
            ),
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-b",
                day=OCT_WED,
                channel="SMS",
                filter_logic_1="Group B",
                filter_logic_1_group="B",
                sent=90,
                delivered=10,
                unique_clicks=8,
                total_cost="30.0000",
                revenue_inr="60.0000",
            ),
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=OCT_NEXT_MON,
                sent=100,
                delivered=80,
                unique_clicks=10,
                total_cost="20.0000",
                revenue_inr="40.0000",
            ),
        ],
        "run-oct",
    )
    _publish(
        store,
        CLIENT_B,
        [_fact(client_id=CLIENT_B, campaign_id="camp-a", day=OCT_WED, total_cost="999.0000")],
        "run-b",
    )
    return store


def _get(store: InMemoryPublicationStore, params: dict | None = None, *, role: str = "client", client_id: str = CLIENT_ID):
    http, *_rest = jwt_app(publication_store=store)
    return http.get(TRENDS, headers=jwt_headers(role, client_id), params=params or {})


def _series(body: dict, key: str = "total") -> dict:
    return next(item for item in body["series"] if item["key"] == key)


def _point(series: dict, bucket: str) -> dict:
    return next(item for item in series["points"] if item["bucket"] == bucket)


def test_iso_week_is_monday_start() -> None:
    assert iso_week_start(OCT_WED) == OCT_MON
    assert bucket_start(OCT_WED, "week") == OCT_MON
    assert bucket_start(OCT_WED, "month") == date(2025, 10, 1)
    assert TREND_GRAINS == ("day", "week", "month")
    assert set(TREND_METRICS_BY_KEY) == {
        "total_cost",
        "revenue_inr",
        "overall_roas",
        "delivered",
        "unique_clicks",
        "unique_conversions",
        "delivery_rate",
        "ctr_del_to_clicks",
    }
    assert set(TREND_DIMENSIONS_BY_KEY) == {
        "campaign_id",
        "channel",
        "filter_logic_1",
        "filter_logic_1_group",
    }


def test_primary_metric_only_defaults_total_cost_day() -> None:
    body = _get(_d3_store()).json()
    assert body["selection"]["metric"] == "total_cost"
    assert body["selection"]["secondary"] is None
    assert body["selection"]["grain"] == "day"
    assert body["selection"]["breakdown"] is None
    assert body["selection"]["chart"] == "line"
    series = _series(body)
    assert _point(series, "2025-10-06")["value"] == "10.0000"
    assert _point(series, "2025-10-08")["value"] == "30.0000"
    assert _point(series, "2025-10-13")["value"] == "20.0000"
    assert _point(series, "2025-10-01")["value"] is None


def test_primary_plus_secondary_metric() -> None:
    body = _get(_d3_store(), {"metric": "total_cost", "secondary": "delivered"}).json()
    assert body["selection"]["dual_axis"] is True
    point = _point(_series(body), "2025-10-06")
    assert point["value"] == "10.0000"
    assert point["secondary_value"] == 10


def test_day_week_and_month_grains() -> None:
    store = _d3_store()
    day_body = _get(store, {"grain": "day"}).json()
    week_body = _get(store, {"grain": "week"}).json()
    month_body = _get(store, {"grain": "month"}).json()
    assert _point(_series(day_body), "2025-10-06")["value"] == "10.0000"
    week = _series(week_body)
    assert _point(week, "2025-10-06")["value"] == "40.0000"
    assert _point(week, "2025-10-06")["bucket_label"] == "2025-W41"
    assert _point(week, "2025-10-13")["value"] == "20.0000"
    month = _series(month_body)
    assert len(month["points"]) == 1
    assert month["points"][0]["bucket"] == "2025-10-01"
    assert month["points"][0]["value"] == "60.0000"


def test_campaign_and_channel_breakdown() -> None:
    store = _d3_store()
    campaigns = _get(store, {"breakdown": "campaign_id", "grain": "month"}).json()
    keys = {item["key"] for item in campaigns["series"]}
    assert keys == {"camp-a", "camp-b"}
    assert campaigns["selection"]["chart"] == "bar"
    assert campaigns["comparison_shown"] is False
    assert campaigns["comparison_omitted_reason"] == "comparison_not_shown_with_breakdown"
    assert _point(_series(campaigns, "camp-a"), "2025-10-01")["value"] == "30.0000"
    channels = _get(store, {"breakdown": "channel", "grain": "month"}).json()
    channel_keys = {item["key"] for item in channels["series"]}
    assert channel_keys == {"WhatsApp", "SMS"}


def test_d2_filters_apply_and_stack() -> None:
    store = _d3_store()
    filtered = _get(
        store,
        {
            "grain": "month",
            "channel": "SMS",
            "campaign_id": "camp-b",
            "filter_logic_1": "Group B",
        },
    ).json()
    assert filtered["applied"]["channels"] == ["SMS"]
    assert filtered["applied"]["campaign_ids"] == ["camp-b"]
    assert _series(filtered)["points"][0]["value"] == "30.0000"
    overview = jwt_app(publication_store=store)[0].get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"channel": "SMS", "campaign_id": "camp-b", "filter_logic_1": "Group B"},
    ).json()
    assert _kpi_map(overview)["total_cost"]["value"] == "30.0000"


def test_invalid_metric_dimension_pair_and_combo() -> None:
    store = _d3_store()
    http, *_rest = jwt_app(publication_store=store)
    headers = jwt_headers("client")
    unknown_metric = http.get(TRENDS, headers=headers, params={"metric": "orders"})
    assert unknown_metric.status_code == 422
    unknown_dim = http.get(TRENDS, headers=headers, params={"breakdown": "variation_id"})
    assert unknown_dim.status_code == 422
    combo = http.get(
        TRENDS,
        headers=headers,
        params={"metric": "total_cost", "secondary": "delivered", "breakdown": "channel"},
    )
    assert combo.status_code == 422
    same = http.get(TRENDS, headers=headers, params={"metric": "total_cost", "secondary": "total_cost"})
    assert same.status_code == 422


def test_comparison_off_and_on() -> None:
    store = _d3_store()
    none = _get(store, {"compare": "none", "grain": "month"}).json()
    assert none["comparison"]["available"] is False
    assert none["comparison_shown"] is False
    auto = _get(store, {"grain": "month"}).json()
    assert auto["comparison"]["available"] is True
    assert auto["comparison_shown"] is True
    october = _series(auto)["points"][0]
    assert october["value"] == "60.0000"
    assert october["comparison_value"] == "10.0000"


def test_empty_result_and_query_error() -> None:
    empty = _get(_d3_store(), {"period": "range", "day_from": "2025-07-01", "day_to": "2025-07-31"}).json()
    assert empty["empty"] is True
    assert empty["has_published_history"] is True

    class BoomStore(InMemoryPublicationStore):
        def list_published_history_series(self, *args, **kwargs):
            raise PersistenceUnavailableError("trend store failed")

    store = BoomStore()
    _publish(store, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT)], "run-1")
    response = _get(store)
    assert response.status_code == 503


def test_tenant_isolation_and_client_id_cannot_widen() -> None:
    store = _d3_store()
    http, *_rest = jwt_app(publication_store=store)
    a_body = http.get(TRENDS, headers=jwt_headers("client", CLIENT_ID), params={"grain": "month"}).json()
    b_body = http.get(TRENDS, headers=jwt_headers("client", CLIENT_B), params={"grain": "month"}).json()
    assert _series(a_body)["points"][0]["value"] == "60.0000"
    assert _series(b_body)["points"][0]["value"] == "999.0000"
    denied = http.get(
        TRENDS,
        headers=jwt_headers("client", CLIENT_ID),
        params={"client_id": CLIENT_B, "grain": "month"},
    )
    assert denied.status_code == 403
    assert "999.0000" not in denied.text
    publisher = http.get(TRENDS, headers=jwt_headers("publisher", CLIENT_ID), params={"grain": "month"})
    assert publisher.status_code == 200
    assert _series(publisher.json())["points"][0]["value"] == "60.0000"
    leak = http.get(
        TRENDS,
        headers=jwt_headers("client", CLIENT_ID),
        params={"breakdown": "campaign_id", "grain": "month"},
    ).json()
    assert "999.0000" not in str(leak)


def test_corrected_month_newest_wins_on_trend() -> None:
    store = InMemoryPublicationStore()
    grain = _fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="10.0000", revenue_inr="10.0000")
    _publish(store, CLIENT_ID, [grain], "run-old")
    corrected = _fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="40.0000", revenue_inr="80.0000")
    _publish(store, CLIENT_ID, [corrected], "run-new")
    body = _get(store, {"grain": "month"}).json()
    point = _series(body)["points"][0]
    assert point["value"] == "40.0000"
    assert point["value"] != "50.0000"


def test_ratio_and_percentage_after_aggregation() -> None:
    store = _d3_store()
    body = _get(store, {"metric": "delivery_rate", "grain": "month"}).json()
    point = _series(body)["points"][0]
    expected = compute_kpis(
        {
            "sent": Decimal(200),
            "delivered": Decimal(100),
            "unique_clicks": Decimal(20),
            "unique_conversions": Decimal(6),
            "total_cost": Decimal("60.0000"),
            "revenue_inr": Decimal("120.0000"),
        },
        namespace="client",
    )
    assert point["value"] == format(expected["delivery_rate"], "f")
    assert point["numerator"] == 100
    assert point["denominator"] == 200
    daily = _get(store, {"metric": "delivery_rate", "grain": "day"}).json()
    day_rates = [
        Decimal(_point(_series(daily), "2025-10-06")["value"]),
        Decimal(_point(_series(daily), "2025-10-08")["value"]),
        Decimal(_point(_series(daily), "2025-10-13")["value"]),
    ]
    assert sum(day_rates) / 3 != Decimal(point["value"])
    ctr = _get(store, {"metric": "ctr_del_to_clicks", "grain": "month"}).json()
    ctr_point = _series(ctr)["points"][0]
    assert ctr_point["value"] == format(expected["ctr_del_to_clicks"], "f")
    roas = _get(store, {"metric": "overall_roas", "grain": "month"}).json()
    assert _series(roas)["points"][0]["value"] == format(expected["overall_roas"], "f")


def test_d1_kpi_and_d2_filter_regression() -> None:
    store = d2_store()
    http, *_rest = jwt_app(publication_store=store)
    headers = jwt_headers("client")
    overview = http.get(OVERVIEW, headers=headers).json()
    assert overview["applied"]["is_default"] is True
    assert [item["id"] for item in overview["kpis"]] == list(TREND_METRICS_BY_KEY)
    assert _kpi_map(overview)["total_cost"]["value"] == "60.0000"
    june = http.get(OVERVIEW, headers=headers, params={"month_start": "2025-06-01"}).json()
    assert _kpi_map(june)["total_cost"]["value"] == "15.0000"
    trend = http.get(TRENDS, headers=headers, params={"grain": "month"}).json()
    assert trend["applied"]["is_default"] is True
    assert _series(trend)["points"][0]["value"] == "60.0000"
    sms = http.get(TRENDS, headers=headers, params={"grain": "month", "channel": "SMS"}).json()
    assert sms["applied"]["channels"] == ["SMS"]
    assert _series(sms)["points"][0]["value"] == "20.0000"


def test_d3_trend_presentation_layer() -> None:
    """Trends states its analytical context as labels and keeps the chart dominant."""

    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static"
    views = (root / "js" / "views.js").read_text(encoding="utf-8")
    chart = (root / "js" / "trend-chart.js").read_text(encoding="utf-8")
    css = (root / "css" / "app.css").read_text(encoding="utf-8")
    section = views[
        views.index("function overviewTrendSection") : views.index("const DRILL_DIM_LABELS")
    ]

    assert 'data-trend-title="true"' in section
    assert "contextChips(" in section
    for term in ('"Metric"', '"Secondary"', '"Time grain"', '"Breakdown"', '"Period"', '"Comparison"'):
        assert term in section
    assert '<p class="overview-control-label">Series</p>' in section
    assert '<p class="overview-control-label">Time and breakdown</p>' in section

    # Labels come from the existing registries, not from raw selection ids.
    assert "TREND_METRIC_OPTIONS.find" in section
    assert "TREND_GRAIN_OPTIONS.find" in section
    assert "TREND_BREAKDOWN_OPTIONS.find" in section
    assert "${metric}" not in section.split("<select")[0]

    # Comparison state stays explicit and the existing messages are unchanged.
    assert 'data-trend-comparison="shown"' in section
    assert "Comparison is overlaid by period offset from the start of each window." in section
    assert "Comparison is hidden while a breakdown is selected." in section
    assert "No comparison: comparison is turned off." in section
    assert "No comparison for this trend." in section
    assert 'trendCompareLabel' in section

    # Chart frame owns the chart plus its notes, and every state keeps the frame.
    assert section.index("renderTrendChart(trend)") < section.index('class="trend-notes"')
    assert section.count('class="trend-chart-frame is-state"') == 3
    assert 'data-trend-unsupported="true"' in section
    assert 'data-trend-truncated="true"' in section

    # Primary and secondary series are distinguishable without relying on colour.
    assert 'is-primary' in chart
    assert 'is-secondary' in chart
    assert 'dual ? " (left axis)" : ""' in chart
    assert 'dual ? " (right axis)" : ""' in chart
    assert "trend-swatch-dashed" in chart

    assert ".overview-controls-inline .overview-control-groups {" in css
    assert ".trend-chart-frame.is-state {" in css
    assert ".trend-notes," in css
    assert "min-height: 17rem" in css


def test_spa_has_trend_workspace_without_explorer() -> None:
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    views = (root / "views.js").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    state = (root / "analytics-state.js").read_text(encoding="utf-8")
    assert "data-overview-trend" in views
    assert "queryFromTrendForm" in state
    assert "getOverviewTrends" in app_js
    trend_section = views[
        views.index("function overviewTrendSection") : views.index("const EXPLORER_DIM_OPTIONS")
    ].lower()
    assert "ranking" not in trend_section
    assert "anomal" not in trend_section
    assert "performance explorer" not in trend_section
    assert "function overviewExplorerSection" in views
    components = (root / "components.js").read_text(encoding="utf-8")
    assert 'navItem("/admin", "Dashboard"' in components
    assert 'navItem("/client", "Reports"' in components


def test_include_metric_batch_matches_sequential_and_preserves_single_metric() -> None:
    from dfip_analytics.trends import MAX_INCLUDE_METRICS, parse_include_metrics

    store = _d3_store()
    keys = list(TREND_METRICS_BY_KEY)
    assert MAX_INCLUDE_METRICS == 8
    assert [item.key for item in parse_include_metrics(["total_cost", "total_cost", "revenue_inr"])] == [
        "total_cost",
        "revenue_inr",
    ]
    assert len(parse_include_metrics(keys)) == 8

    plain = _get(store, {"grain": "month"}).json()
    first = _series(plain)["points"][0]
    assert "values" not in first
    assert first["value"] == "60.0000"
    assert first["comparison_value"] == "10.0000"
    assert plain["selection"]["metric"] == "total_cost"

    batch = _get(
        store,
        {"grain": "month", "compare": "none", "include_metric": keys},
    ).json()
    assert batch["comparison_shown"] is False
    assert batch["selection"]["metric"] == "total_cost"
    assert batch["metrics"] and isinstance(batch["metrics"], list)
    assert all(isinstance(item, dict) and "key" in item for item in batch["metrics"])
    batch_point = _series(batch)["points"][0]
    assert batch_point["comparison_value"] is None
    assert set(batch_point["values"]) == set(keys)
    for key in keys:
        single = _get(store, {"metric": key, "grain": "month", "compare": "none"}).json()
        sequential = _series(single)["points"][0]["value"]
        assert batch_point["values"][key] == sequential
        assert batch_point["value"] == batch_point["values"]["total_cost"]

    expected = compute_kpis(
        {
            "sent": Decimal(200),
            "delivered": Decimal(100),
            "unique_clicks": Decimal(20),
            "unique_conversions": Decimal(6),
            "total_cost": Decimal("60.0000"),
            "revenue_inr": Decimal("120.0000"),
        },
        namespace="client",
    )
    assert batch_point["values"]["delivery_rate"] == format(expected["delivery_rate"], "f")
    assert batch_point["values"]["ctr_del_to_clicks"] == format(expected["ctr_del_to_clicks"], "f")
    assert batch_point["values"]["overall_roas"] == format(expected["overall_roas"], "f")
    assert batch_point["values"]["delivered"] == 100
    assert batch_point["values"]["total_cost"] == "60.0000"

    http, *_rest = jwt_app(publication_store=store)
    headers = jwt_headers("client")
    unknown = http.get(TRENDS, headers=headers, params={"include_metric": "orders"})
    assert unknown.status_code == 422
    with_break = http.get(
        TRENDS,
        headers=headers,
        params={"include_metric": "total_cost", "breakdown": "channel"},
    )
    assert with_break.status_code == 422
    with_secondary = http.get(
        TRENDS,
        headers=headers,
        params={"include_metric": "total_cost", "secondary": "delivered"},
    )
    assert with_secondary.status_code == 422

    isolated = http.get(
        TRENDS,
        headers=jwt_headers("client", CLIENT_ID),
        params={"client_id": str(CLIENT_B), "grain": "month", "include_metric": keys},
    )
    assert isolated.status_code == 403
