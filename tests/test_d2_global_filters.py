"""D2 global filters on the Overview KPI contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dfip_analytics.filters import FilterValidationError, resolve_period
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.transform.fact import FactRecord

from test_d1_kpi_overview import CLIENT_B, OCT, OVERVIEW, _kpi_map, _publish
from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers

JUN = date(2025, 6, 15)
OCT_START = date(2025, 10, 1)


def _fact(
    *,
    client_id: str,
    campaign_id: str,
    day: date,
    campaign_name: str | None = None,
    channel: str | None = "WhatsApp",
    filter_logic_1: str | None = "Group A",
    filter_logic_1_group: str | None = "A",
    total_cost: str = "40.0000",
    revenue_inr: str = "80.0000",
    sent: int = 100,
    delivered: int = 80,
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
        unique_clicks=10,
        unique_conversions=2,
        total_cost=Decimal(total_cost),
        revenue_inr=Decimal(revenue_inr),
        processing_run_id="run-d2",
    )


def _store() -> InMemoryPublicationStore:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(client_id=CLIENT_ID, campaign_id="camp-a", day=JUN, total_cost="10.0000", revenue_inr="20.0000"),
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-b",
                day=JUN,
                channel="SMS",
                filter_logic_1="Group B",
                filter_logic_1_group="B",
                total_cost="5.0000",
                revenue_inr="5.0000",
            ),
        ],
        "run-jun",
    )
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(client_id=CLIENT_ID, campaign_id="camp-a", day=OCT, total_cost="40.0000"),
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-b",
                day=OCT,
                channel="SMS",
                filter_logic_1="Group B",
                filter_logic_1_group="B",
                total_cost="20.0000",
                revenue_inr="40.0000",
            ),
        ],
        "run-oct",
    )
    _publish(
        store,
        CLIENT_B,
        [_fact(client_id=CLIENT_B, campaign_id="camp-a", day=OCT, total_cost="999.0000")],
        "run-b",
    )
    return store


def test_default_state_is_latest_month_auto_comparison() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(OVERVIEW, headers=jwt_headers("client")).json()
    assert body["applied"]["is_default"] is True
    assert body["applied"]["period"] == "month"
    assert body["period"]["month_start"] == "2025-10-01"
    assert body["comparison"]["available"] is True
    assert body["comparison"]["month_start"] == "2025-06-01"
    assert _kpi_map(body)["total_cost"]["value"] == "60.0000"


def test_selecting_another_published_month_changes_kpis() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"month_start": "2025-06-01"},
    ).json()
    assert body["applied"]["is_default"] is False
    assert body["period"]["month_start"] == "2025-06-01"
    assert body["comparison"]["available"] is False
    assert _kpi_map(body)["total_cost"]["value"] == "15.0000"


def test_date_range_sums_published_days_only() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"period": "range", "day_from": "2025-06-01", "day_to": "2025-10-31"},
    ).json()
    assert body["period"]["grain"] == "range"
    assert _kpi_map(body)["total_cost"]["value"] == "75.0000"
    assert body["comparison"]["available"] is False


def test_all_history_and_compare_none() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"period": "all_history", "compare": "none"},
    ).json()
    assert body["period"]["grain"] == "all_history"
    assert body["comparison"]["reason"] == "comparison_disabled"
    assert _kpi_map(body)["total_cost"]["value"] == "75.0000"


def test_explicit_compare_month() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"month_start": "2025-10-01", "compare_month_start": "2025-06-01"},
    ).json()
    assert body["comparison"]["month_start"] == "2025-06-01"
    assert _kpi_map(body)["total_cost"]["prior_value"] == "15.0000"


def test_compare_none_does_not_use_stale_compare_month() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    none = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"compare": "none", "compare_month_start": "2025-06-01"},
    ).json()
    assert none["applied"]["compare"] == "none"
    assert none["applied"]["compare_month_start"] is None
    assert none["comparison"]["available"] is False
    assert none["comparison"]["reason"] == "comparison_disabled"
    assert none["comparison"]["month_start"] is None
    cost = _kpi_map(none)["total_cost"]
    assert cost["value"] == "60.0000"
    assert cost["prior_value"] is None
    assert cost["delta"] is None
    auto = client.get(OVERVIEW, headers=jwt_headers("client")).json()
    assert auto["comparison"]["available"] is True
    assert auto["comparison"]["month_start"] == "2025-06-01"
    explicit = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"compare_month_start": "2025-06-01"},
    ).json()
    assert explicit["comparison"]["available"] is True
    assert explicit["comparison"]["month_start"] == "2025-06-01"
    back_to_none = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"compare": "none"},
    ).json()
    assert back_to_none["comparison"]["reason"] == "comparison_disabled"
    assert back_to_none["applied"]["compare"] == "none"


def test_campaign_and_channel_filters_combine() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params=[("campaign_id", "camp-a"), ("channel", "WhatsApp")],
    ).json()
    assert body["applied"]["campaign_ids"] == ["camp-a"]
    assert body["applied"]["channels"] == ["WhatsApp"]
    assert _kpi_map(body)["total_cost"]["value"] == "40.0000"
    assert _kpi_map(body)["total_cost"]["prior_value"] == "10.0000"


def test_filter_logic_narrows_kpis() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"filter_logic_1": "Group B"},
    ).json()
    assert _kpi_map(body)["total_cost"]["value"] == "20.0000"


def test_unknown_campaign_is_dropped_not_injected() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"campaign_id": "does-not-exist"},
    ).json()
    assert body["dropped_filters"]["campaign_id"] == ["does-not-exist"]
    assert body["applied"]["campaign_ids"] == []
    assert _kpi_map(body)["total_cost"]["value"] == "60.0000"


def test_unpublished_month_is_rejected() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    response = client.get(
        OVERVIEW,
        headers=jwt_headers("client"),
        params={"month_start": "2025-07-01"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_filters_cannot_widen_tenant() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    denied = client.get(
        OVERVIEW,
        headers=jwt_headers("client", CLIENT_ID),
        params={"client_id": CLIENT_B, "campaign_id": "camp-a"},
    )
    assert denied.status_code == 403
    own = client.get(
        OVERVIEW,
        headers=jwt_headers("client", CLIENT_ID),
        params={"campaign_id": "camp-a"},
    ).json()
    assert "999.0000" not in str(own)
    assert _kpi_map(own)["total_cost"]["value"] == "40.0000"


def test_d1_regression_without_filters() -> None:
    client, *_rest = jwt_app(publication_store=_store())
    body = client.get(OVERVIEW, headers=jwt_headers("client")).json()
    assert [item["id"] for item in body["kpis"]] == [
        "total_cost",
        "revenue_inr",
        "overall_roas",
        "delivered",
        "unique_clicks",
        "unique_conversions",
        "delivery_rate",
        "ctr_del_to_clicks",
    ]
    assert body["options"]["months"][-1]["month_start"] == "2025-10-01"
    assert {item["value"] for item in body["options"]["campaigns"]} == {"camp-a", "camp-b"}


def test_resolve_period_rejects_unknown_grain() -> None:
    try:
        resolve_period(
            months=[OCT_START],
            published_min=OCT,
            published_max=OCT,
            period="week",
            month_start=None,
            day_from=None,
            day_to=None,
        )
    except FilterValidationError as exc:
        assert "period" in str(exc).lower()
    else:
        raise AssertionError("expected FilterValidationError")


def test_spa_has_shared_filter_state() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    state = (root / "analytics-state.js").read_text(encoding="utf-8")
    views = (root / "views.js").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    assert "overviewParamsFromQuery" in state
    assert "queryFromTrendForm" in state
    assert "data-overview-filters" in views
    assert "Clear all" in views
    assert "queryFromOverviewForm" in app_js
    assert "data-overview-filters" in views
    assert "data-overview-trend" in views
    assert 'compare === "none" || compareMonth === "none"' in state
    assert 'delete next.compare_month_start' in state
    assert 'value="none"' in views
