"""D7 anomaly / diagnostic: baseline, spike/drop, isolation, D1–D6 regression."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from dfip_analytics.anomalies import (
    MIN_BASELINE_MONTHS,
    classify_anomaly,
    parse_anomaly_dimension,
    parse_anomaly_metric,
)
from dfip_analytics.filters import FilterValidationError
from dfip_analytics.trends import TREND_METRICS_BY_KEY
from dfip_api.errors import PersistenceUnavailableError
from dfip_api.publication_store import InMemoryPublicationStore

from test_d1_kpi_overview import CLIENT_B, OVERVIEW, _kpi_map, _publish
from test_d2_global_filters import _store as d2_store
from test_d3_dynamic_trends import TRENDS, _d3_store, _fact, _series
from test_d4_drilldown import DRILL
from test_d5_performance_explorer import EXPLORER
from test_d6_insights import INSIGHTS
from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers

ANOMALIES = "/api/v1/analytics/anomalies"
JAN = date(2025, 1, 15)
FEB = date(2025, 2, 15)
MAR = date(2025, 3, 15)
APR = date(2025, 4, 15)
MAY = date(2025, 5, 8)


def _get(store, params=None, *, role: str = "client", client_id: str = CLIENT_ID):
    http, *_rest = jwt_app(publication_store=store)
    return http.get(ANOMALIES, headers=jwt_headers(role, client_id), params=params or {})


def _item(body: dict, **fields) -> dict:
    for item in body["anomalies"]:
        if all(item.get(key) == value for key, value in fields.items()):
            return item
    raise AssertionError(f"no anomaly matching {fields} in {[row['anomaly_id'] for row in body['anomalies']]}")


def _month(store, day: date, facts: list, run: str) -> None:
    _publish(store, CLIENT_ID, facts, run)


def _pair(day: date, *, a_cost: str, b_cost: str, a_rev: str = "16.0000", b_rev: str = "4.0000", **kwargs):
    sent = kwargs.get("sent", 100)
    delivered = kwargs.get("delivered", 80)
    return [
        _fact(
            client_id=CLIENT_ID,
            campaign_id="camp-a",
            day=day,
            total_cost=a_cost,
            revenue_inr=a_rev,
            sent=sent,
            delivered=delivered,
        ),
        _fact(
            client_id=CLIENT_ID,
            campaign_id="camp-b",
            day=day,
            channel="SMS",
            filter_logic_1="Group B",
            filter_logic_1_group="B",
            total_cost=b_cost,
            revenue_inr=b_rev,
            sent=sent,
            delivered=delivered,
        ),
    ]


def _history_store(may_cost_a="40.0000", may_cost_b="10.0000", **may_kwargs) -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    for day, tag, a, b in (
        (JAN, "jan", "8.0000", "2.0000"),
        (FEB, "feb", "8.0000", "2.0000"),
        (MAR, "mar", "8.0000", "2.0000"),
        (APR, "apr", "8.0000", "2.0000"),
    ):
        _month(store, day, _pair(day, a_cost=a, b_cost=b), tag)
    _month(
        store,
        MAY,
        _pair(MAY, a_cost=may_cost_a, b_cost=may_cost_b, **may_kwargs),
        "may",
    )
    return store


def _stable_store() -> InMemoryPublicationStore:
    return _history_store(may_cost_a="8.0000", may_cost_b="2.0000")


def test_sufficient_history_genuine_spike_and_driver() -> None:
    body = _get(_history_store(), {"metric": "total_cost"}).json()
    assert body["empty"] is False
    assert body["baseline"]["observation_count"] == 4
    assert body["baseline"]["required_count"] == MIN_BASELINE_MONTHS
    spike = _item(body, metric="total_cost")
    assert spike["kind"] in {"spike", "rolling_deviation"}
    assert spike["direction"] == "spike"
    assert spike["current_value"] == "50.0000"
    assert spike["baseline_value"] == "10.0000"
    assert Decimal(spike["delta"]) == Decimal("40.0000")
    assert spike["evidence"]["new_extreme"] is True
    assert "median" in spike["explanation"]
    assert spike["affected_key"] == "camp-a"
    assert Decimal(spike["drivers"][0]["contribution_pct"]) >= Decimal("0.40")


def test_genuine_drop_and_normal_movement_suppressed() -> None:
    drop_store = InMemoryPublicationStore()
    for day, tag in ((JAN, "jan"), (FEB, "feb"), (MAR, "mar"), (APR, "apr")):
        _month(drop_store, day, _pair(day, a_cost="40.0000", b_cost="10.0000"), tag)
    _month(drop_store, MAY, _pair(MAY, a_cost="8.0000", b_cost="2.0000"), "may")
    dropped = _get(drop_store, {"metric": "total_cost"}).json()
    item = _item(dropped, metric="total_cost")
    assert item["direction"] == "drop"
    assert item["current_value"] == "10.0000"
    stable = _get(_stable_store(), {"metric": "total_cost"}).json()
    assert stable["empty"] is True
    assert stable["empty_reason"] == "no_anomalies"
    mild = _history_store(may_cost_a="10.0000", may_cost_b="2.0000")
    mild_body = _get(mild, {"metric": "total_cost"}).json()
    assert mild_body["empty"] is True


def test_insufficient_history_and_period_not_month() -> None:
    body = _get(d2_store()).json()
    assert body["empty"] is True
    assert body["empty_reason"] == "insufficient_history"
    assert body["baseline"]["observation_count"] < MIN_BASELINE_MONTHS
    ranged = _get(
        _history_store(),
        {"period": "range", "day_from": "2025-01-01", "day_to": "2025-05-31"},
    ).json()
    assert ranged["empty_reason"] == "period_not_month"


def test_ratio_zero_denominator_and_metric_specific_rules() -> None:
    rate_drop = InMemoryPublicationStore()
    for day, tag in ((JAN, "jan"), (FEB, "feb"), (MAR, "mar"), (APR, "apr")):
        _month(rate_drop, day, _pair(day, a_cost="8.0000", b_cost="2.0000", sent=100, delivered=80), tag)
    _month(
        rate_drop,
        MAY,
        _pair(MAY, a_cost="8.0000", b_cost="2.0000", sent=100, delivered=30),
        "may",
    )
    body = _get(rate_drop, {"metric": "delivery_rate"}).json()
    item = _item(body, metric="delivery_rate")
    assert item["direction"] == "drop"
    assert item["evidence"]["contribution_valid"] is False
    assert item["drivers"] == []
    tiny = InMemoryPublicationStore()
    for day, tag in ((JAN, "jan"), (FEB, "feb"), (MAR, "mar"), (APR, "apr")):
        _month(tiny, day, _pair(day, a_cost="8.0000", b_cost="2.0000", sent=100, delivered=80), tag)
    _month(
        tiny,
        MAY,
        _pair(MAY, a_cost="8.0000", b_cost="2.0000", sent=100, delivered=81),
        "may",
    )
    assert _get(tiny, {"metric": "delivery_rate"}).json()["empty"] is True
    zero = _history_store(may_cost_a="0.0000", may_cost_b="0.0000", a_rev="16.0000", b_rev="4.0000")
    roas = _get(zero, {"metric": "overall_roas"}).json()
    assert all(item["metric"] != "overall_roas" for item in roas["anomalies"])


def test_filters_newest_wins_ranking_and_compare_independence() -> None:
    store = _history_store()
    sms = _get(store, {"channel": "SMS", "metric": "total_cost"}).json()
    assert sms["applied"]["channels"] == ["SMS"]
    assert _item(sms, metric="total_cost")["current_value"] == "10.0000"
    none = _get(store, {"metric": "total_cost", "compare": "none"}).json()
    assert none["empty"] is False
    wins = _history_store(may_cost_a="8.0000", may_cost_b="2.0000")
    _month(wins, MAY, _pair(MAY, a_cost="40.0000", b_cost="10.0000"), "may-new")
    newest = _get(wins, {"metric": "total_cost"}).json()
    assert _item(newest, metric="total_cost")["current_value"] == "50.0000"
    ranked = _get(_history_store()).json()
    scores = [Decimal(item["severity_score"]) for item in ranked["anomalies"]]
    assert scores == sorted(scores, reverse=True)
    assert [item["rank"] for item in ranked["anomalies"]] == list(range(1, len(ranked["anomalies"]) + 1))


def test_tenant_isolation_invalid_empty_and_errors() -> None:
    store = _history_store()
    http, *_rest = jwt_app(publication_store=store)
    assert http.get(ANOMALIES).status_code == 401
    denied = http.get(
        ANOMALIES,
        headers=jwt_headers("client", CLIENT_ID),
        params={"client_id": CLIENT_B},
    )
    assert denied.status_code == 403
    unbound = http.get(ANOMALIES, headers=jwt_headers("publisher", None))
    assert unbound.status_code == 403
    publisher = http.get(ANOMALIES, headers=jwt_headers("publisher", CLIENT_ID))
    assert publisher.status_code == 200
    other = InMemoryPublicationStore()
    for day, tag in ((JAN, "jan"), (FEB, "feb"), (MAR, "mar"), (APR, "apr")):
        _publish(other, CLIENT_ID, _pair(day, a_cost="8.0000", b_cost="2.0000"), f"a-{tag}")
        _publish(
            other,
            CLIENT_B,
            [_fact(client_id=CLIENT_B, campaign_id="camp-a", day=day, total_cost="999.0000", revenue_inr="999.0000")],
            f"b-{tag}",
        )
    _publish(other, CLIENT_ID, _pair(MAY, a_cost="40.0000", b_cost="10.0000"), "a-may")
    _publish(
        other,
        CLIENT_B,
        [_fact(client_id=CLIENT_B, campaign_id="camp-a", day=MAY, total_cost="999.0000", revenue_inr="999.0000")],
        "b-may",
    )
    http, *_rest = jwt_app(publication_store=other)
    a_body = http.get(ANOMALIES, headers=jwt_headers("client", CLIENT_ID), params={"metric": "total_cost"}).json()
    b_body = http.get(ANOMALIES, headers=jwt_headers("client", CLIENT_B), params={"metric": "total_cost"}).json()
    assert a_body["client_id"] == CLIENT_ID
    assert b_body["client_id"] == CLIENT_B
    assert "999.0000" not in str(a_body)
    headers = jwt_headers("client")
    http, *_rest = jwt_app(publication_store=store)
    assert http.get(ANOMALIES, headers=headers, params={"metric": "orders"}).status_code == 422
    assert http.get(ANOMALIES, headers=headers, params={"dimension": "brand"}).status_code == 422
    assert http.get(ANOMALIES, headers=headers, params={"dimension": "day"}).status_code == 422

    class BoomStore(InMemoryPublicationStore):
        def list_published_history_series(self, *args, **kwargs):
            raise PersistenceUnavailableError("anomaly store failed")

    boom = BoomStore()
    for day, tag in ((JAN, "jan"), (FEB, "feb"), (MAR, "mar"), (APR, "apr")):
        _publish(boom, CLIENT_ID, _pair(day, a_cost="8.0000", b_cost="2.0000"), tag)
    _publish(boom, CLIENT_ID, _pair(MAY, a_cost="40.0000", b_cost="10.0000"), "may")
    assert _get(boom).status_code == 503


def test_d1_d6_regression() -> None:
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
    explorer = http.get(EXPLORER, headers=headers, params={"dimension": "campaign_id"}).json()
    assert explorer["rows"][0]["value"] == "40.0000"
    insights = http.get(INSIGHTS, headers=headers, params={"metric": "total_cost"}).json()
    assert any(item["metric"] == "total_cost" for item in insights["insights"])
    anomalies = http.get(ANOMALIES, headers=headers).json()
    assert anomalies["empty_reason"] == "insufficient_history"
    d3_http, *_rest = jwt_app(publication_store=_d3_store())
    d3 = d3_http.get(OVERVIEW, headers=jwt_headers("client")).json()
    assert _kpi_map(d3)["total_cost"]["value"] is not None


def test_classify_helpers_reject_ordinary_movement() -> None:
    spec = TREND_METRICS_BY_KEY["total_cost"]
    spike = classify_anomaly(spec, Decimal("50.0000"), [Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10")])
    assert spike is not None
    assert spike[1] == "spike"
    none = classify_anomaly(spec, Decimal("12.0000"), [Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10")])
    assert none is None
    with pytest.raises(FilterValidationError):
        parse_anomaly_metric("orders")
    with pytest.raises(FilterValidationError):
        parse_anomaly_dimension("brand")


def test_d7_anomaly_presentation_layer() -> None:
    """Anomaly cards read as exceptions: severity, direction, evidence, then the rule string."""

    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static"
    views = (root / "js" / "views.js").read_text(encoding="utf-8")
    css = (root / "css" / "app.css").read_text(encoding="utf-8")
    section = views[
        views.index("function overviewAnomaliesSection") : views.index("function askAboutButton")
    ]

    assert '<ol class="insight-grid finding-list" data-anomalies-list="true">' in section
    assert 'class="insight-card finding-card anomaly-card severity-${item.severity}' in section
    assert 'data-anomaly-severity="${item.severity}"' in section
    assert 'data-anomaly-direction="${direction}"' in section
    assert "ANOMALY_SEVERITY_LABELS[item.severity]" in section
    assert "ANOMALY_DIRECTION_LABELS[direction]" in section
    assert 'data-anomaly-context="true"' in section

    # Severity and direction are announced as words, not only as colour.
    assert '${item.severity}<' not in section
    assert "|| item.kind" not in section
    assert 'is-${direction}' in section
    assert ".anomaly-status.is-spike::before" in css
    assert ".anomaly-status.is-drop::before" in css

    # Headline first, evidence second, rule string behind a disclosure.
    assert section.index("${item.headline}") < section.index('class="insight-evidence is-secondary"')
    assert 'data-anomaly-rule="true"' in section
    assert section.count("${item.threshold}") == 1
    assert section.index('data-anomaly-rule="true"') < section.index("${item.threshold}")
    assert section.index('class="insight-evidence is-secondary"') < section.index("${item.threshold}")

    # Empty and count states use business wording instead of the raw reason token.
    assert "ANOMALY_EMPTY_STATUS[reason]" in section
    assert "|| anomalies.empty_reason" not in section

    # Existing drill/focus/ask contracts unchanged.
    assert 'data-anomaly-drill="${top.key}"' in section
    assert 'data-anomaly-select="${item.anomaly_id}"' in section
    assert 'class="btn-secondary finding-drill"' in section
    assert 'source: "anomaly"' in section

    labels = views[views.index("const ANOMALY_SEVERITY_LABELS") : views.index("function anomalyEmptyCopy")]
    for severity in ("high", "medium", "low"):
        assert f"{severity}:" in labels
    assert "spike:" in labels and "drop:" in labels

    assert ".anomaly-card.severity-high {" in css
    assert ".anomaly-card.severity-medium .anomaly-severity {" in css


def test_spa_has_anomalies_without_ask() -> None:
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    views = (root / "views.js").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    state = (root / "analytics-state.js").read_text(encoding="utf-8")
    section = views[
        views.index("function overviewAnomaliesSection") : views.index("function parentToken")
    ].lower()
    assert "data-overview-anomalies" in views
    assert "Anomalies" in views
    assert "getOverviewAnomalies" in app_js
    assert "anomaliesParamsFromQuery" in state
    assert "data-anomaly-drill" in views
    assert "chat" not in section
    assert "data-ask" in views
    assert "data-overview-ask" in views
    assert 'navItem("/admin", "Dashboard"' in (root / "components.js").read_text(encoding="utf-8")
    assert 'navItem("/client", "Reports"' in (root / "components.js").read_text(encoding="utf-8")
