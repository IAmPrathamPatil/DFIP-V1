"""Feature 2 explicit-grain Insights and Anomalies coverage."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from dfip_analytics.filters import PeriodWindow
from dfip_api.analytics_service import _bucket_period, _bucket_slots
from dfip_api.publication_store import InMemoryPublicationStore

from test_d1_kpi_overview import _publish
from test_d2_global_filters import _fact
from test_d3_dynamic_trends import TRENDS, _d3_store
from test_d6_insights import INSIGHTS
from test_d7_anomaly_diagnostic import ANOMALIES
from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers


def _request(store, path, params=None, *, client_id=CLIENT_ID):
    http, *_ = jwt_app(publication_store=store)
    return http.get(path, headers=jwt_headers("client", client_id), params=params or {})


def _paired_month_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=date(2025, 6, 1),
                total_cost="10.0000",
            ),
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-b",
                day=date(2025, 6, 2),
                total_cost="10.0000",
            ),
        ],
        "feature2-jun",
    )
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=date(2025, 10, 1),
                total_cost="40.0000",
            ),
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-b",
                day=date(2025, 10, 2),
                total_cost="20.0000",
            ),
        ],
        "feature2-oct",
    )
    return store


def _anomaly_grain_store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    for month, value in ((1, "8.0000"), (2, "8.0000"), (3, "8.0000"), (4, "40.0000")):
        day = date(2025, month, 1)
        _publish(
            store,
            CLIENT_ID,
            [
                _fact(client_id=CLIENT_ID, campaign_id="camp-a", day=day, total_cost=value),
                _fact(client_id=CLIENT_ID, campaign_id="camp-b", day=day, total_cost="2.0000"),
            ],
            f"feature2-{month}",
        )
    return store


def test_insights_day_week_month_and_explicit_comparison_mapping() -> None:
    store = _paired_month_store()
    day = _request(store, INSIGHTS, {"grain": "day", "metric": "total_cost"}).json()
    assert day["grain"] == "day"
    assert any(item["period"]["month_label"] == "2025-10-01" for item in day["insights"])
    assert any(item["comparison"]["month_label"] == "2025-06-01" for item in day["insights"])
    assert all("current_value" in item and "delta" in item for item in day["insights"])

    week = _request(store, INSIGHTS, {"grain": "week", "metric": "total_cost"}).json()
    assert week["grain"] == "week"
    assert any(item["period"]["month_label"] == "2025-W40" for item in week["insights"])
    assert any(item["period"]["day_min"] == "2025-10-01" for item in week["insights"])
    assert any(item["comparison"]["month_label"] == "2025-W22" for item in week["insights"])

    month = _request(store, INSIGHTS, {"grain": "month", "metric": "total_cost"}).json()
    assert month["grain"] == "month"
    assert month["comparison"]["month_start"] == "2025-06-01"
    assert month["insights"]


def test_insights_filters_isolate_and_no_raw_facts() -> None:
    store = _paired_month_store()
    body = _request(
        store,
        INSIGHTS,
        {"grain": "day", "metric": "total_cost", "campaign_id": "camp-a"},
    ).json()
    assert body["applied"]["campaign_ids"] == ["camp-a"]
    assert "facts" not in body
    assert "camp-b" not in str(body["insights"])


def test_anomalies_day_week_month_use_prior_published_baselines() -> None:
    store = _anomaly_grain_store()
    day = _request(store, ANOMALIES, {"grain": "day", "metric": "total_cost"}).json()
    assert day["grain"] == "day"
    assert day["baseline"]["observation_count"] == 3
    assert any(item["direction"] == "spike" for item in day["anomalies"])
    assert any(item["drivers"] for item in day["anomalies"])

    week = _request(store, ANOMALIES, {"grain": "week", "metric": "total_cost"}).json()
    assert week["grain"] == "week"
    assert week["anomalies"]
    assert week["anomalies"][0]["period"]["month_label"] == "2025-W14"
    assert "2025-W14" in week["anomalies"][0]["headline"]
    assert "2025-04 is" not in week["anomalies"][0]["explanation"]

    month = _request(store, ANOMALIES, {"grain": "month", "metric": "total_cost"}).json()
    assert month["grain"] == "month"
    assert month["baseline"]["observation_count"] == 3
    assert month["anomalies"]


def test_anomalies_empty_insufficient_ratio_and_client_isolation() -> None:
    empty = _request(InMemoryPublicationStore(), ANOMALIES, {"grain": "day"}).json()
    assert empty["empty"] is True
    assert empty["empty_reason"] == "no_published_history"

    insufficient = _request(_paired_month_store(), ANOMALIES, {"grain": "day"}).json()
    assert insufficient["empty"] is True
    assert insufficient["empty_reason"] == "insufficient_history"

    zero = _anomaly_grain_store()
    for month in (1, 2, 3, 4):
        day = date(2026, month, 1)
        _publish(
            zero,
            CLIENT_ID,
            [
                _fact(
                    client_id=CLIENT_ID,
                    campaign_id="zero",
                    day=day,
                    sent=0,
                    delivered=0,
                    total_cost="20.0000",
                )
            ],
            f"feature2-zero-{month}",
        )
    ratio = _request(zero, ANOMALIES, {"grain": "day", "metric": "delivery_rate"}).json()
    assert all(item["metric"] != "delivery_rate" for item in ratio["anomalies"])

    isolated = _request(_d3_store(), INSIGHTS, {"grain": "day"}, client_id=CLIENT_ID)
    assert isolated.status_code == 200
    assert "999.0000" not in isolated.text


def test_trends_default_and_ui_selector_regression() -> None:
    trends = _request(_d3_store(), TRENDS).json()
    assert trends["selection"]["grain"] == "day"
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    views = (root / "views.js").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    state = (root / "analytics-state.js").read_text(encoding="utf-8")
    assert views.count("data-finding-grain") >= 1
    assert views.count("findingGrainSelector(query)") >= 3
    assert "finding_grain" in state
    assert "findingChanged" in app


def test_week_identity_is_iso_labelled_and_boundary_safe() -> None:
    current = PeriodWindow(
        grain="month",
        day_from=date(2025, 10, 1),
        day_to_exclusive=date(2025, 11, 1),
        month_start=date(2025, 10, 1),
        month_label="Oct-25",
    )
    prior = PeriodWindow(
        grain="month",
        day_from=date(2025, 8, 1),
        day_to_exclusive=date(2025, 9, 1),
        month_start=date(2025, 8, 1),
        month_label="Aug-25",
    )
    current_slots = _bucket_slots(current, "week")
    prior_slots = _bucket_slots(prior, "week")
    assert current_slots[0][0] == "2025-W40"
    assert current_slots[0][1] == date(2025, 9, 29)
    assert current_slots[0][1] != date(2025, 10, 1)
    assert set(current_slots).intersection(prior_slots)
    short = PeriodWindow(
        grain="range",
        day_from=date(2025, 8, 1),
        day_to_exclusive=date(2025, 8, 16),
        month_start=None,
        month_label=None,
    )
    assert len(current_slots) != len(_bucket_slots(short, "week"))
    boundary = _bucket_period(current, current_slots[0][1], "week", 3)
    assert boundary.month_label == "2025-W40"
    assert boundary.day_min == date(2025, 10, 1)
    assert boundary.day_max == date(2025, 10, 5)
