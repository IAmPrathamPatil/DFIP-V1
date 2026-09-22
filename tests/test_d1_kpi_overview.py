"""D1 Overview KPI contract: published-history aggregates, isolation, semantics."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dfip_analytics.kpis import compute_kpis
from dfip_analytics.overview import (
    HERO_KPIS,
    REASON_NO_HISTORY,
    REASON_NO_PRIOR,
    select_overview_months,
)
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.transform.fact import FactRecord
from fastapi.testclient import TestClient

from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers

CLIENT_B = "a0000000-0000-4000-8000-000000000002"
OVERVIEW = "/api/v1/analytics/overview"
JUN = date(2025, 6, 15)
JUN_START = date(2025, 6, 1)
OCT = date(2025, 10, 8)
OCT_START = date(2025, 10, 1)


def _fact(
    *,
    client_id: str,
    campaign_id: str,
    day: date,
    sent: int | None = 100,
    delivered: int | None = 80,
    unique_clicks: int | None = 10,
    unique_conversions: int | None = 2,
    total_cost: str | None = "40.0000",
    revenue_inr: str | None = "80.0000",
) -> FactRecord:
    return FactRecord(
        client_id=client_id,
        campaign_id=campaign_id,
        variation_id="var-1",
        variation_id_key="var-1",
        day=day,
        month_start=date(day.year, day.month, 1),
        sent=sent,
        delivered=delivered,
        unique_clicks=unique_clicks,
        unique_conversions=unique_conversions,
        total_cost=None if total_cost is None else Decimal(total_cost),
        revenue_inr=None if revenue_inr is None else Decimal(revenue_inr),
        processing_run_id="run-d1",
    )


def _publish(store: InMemoryPublicationStore, client_id: str, facts: list[FactRecord], run: str) -> None:
    store.create(
        client_id=client_id,
        processing_run_id=run,
        period_start=min(item.day for item in facts),
        period_end=max(item.day for item in facts),
        published_by="publisher@test",
        notes=None,
        snapshot_facts=facts,
    )


def _kpi_map(body: dict) -> dict:
    return {item["id"]: item for item in body["kpis"]}


def test_select_overview_months_uses_published_sequence_not_calendar() -> None:
    assert select_overview_months([]) == (None, None)
    assert select_overview_months([JUN_START]) == (JUN_START, None)
    current, prior = select_overview_months([JUN_START, OCT_START])
    assert current == OCT_START
    assert prior == JUN_START


def test_hero_kpi_set_is_exactly_eight() -> None:
    assert [item.id for item in HERO_KPIS] == [
        "total_cost",
        "revenue_inr",
        "overall_roas",
        "delivered",
        "unique_clicks",
        "unique_conversions",
        "delivery_rate",
        "ctr_del_to_clicks",
    ]


def test_unauthenticated_overview_is_401() -> None:
    client, *_rest = jwt_app()
    response = client.get(OVERVIEW)
    assert response.status_code == 401


def test_no_published_history_is_empty_not_invented() -> None:
    client, *_rest = jwt_app()
    response = client.get(OVERVIEW, headers=jwt_headers("client"))
    assert response.status_code == 200
    body = response.json()
    assert body["has_published_history"] is False
    assert body["period"] is None
    assert body["kpis"] == []
    assert body["comparison"] == {
        "available": False,
        "reason": REASON_NO_HISTORY,
        "grain": None,
        "month_start": None,
        "month_label": None,
        "day_min": None,
        "day_max": None,
        "grain_row_count": None,
    }


def test_one_published_month_defaults_latest_and_no_comparison() -> None:
    store = InMemoryPublicationStore()
    _publish(store, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT)], "run-1")
    client, *_rest = jwt_app(publication_store=store)
    body = client.get(OVERVIEW, headers=jwt_headers("client")).json()
    assert body["has_published_history"] is True
    assert body["period"]["month_start"] == "2025-10-01"
    assert body["period"]["month_label"] == "Oct-25"
    assert body["comparison"]["available"] is False
    assert body["comparison"]["reason"] == REASON_NO_PRIOR
    cards = _kpi_map(body)
    expected = compute_kpis(
        {
            "sent": Decimal(100),
            "delivered": Decimal(80),
            "unique_clicks": Decimal(10),
            "unique_conversions": Decimal(2),
            "total_cost": Decimal("40.0000"),
            "revenue_inr": Decimal("80.0000"),
        },
        namespace="client",
    )
    assert cards["total_cost"]["value"] == "40.0000"
    assert cards["revenue_inr"]["value"] == "80.0000"
    assert cards["delivered"]["value"] == 80
    assert cards["unique_clicks"]["value"] == 10
    assert cards["unique_conversions"]["value"] == 2
    assert cards["overall_roas"]["value"] == format(expected["overall_roas"], "f")
    assert cards["delivery_rate"]["value"] == format(expected["delivery_rate"], "f")
    assert cards["ctr_del_to_clicks"]["value"] == format(expected["ctr_del_to_clicks"], "f")
    assert cards["overall_roas"]["numerator"] == "80.0000"
    assert cards["overall_roas"]["denominator"] == "40.0000"
    assert cards["delivery_rate"]["numerator"] == 80
    assert cards["delivery_rate"]["denominator"] == 100
    assert cards["ctr_del_to_clicks"]["numerator"] == 10
    assert cards["ctr_del_to_clicks"]["denominator"] == 80
    assert cards["total_cost"]["prior_value"] is None
    assert cards["total_cost"]["delta"] is None


def test_multiple_months_compare_previous_published_month() -> None:
    store = InMemoryPublicationStore()
    _publish(store, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=JUN, total_cost="10.0000", revenue_inr="20.0000")], "run-jun")
    _publish(
        store,
        CLIENT_ID,
        [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="40.0000", revenue_inr="80.0000")],
        "run-oct",
    )
    client, *_rest = jwt_app(publication_store=store)
    body = client.get(OVERVIEW, headers=jwt_headers("client")).json()
    assert body["period"]["month_start"] == "2025-10-01"
    assert body["comparison"]["available"] is True
    assert body["comparison"]["month_start"] == "2025-06-01"
    assert body["comparison"]["month_label"] == "Jun-25"
    cards = _kpi_map(body)
    assert cards["total_cost"]["value"] == "40.0000"
    assert cards["total_cost"]["prior_value"] == "10.0000"
    assert cards["total_cost"]["delta"] == "30.0000"


def test_corrected_month_uses_newest_successful_publication() -> None:
    store = InMemoryPublicationStore()
    grain = _fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="10.0000", revenue_inr="10.0000")
    _publish(store, CLIENT_ID, [grain], "run-old")
    corrected = _fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="40.0000", revenue_inr="80.0000")
    _publish(store, CLIENT_ID, [corrected], "run-new")
    client, *_rest = jwt_app(publication_store=store)
    body = client.get(OVERVIEW, headers=jwt_headers("client")).json()
    cards = _kpi_map(body)
    assert body["period"]["grain_row_count"] == 1
    assert cards["total_cost"]["value"] == "40.0000"
    assert cards["revenue_inr"]["value"] == "80.0000"
    assert cards["total_cost"]["value"] != "50.0000"


def test_company_isolation_and_client_cannot_widen_scope() -> None:
    store = InMemoryPublicationStore()
    _publish(store, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="a1", day=OCT, total_cost="40.0000")], "run-a")
    _publish(store, CLIENT_B, [_fact(client_id=CLIENT_B, campaign_id="b1", day=OCT, total_cost="999.0000")], "run-b")
    client, *_rest = jwt_app(publication_store=store)
    a_body = client.get(OVERVIEW, headers=jwt_headers("client", CLIENT_ID)).json()
    b_body = client.get(OVERVIEW, headers=jwt_headers("client", CLIENT_B)).json()
    assert _kpi_map(a_body)["total_cost"]["value"] == "40.0000"
    assert _kpi_map(b_body)["total_cost"]["value"] == "999.0000"
    denied = client.get(
        OVERVIEW,
        headers=jwt_headers("client", CLIENT_ID),
        params={"client_id": CLIENT_B},
    )
    assert denied.status_code == 403
    assert "999.0000" not in denied.text
    publisher = client.get(OVERVIEW, headers=jwt_headers("publisher", CLIENT_ID))
    assert publisher.status_code == 200
    assert _kpi_map(publisher.json())["total_cost"]["value"] == "40.0000"


def test_ratio_zero_denominator_is_null_never_zero() -> None:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="c1",
                day=OCT,
                sent=0,
                delivered=0,
                unique_clicks=5,
                total_cost="0.0000",
                revenue_inr="12.0000",
            )
        ],
        "run-zero",
    )
    client, *_rest = jwt_app(publication_store=store)
    cards = _kpi_map(client.get(OVERVIEW, headers=jwt_headers("client")).json())
    assert cards["overall_roas"]["value"] is None
    assert cards["delivery_rate"]["value"] is None
    assert cards["ctr_del_to_clicks"]["value"] is None
    assert cards["overall_roas"]["numerator"] == "12.0000"
    assert cards["overall_roas"]["denominator"] == "0.0000"


def test_publisher_without_company_is_not_empty_data() -> None:
    from test_p5_api import JWT_SECRET, _encode_jwt

    client, *_rest = jwt_app()
    token = _encode_jwt(role="publisher")
    response = client.get(OVERVIEW, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTHORIZATION_FAILED"


def test_spa_has_overview_route_and_keeps_reports() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    app_js = (root / "app.js").read_text(encoding="utf-8")
    components = (root / "components.js").read_text(encoding="utf-8")
    views = (root / "views.js").read_text(encoding="utf-8")
    assert "/client/overview" in app_js
    assert 'name: "client-overview"' in app_js
    assert 'name: "admin-home"' in app_js
    assert 'navItem("/admin", "Dashboard"' in components
    assert 'navItem("/client", "Reports"' in components
    assert 'navItem("/client/overview", "Overview"' in components
    assert 'navItem("/client/studio", "Analytics Studio"' in components
    assert "export function clientOverviewView" in views
    assert 'title: "Reports"' in views
    assert "charts" not in views[views.index("export function clientOverviewView") : views.index("export function clientHomeView")].lower()
