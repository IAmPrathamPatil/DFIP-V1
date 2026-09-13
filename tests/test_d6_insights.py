"""D6 deterministic Insights: materiality, drivers, ranking, isolation, D1–D5 regression."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from dfip_analytics.filters import FilterValidationError
from dfip_analytics.insights import (
    DRIVER_SHARE,
    MATERIAL_HIGH_PCT,
    MATERIAL_PCT,
    insight_score,
    parse_insight_dimension,
    parse_insight_limit,
    parse_insight_metric,
)
from dfip_analytics.kpis import compute_kpis
from dfip_analytics.trends import TREND_METRICS_BY_KEY
from dfip_api.errors import PersistenceUnavailableError
from dfip_api.publication_store import InMemoryPublicationStore

from test_d1_kpi_overview import CLIENT_B, OCT, OVERVIEW, _kpi_map, _publish
from test_d2_global_filters import _store as d2_store
from test_d3_dynamic_trends import TRENDS, _d3_store, _fact, _series
from test_d4_drilldown import DRILL
from test_d5_performance_explorer import EXPLORER
from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers

INSIGHTS = "/api/v1/analytics/insights"
JUN = date(2025, 6, 15)


def _get(store, params=None, *, role: str = "client", client_id: str = CLIENT_ID):
    http, *_rest = jwt_app(publication_store=store)
    return http.get(INSIGHTS, headers=jwt_headers(role, client_id), params=params or {})


def _item(body: dict, insight_id: str | None = None, **fields) -> dict:
    rows = body["insights"]
    if insight_id is not None:
        return next(item for item in rows if item["insight_id"] == insight_id)
    for item in rows:
        if all(item.get(key) == value for key, value in fields.items()):
            return item
    raise AssertionError(f"no insight matching {fields} in {[row['insight_id'] for row in rows]}")


def _tiny_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="camp-a", day=JUN, total_cost="40.0000", revenue_inr="80.0000")],
        "tiny-jun",
    )
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="camp-a", day=OCT, total_cost="42.0000", revenue_inr="84.0000")],
        "tiny-oct",
    )
    return store


def _zero_cost_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="camp-a", day=JUN, total_cost="10.0000", revenue_inr="20.0000")],
        "zero-jun",
    )
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="camp-a", day=OCT, total_cost="0.0000", revenue_inr="20.0000")],
        "zero-oct",
    )
    return store


def _new_campaign_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="camp-a", day=JUN, total_cost="10.0000", revenue_inr="20.0000")],
        "null-jun",
    )
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(client_id=CLIENT_ID, campaign_id="camp-a", day=OCT, total_cost="10.0000", revenue_inr="20.0000"),
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-b",
                day=OCT,
                channel="SMS",
                total_cost="40.0000",
                revenue_inr="40.0000",
            ),
        ],
        "null-oct",
    )
    return store


def _zero_sent_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="camp-a", day=JUN, sent=100, delivered=80, total_cost="10.0000")],
        "den-jun",
    )
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="camp-a", day=OCT, sent=0, delivered=0, total_cost="20.0000")],
        "den-oct",
    )
    return store


def _one_month_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="camp-a", day=OCT, total_cost="40.0000")],
        "one",
    )
    return store


def _relationship_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=JUN,
                total_cost="10.0000",
                revenue_inr="80.0000",
                sent=100,
                delivered=80,
            )
        ],
        "rel-jun",
    )
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=OCT,
                total_cost="50.0000",
                revenue_inr="100.0000",
                sent=200,
                delivered=90,
            )
        ],
        "rel-oct",
    )
    return store


def test_material_positive_and_negative_change() -> None:
    store = d2_store()
    body = _get(store).json()
    cost = _item(body, category="material_change", metric="total_cost")
    assert cost["current_value"] == "60.0000"
    assert cost["prior_value"] == "15.0000"
    assert cost["delta"] == "45.0000"
    assert Decimal(cost["delta_pct"]) == Decimal("3.000000")
    assert "increased" in cost["explanation"]
    assert cost["evidence"]["materiality_pct"] == str(MATERIAL_PCT)
    down = _get(
        store, {"month_start": "2025-06-01", "compare_month_start": "2025-10-01"}
    ).json()
    declined = _item(down, category="material_change", metric="total_cost")
    assert declined["delta"] == "-45.0000"
    assert "declined" in declined["explanation"]


def test_below_threshold_movement_suppressed() -> None:
    body = _get(_tiny_store()).json()
    assert body["empty"] is True
    assert body["empty_reason"] == "no_material_insights"
    assert body["insights"] == []


def test_dominant_driver_multiple_and_contribution() -> None:
    body = _get(d2_store(), {"metric": "total_cost", "dimension": "campaign_id"}).json()
    driver = _item(body, category="dominant_driver", metric="total_cost")
    assert driver["dimension"] == "campaign_id"
    assert driver["driver_key"] == "camp-a"
    assert Decimal(driver["driver_contribution_pct"]) == Decimal("0.666667")
    assert len(driver["drivers"]) >= 2
    assert driver["drivers"][0]["key"] == "camp-a"
    assert driver["drivers"][1]["key"] == "camp-b"
    assert Decimal(driver["drivers"][1]["contribution_pct"]) == Decimal("0.333333")
    assert "camp-a" in driver["explanation"]
    assert "accounted for" in driver["explanation"]
    assert "66.7%" in driver["explanation"]
    assert driver["drivers"][0]["drillable"] is True
    assert driver["drivers"][0]["drill_parents"] == ["campaign_id:camp-a"]


def test_ratio_metric_zero_denominator_and_null_group() -> None:
    store = d2_store()
    body = _get(store, {"metric": "overall_roas"}).json()
    roas = _item(body, metric="overall_roas")
    expected = compute_kpis(
        {
            "total_cost": Decimal("60.0000"),
            "revenue_inr": Decimal("120.0000"),
            "sent": Decimal(200),
            "delivered": Decimal(160),
            "unique_clicks": Decimal(20),
            "unique_conversions": Decimal(4),
        },
        namespace="client",
    )
    assert roas["current_value"] == format(expected["overall_roas"], "f")
    assert roas["evidence"]["contribution_valid"] is False
    assert roas["driver_contribution_pct"] is None
    assert not any(item["category"] == "dominant_driver" for item in body["insights"])
    zero = _get(_zero_cost_store()).json()
    assert all(item["metric"] != "overall_roas" for item in zero["insights"])
    added = _get(_new_campaign_store(), {"metric": "total_cost", "dimension": "campaign_id"}).json()
    driver = _item(added, category="dominant_driver", metric="total_cost")
    assert driver["driver_key"] == "camp-b"
    camp_b = next(item for item in driver["drivers"] if item["key"] == "camp-b")
    assert camp_b["prior_value"] == "0.0000"
    assert Decimal(camp_b["contribution_pct"]) == Decimal("1.000000")


def test_no_comparison_insufficient_history_and_denominator() -> None:
    none = _get(d2_store(), {"compare": "none"}).json()
    assert none["empty"] is True
    assert none["empty_reason"] == "insufficient_comparison"
    assert none["insights"] == []
    history = _get(_one_month_store()).json()
    assert history["empty"] is True
    assert history["empty_reason"] == "insufficient_history"
    denom = _get(_zero_sent_store()).json()
    assert all(item["metric"] != "delivery_rate" for item in denom["insights"])


def test_ranking_filters_newest_wins_and_relationship() -> None:
    body = _get(d2_store()).json()
    scores = [Decimal(item["score"]) for item in body["insights"]]
    assert scores == sorted(scores, reverse=True)
    assert [item["rank"] for item in body["insights"]] == list(range(1, len(body["insights"]) + 1))
    filtered = _get(d2_store(), {"channel": "SMS", "metric": "total_cost"}).json()
    assert filtered["applied"]["channels"] == ["SMS"]
    cost = _item(filtered, metric="total_cost", category="material_change")
    assert cost["current_value"] == "20.0000"
    assert cost["prior_value"] == "5.0000"
    wins = InMemoryPublicationStore()
    _publish(wins, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=JUN, total_cost="10.0000")], "old-jun")
    _publish(wins, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="10.0000")], "old-oct")
    _publish(wins, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="40.0000")], "new-oct")
    newest = _get(wins, {"metric": "total_cost"}).json()
    changed = _item(newest, metric="total_cost")
    assert changed["current_value"] == "40.0000"
    mix = _get(_relationship_store()).json()
    rel = _item(mix, category="relationship")
    assert rel["metric"] == "revenue_inr"
    assert rel["related_metric"] == "overall_roas"
    assert "mix/efficiency" in rel["explanation"]
    assert Decimal(rel["delta_pct"]) >= MATERIAL_HIGH_PCT or Decimal(rel["delta_pct"]) >= MATERIAL_PCT


def test_tenant_isolation_invalid_input_empty_and_errors() -> None:
    http, *_rest = jwt_app(publication_store=d2_store())
    assert http.get(INSIGHTS).status_code == 401
    denied = http.get(
        INSIGHTS,
        headers=jwt_headers("client", CLIENT_ID),
        params={"client_id": CLIENT_B},
    )
    assert denied.status_code == 403
    unbound = http.get(INSIGHTS, headers=jwt_headers("publisher", None))
    assert unbound.status_code == 403
    publisher = http.get(INSIGHTS, headers=jwt_headers("publisher", CLIENT_ID))
    assert publisher.status_code == 200
    a_body = http.get(INSIGHTS, headers=jwt_headers("client", CLIENT_ID)).json()
    b_body = http.get(INSIGHTS, headers=jwt_headers("client", CLIENT_B)).json()
    assert "999.0000" not in str(a_body)
    assert b_body["empty"] is True or "999.0000" in str(b_body)
    headers = jwt_headers("client")
    assert http.get(INSIGHTS, headers=headers, params={"metric": "orders"}).status_code == 422
    assert http.get(INSIGHTS, headers=headers, params={"dimension": "brand"}).status_code == 422
    assert http.get(INSIGHTS, headers=headers, params={"dimension": "day"}).status_code == 422
    assert http.get(INSIGHTS, headers=headers, params={"limit": 0}).status_code == 422

    class BoomStore(InMemoryPublicationStore):
        def list_published_history_groups(self, *args, **kwargs):
            raise PersistenceUnavailableError("insight store failed")

    boom = BoomStore()
    from test_d1_kpi_overview import _fact as d1_fact

    _publish(boom, CLIENT_ID, [d1_fact(client_id=CLIENT_ID, campaign_id="c1", day=JUN)], "jun")
    _publish(boom, CLIENT_ID, [d1_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT)], "oct")
    assert _get(boom).status_code == 503


def test_d1_d5_regression() -> None:
    d2 = d2_store()
    http, *_rest = jwt_app(publication_store=d2)
    headers = jwt_headers("client")
    overview = http.get(OVERVIEW, headers=headers).json()
    assert [item["id"] for item in overview["kpis"]] == list(TREND_METRICS_BY_KEY)
    assert _kpi_map(overview)["total_cost"]["value"] == "60.0000"
    trend = http.get(TRENDS, headers=headers, params={"grain": "month"}).json()
    assert _series(trend)["points"][0]["value"] == "60.0000"
    drill = http.get(DRILL, headers=headers, params={"dimension": "channel"}).json()
    assert {item["key"] for item in drill["rows"]} == {"WhatsApp", "SMS"}
    explorer = http.get(
        EXPLORER, headers=headers, params={"dimension": "campaign_id", "channel": "SMS"}
    ).json()
    assert explorer["applied"]["channels"] == ["SMS"]
    assert explorer["rows"][0]["value"] == "20.0000"
    insights = http.get(INSIGHTS, headers=headers, params={"channel": "SMS"}).json()
    assert insights["applied"]["channels"] == ["SMS"]
    sms_overview = http.get(OVERVIEW, headers=headers, params={"channel": "SMS"}).json()
    assert _kpi_map(sms_overview)["total_cost"]["value"] == "20.0000"
    d3_http, *_rest = jwt_app(publication_store=_d3_store())
    d3_body = d3_http.get(
        INSIGHTS, headers=jwt_headers("client"), params={"metric": "delivery_rate"}
    ).json()
    assert d3_body["comparison"]["available"] is True


def test_insight_helpers_reject_invalid_inputs() -> None:
    with pytest.raises(FilterValidationError):
        parse_insight_metric("orders")
    with pytest.raises(FilterValidationError):
        parse_insight_dimension("brand")
    with pytest.raises(FilterValidationError):
        parse_insight_dimension("day")
    with pytest.raises(FilterValidationError):
        parse_insight_limit(0)
    assert parse_insight_metric(None) is None
    score = insight_score(
        category="dominant_driver",
        delta=Decimal("45"),
        delta_pct=Decimal("3.0"),
        prior=Decimal("15"),
        driver_share=DRIVER_SHARE,
    )
    assert score > 0


def test_d6_insight_presentation_layer() -> None:
    """Insight cards lead with a ranked human headline and keep rule strings secondary."""

    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static"
    views = (root / "js" / "views.js").read_text(encoding="utf-8")
    css = (root / "css" / "app.css").read_text(encoding="utf-8")
    section = views[
        views.index("function overviewInsightsSection") : views.index("const ANOMALY_KIND_LABELS")
    ]

    assert 'title: "Key Insights"' in section
    assert '<ol class="insight-grid finding-list" data-insights-list="true">' in section
    assert 'class="finding-item"' in section
    assert 'data-insight-rank="${item.rank}"' in section
    assert "findingRankLabel(item.rank)" in section
    assert 'lead ? "is-lead" : ""' in section
    assert 'data-insight-context="true"' in section
    assert "findingMetricLabel(item)" in section

    # Human headline and business context precede evidence and rule strings.
    assert section.index("${item.headline}") < section.index('class="insight-evidence is-secondary"')
    assert section.index('data-insight-context="true"') < section.index("${item.explanation}")
    assert 'data-insight-rule="true"' in section
    assert section.count("${item.threshold}") == 1
    assert section.index('data-insight-rule="true"') < section.index("${item.threshold}")
    assert section.index('class="insight-evidence is-secondary"') < section.index("${item.threshold}")

    # No raw internal identifiers survive as visible fallbacks.
    assert "|| item.category" not in section
    assert "${item.metric}<" not in section
    assert "|| insights.empty_reason" not in section
    assert "INSIGHT_EMPTY_STATUS[reason]" in section

    # The existing drill target and focus contract are untouched.
    assert 'data-insight-drill="${top.key}"' in section
    assert 'data-insight-select="${item.insight_id}"' in section
    assert 'class="btn-secondary finding-drill"' in section
    assert "withDrill(query || new URLSearchParams()" in section
    assert "withFocus(query || new URLSearchParams()" in section

    helpers = views[views.index("const INSIGHT_EMPTY_STATUS") : views.index("function overviewInsightsSection")]
    assert "TREND_METRIC_OPTIONS.find((option) => option[0] === metric)" in helpers
    assert views.index("const TREND_METRIC_OPTIONS") < views.index("function findingMetricLabel")

    assert ".finding-list {" in css
    assert ".finding-card.is-lead {" in css
    assert ".finding-rule > summary:focus-visible {" in css
    assert ".finding-context dd {" in css
    assert "@media (max-width: 480px)" in css


def test_spa_has_insights_without_anomalies() -> None:
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    views = (root / "views.js").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    state = (root / "analytics-state.js").read_text(encoding="utf-8")
    insight_section = views[
        views.index("function overviewInsightsSection") : views.index("const ANOMALY_KIND_LABELS")
    ].lower()
    assert "data-overview-insights" in views
    assert "Insights" in views
    assert "getOverviewInsights" in app_js
    assert "insightsParamsFromQuery" in state
    assert "data-insight-drill" in views
    assert "anomal" not in insight_section
    assert "llm" not in insight_section
    assert "export" not in insight_section
    assert "chat" not in insight_section
    assert 'navItem("/admin", "Dashboard"' in (root / "components.js").read_text(encoding="utf-8")
    assert 'navItem("/client", "Reports"' in (root / "components.js").read_text(encoding="utf-8")
