"""D5 Performance Explorer: ranking, movers, contribution, isolation, D1–D4 regression."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from dfip_analytics.explorer import (
    MAX_EXPLORER_LIMIT,
    contribution_supported,
    parse_explorer_limit,
    parse_explorer_mode,
    validate_explorer_selection,
)
from dfip_analytics.filters import FilterValidationError
from dfip_analytics.kpis import compute_kpis
from dfip_analytics.trends import TREND_METRICS_BY_KEY
from dfip_api.errors import PersistenceUnavailableError
from dfip_api.publication_store import InMemoryPublicationStore

from test_d1_kpi_overview import CLIENT_B, OCT, OVERVIEW, _kpi_map, _publish
from test_d2_global_filters import _store as d2_store
from test_d3_dynamic_trends import TRENDS, _d3_store, _fact, _series
from test_d4_drilldown import DRILL
from test_p5_api import CLIENT_ID
from test_p9_authz import jwt_app, jwt_headers

EXPLORER = "/api/v1/analytics/explorer"


def _get(store, params=None, *, role: str = "client", client_id: str = CLIENT_ID):
    http, *_rest = jwt_app(publication_store=store)
    return http.get(EXPLORER, headers=jwt_headers(role, client_id), params=params or {})


def _row(body: dict, key: str) -> dict:
    return next(item for item in body["rows"] if item["key"] == key)


def test_campaign_and_channel_ranking() -> None:
    store = d2_store()
    campaigns = _get(store, {"dimension": "campaign_id"}).json()
    assert campaigns["selection"]["mode"] == "ranking"
    assert [item["key"] for item in campaigns["rows"]] == ["camp-a", "camp-b"]
    assert _row(campaigns, "camp-a")["value"] == "40.0000"
    assert _row(campaigns, "camp-a")["rank"] == 1
    assert _row(campaigns, "camp-b")["value"] == "20.0000"
    channels = _get(store, {"dimension": "channel"}).json()
    assert [item["key"] for item in channels["rows"]] == ["WhatsApp", "SMS"]
    assert _row(channels, "WhatsApp")["value"] == "40.0000"
    assert _row(channels, "SMS")["value"] == "20.0000"


def test_top_bottom_limit_and_sorting() -> None:
    store = d2_store()
    top = _get(store, {"dimension": "campaign_id", "mode": "top", "limit": 1}).json()
    assert [item["key"] for item in top["rows"]] == ["camp-a"]
    assert top["truncated"] is True
    assert top["result_count"] == 2
    bottom = _get(store, {"dimension": "campaign_id", "mode": "bottom", "limit": 1}).json()
    assert [item["key"] for item in bottom["rows"]] == ["camp-b"]
    ranked_asc = _get(
        store, {"dimension": "campaign_id", "mode": "ranking", "direction": "asc"}
    ).json()
    assert [item["key"] for item in ranked_asc["rows"]] == ["camp-b", "camp-a"]
    over = _get(store, {"dimension": "campaign_id", "limit": 0})
    assert over.status_code == 422
    too_big = _get(store, {"dimension": "campaign_id", "limit": MAX_EXPLORER_LIMIT + 1})
    assert too_big.status_code == 422


def test_movers_comparison_delta_and_no_comparison() -> None:
    store = d2_store()
    up = _get(store, {"dimension": "campaign_id", "mode": "movers", "mover": "up"}).json()
    assert [item["key"] for item in up["rows"]] == ["camp-a", "camp-b"]
    camp_a = _row(up, "camp-a")
    assert camp_a["prior_value"] == "10.0000"
    assert camp_a["delta"] == "30.0000"
    assert camp_a["delta_pct"] == "3.000000"
    down = _get(
        store,
        {
            "dimension": "campaign_id",
            "mode": "movers",
            "mover": "down",
            "month_start": "2025-06-01",
            "compare_month_start": "2025-10-01",
        },
    ).json()
    assert [item["key"] for item in down["rows"]] == ["camp-a", "camp-b"]
    assert _row(down, "camp-a")["delta"] == "-30.0000"
    none = _get(store, {"dimension": "campaign_id", "mode": "movers", "compare": "none"})
    assert none.status_code == 422
    ranking_none = _get(store, {"dimension": "campaign_id", "compare": "none"}).json()
    assert ranking_none["comparison"]["available"] is False
    assert _row(ranking_none, "camp-a")["prior_value"] is None
    assert _row(ranking_none, "camp-a")["delta"] is None


def test_contribution_efficiency_and_secondary() -> None:
    store = d2_store()
    cost = _get(store, {"metric": "total_cost", "dimension": "campaign_id"}).json()
    assert Decimal(_row(cost, "camp-a")["contribution_pct"]) == Decimal("0.666667")
    assert Decimal(_row(cost, "camp-b")["contribution_pct"]) == Decimal("0.333333")
    assert cost["selection"]["contribution_supported"] is True
    roas = _get(store, {"metric": "overall_roas", "dimension": "campaign_id"}).json()
    camp_a = _row(roas, "camp-a")
    expected = compute_kpis(
        {
            "total_cost": Decimal("40.0000"),
            "revenue_inr": Decimal("80.0000"),
            "sent": Decimal(100),
            "delivered": Decimal(80),
            "unique_clicks": Decimal(10),
            "unique_conversions": Decimal(2),
        },
        namespace="client",
    )
    assert camp_a["value"] == format(expected["overall_roas"], "f")
    assert roas["selection"]["contribution_supported"] is False
    assert camp_a["contribution_pct"] is None
    share = _get(
        store,
        {"metric": "overall_roas", "dimension": "campaign_id", "contribution": "share"},
    )
    assert share.status_code == 422
    ctr = _get(store, {"metric": "ctr_del_to_clicks", "dimension": "channel"}).json()
    assert ctr["metric"]["key"] == "ctr_del_to_clicks"
    pairs = _get(
        store,
        {"dimension": "campaign_id", "secondary": "channel"},
    ).json()
    assert pairs["selection"]["secondary"] == "channel"
    assert pairs["selection"]["max_depth"] == 2
    assert {item["key"] for item in pairs["rows"]} == {"WhatsApp", "SMS"}
    assert _row(pairs, "WhatsApp")["parent_key"] == "camp-a"
    assert _row(pairs, "WhatsApp")["value"] == "40.0000"
    assert _row(pairs, "WhatsApp")["drillable"] is True


def test_filters_invalid_inputs_empty_and_errors() -> None:
    store = d2_store()
    http, *_rest = jwt_app(publication_store=store)
    headers = jwt_headers("client")
    filtered = http.get(
        EXPLORER,
        headers=headers,
        params={"dimension": "campaign_id", "channel": "SMS", "filter_logic_1": "Group B"},
    ).json()
    assert filtered["applied"]["channels"] == ["SMS"]
    assert filtered["applied"]["filter_logic_1"] == ["Group B"]
    assert [item["key"] for item in filtered["rows"]] == ["camp-b"]
    assert filtered["rows"][0]["value"] == "20.0000"
    assert http.get(EXPLORER, headers=headers, params={"metric": "orders"}).status_code == 422
    assert http.get(EXPLORER, headers=headers, params={"dimension": "brand"}).status_code == 422
    assert (
        http.get(
            EXPLORER,
            headers=headers,
            params={"dimension": "campaign_id", "secondary": "campaign_id"},
        ).status_code
        == 422
    )
    assert http.get(EXPLORER, headers=headers, params={"mode": "anomalies"}).status_code == 422
    empty = http.get(
        EXPLORER,
        headers=headers,
        params={"period": "range", "day_from": "2025-07-01", "day_to": "2025-07-31"},
    ).json()
    assert empty["empty"] is True
    assert empty["rows"] == []

    class BoomStore(InMemoryPublicationStore):
        def list_published_history_groups(self, *args, **kwargs):
            raise PersistenceUnavailableError("explorer store failed")

    boom = BoomStore()
    from test_d1_kpi_overview import _fact as d1_fact

    _publish(boom, CLIENT_ID, [d1_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT)], "run-1")
    assert _get(boom).status_code == 503


def test_tenant_isolation_newest_wins_and_d1_d4_regression() -> None:
    http, *_rest = jwt_app(publication_store=d2_store())
    assert http.get(EXPLORER).status_code == 401
    denied = http.get(
        EXPLORER,
        headers=jwt_headers("client", CLIENT_ID),
        params={"client_id": CLIENT_B, "dimension": "campaign_id"},
    )
    assert denied.status_code == 403
    unbound = http.get(EXPLORER, headers=jwt_headers("publisher", None), params={"dimension": "campaign_id"})
    assert unbound.status_code == 403
    publisher = http.get(
        EXPLORER,
        headers=jwt_headers("publisher", CLIENT_ID),
        params={"dimension": "campaign_id"},
    )
    assert publisher.status_code == 200
    a_body = http.get(
        EXPLORER, headers=jwt_headers("client", CLIENT_ID), params={"dimension": "campaign_id"}
    ).json()
    b_body = http.get(
        EXPLORER, headers=jwt_headers("client", CLIENT_B), params={"dimension": "campaign_id"}
    ).json()
    assert "999.0000" not in str(a_body)
    assert _row(b_body, "camp-a")["value"] == "999.0000"

    wins = InMemoryPublicationStore()
    _publish(wins, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="10.0000")], "old")
    _publish(wins, CLIENT_ID, [_fact(client_id=CLIENT_ID, campaign_id="c1", day=OCT, total_cost="40.0000")], "new")
    assert _get(wins, {"dimension": "campaign_id"}).json()["rows"][0]["value"] == "40.0000"

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
    sms_overview = http.get(OVERVIEW, headers=headers, params={"channel": "SMS"}).json()
    assert _kpi_map(sms_overview)["total_cost"]["value"] == "20.0000"


def test_d3_store_delivery_rate_matches_sum_then_divide() -> None:
    body = _get(_d3_store(), {"metric": "delivery_rate", "dimension": "campaign_id"}).json()
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


def test_explorer_helpers_reject_invalid_modes() -> None:
    from dfip_analytics.drill import parse_drill_dimension

    metric = TREND_METRICS_BY_KEY["overall_roas"]
    campaign = parse_drill_dimension("campaign_id")
    with pytest.raises(FilterValidationError):
        parse_explorer_mode("anomalies")
    with pytest.raises(FilterValidationError):
        parse_explorer_limit(0, mode="ranking")
    assert contribution_supported(TREND_METRICS_BY_KEY["total_cost"]) is True
    assert contribution_supported(metric) is False
    with pytest.raises(FilterValidationError):
        validate_explorer_selection(
            metric,
            campaign,
            None,
            mode="ranking",
            sort="value",
            contribution="share",
            comparison_available=True,
            min_contribution=None,
        )


def test_d5_explorer_presentation_layer() -> None:
    """Explorer exposes its state as labels and makes rank/sort scannable."""

    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static"
    views = (root / "js" / "views.js").read_text(encoding="utf-8")
    css = (root / "css" / "app.css").read_text(encoding="utf-8")
    section = views[
        views.index("function overviewExplorerSection") : views.index("const INSIGHT_CATEGORY_LABELS")
    ]

    assert 'data-explorer-title="true"' in section
    assert "contextChips(" in section
    for term in ('"Metric"', '"Dimension"', '"Mode"', '"Sort"', '"Period"', '"Filters"', '"Share"'):
        assert term in section

    # Raw selection tokens survive only as form/URL state, never as rendered copy.
    assert "· ${mode}" not in section
    assert "· ${direction}" not in section
    assert "(${mover})" not in section
    assert "${mode}" not in section.split("<form")[0]
    assert section.count("${direction}") == 1
    assert 'name="ex_dir" value="${direction}"' in section
    assert "${mover}" not in section.replace('mover === "up"', "").replace('mover === "down"', "")
    assert "modeLabel" in section and "sortLabel" in section
    assert "directionLabel" in section and "moverLabel" in section
    assert "appliedFilterBits(applied)" in section
    helper = views[views.index("function appliedFilterBits") : views.index("export function overviewTrendSection")]
    assert "campaign(s)" not in helper
    assert "channel(s)" not in helper
    assert 'campaign${campaigns === 1 ? "" : "s"}' in helper

    # Sortable headers announce state; every header is scoped.
    assert "explorerSortHead({ query, sort, direction, column: \"value\", label: \"Value\" })" in section
    assert "explorerSortHead({ query, sort, direction, column: \"delta\", label: \"Delta\" })" in section
    assert "explorerSortHead({ query, sort, direction, column: \"contribution\", label: \"Share\" })" in section
    assert section.count("<th scope=\"col\"") == 3
    heads = views[views.index("function explorerSortState") : views.index("function explorerSortHref")]
    assert 'aria-sort="${state}"' in heads
    assert '"ascending"' in heads and '"descending"' in heads and '"none"' in heads
    assert 'class="sr-only"' in heads
    assert 'data-explorer-sort="${column}"' in heads

    # Existing drill/sort/contribution contracts unchanged.
    assert 'data-explorer-drill="${row.key}"' in section
    assert "withDrill(query, {" in section
    assert "explorerSortHref(query, column)" in heads
    assert "contribution_supported" in section
    assert 'data-explorer-drill-hint="true"' in section
    assert "Comparison is the D2 prior window, matched by dimension value. Missing comparison values are n/a, not zero." in section
    assert "No comparison for this ranking." in section

    assert ".explorer-sort:focus-visible {" in css
    assert '.explorer-table th[data-explorer-sort-active="true"]' in css
    assert ".explorer-affordance {\n  color: var(--accent);" in css


def test_spa_has_explorer_without_anomalies() -> None:
    root = Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "js"
    views = (root / "views.js").read_text(encoding="utf-8")
    app_js = (root / "app.js").read_text(encoding="utf-8")
    state = (root / "analytics-state.js").read_text(encoding="utf-8")
    assert "data-overview-explorer" in views
    assert "Performance Explorer" in views
    assert "queryFromExplorerForm" in state
    assert "explorerParamsFromQuery" in state
    assert "getOverviewExplorer" in app_js
    assert "withDrill" in views
    assert "data-explorer-drill" in views
    assert "data-overview-insights" in views
    assert "Insights" in views
    assert "data-overview-anomalies" in views
    assert "Anomalies" in views
    assert "data-ask" in views
    assert "data-overview-ask" in views
    assert 'navItem("/admin", "Dashboard"' in (root / "components.js").read_text(encoding="utf-8")
    assert 'navItem("/client", "Reports"' in (root / "components.js").read_text(encoding="utf-8")
