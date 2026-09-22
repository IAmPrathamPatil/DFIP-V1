"""P3-C2a: server-side per-bucket share on GET /analytics/trends breakdown."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dfip_api.publication_store import InMemoryPublicationStore

from test_d1_kpi_overview import _publish
from test_d3_dynamic_trends import (
    OCT_MON,
    OCT_NEXT_MON,
    OCT_WED,
    _d3_store,
    _fact,
    _get,
    _point,
    _series,
)
from test_p5_api import CLIENT_ID

ZERO = date(2025, 11, 3)


def _shares_for_bucket(body: dict, bucket: str) -> list[Decimal]:
    shares = []
    for item in body["series"]:
        point = _point(item, bucket)
        raw = point.get("bucket_share")
        if raw is None:
            continue
        shares.append(Decimal(raw))
    return shares


def test_breakdown_bucket_share_is_value_over_bucket_total() -> None:
    body = _get(_d3_store(), {"breakdown": "channel", "grain": "month"}).json()
    assert body["selection"]["breakdown"] == "channel"
    october = "2025-10-01"
    whatsapp = _point(_series(body, "WhatsApp"), october)
    sms = _point(_series(body, "SMS"), october)
    assert whatsapp["value"] == "30.0000"
    assert sms["value"] == "30.0000"
    assert Decimal(whatsapp["bucket_share"]) == Decimal("0.500000")
    assert Decimal(sms["bucket_share"]) == Decimal("0.500000")
    assert sum(_shares_for_bucket(body, october), Decimal("0")) == Decimal("1.000000")


def test_breakdown_bucket_shares_sum_to_one_when_total_positive() -> None:
    body = _get(_d3_store(), {"breakdown": "channel", "grain": "day"}).json()
    oct6 = _point(_series(body, "WhatsApp"), "2025-10-06")
    assert oct6["value"] == "10.0000"
    assert Decimal(oct6["bucket_share"]) == Decimal("1.000000")
    assert _point(_series(body, "SMS"), "2025-10-06").get("bucket_share") is None
    assert sum(_shares_for_bucket(body, "2025-10-06"), Decimal("0")) == Decimal("1.000000")


def test_zero_total_bucket_returns_null_shares() -> None:
    store = InMemoryPublicationStore()
    _publish(
        store,
        CLIENT_ID,
        [
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-a",
                day=ZERO,
                channel="WhatsApp",
                total_cost="0.0000",
            ),
            _fact(
                client_id=CLIENT_ID,
                campaign_id="camp-b",
                day=ZERO,
                channel="SMS",
                total_cost="0.0000",
            ),
        ],
        "run-zero",
    )
    body = _get(
        store,
        {
            "breakdown": "channel",
            "grain": "day",
            "period": "range",
            "day_from": "2025-11-03",
            "day_to": "2025-11-03",
            "compare": "none",
        },
    ).json()
    whatsapp = _point(_series(body, "WhatsApp"), "2025-11-03")
    sms = _point(_series(body, "SMS"), "2025-11-03")
    assert whatsapp["value"] == "0.0000"
    assert sms["value"] == "0.0000"
    assert whatsapp.get("bucket_share") is None
    assert sms.get("bucket_share") is None
    assert whatsapp.get("bucket_share") != "0"
    assert whatsapp.get("bucket_share") != "0.000000"


def test_breakdown_absolute_values_are_unchanged() -> None:
    store = _d3_store()
    campaigns = _get(store, {"breakdown": "campaign_id", "grain": "month"}).json()
    assert _point(_series(campaigns, "camp-a"), "2025-10-01")["value"] == "30.0000"
    assert _point(_series(campaigns, "camp-b"), "2025-10-01")["value"] == "30.0000"
    channels = _get(store, {"breakdown": "channel", "grain": "month"}).json()
    assert _point(_series(channels, "WhatsApp"), "2025-10-01")["value"] == "30.0000"
    assert _point(_series(channels, "SMS"), "2025-10-01")["value"] == "30.0000"
    day = _get(store, {"breakdown": "channel", "grain": "day"}).json()
    assert _point(_series(day, "WhatsApp"), "2025-10-06")["value"] == "10.0000"
    assert _point(_series(day, "SMS"), "2025-10-08")["value"] == "30.0000"
    assert _point(_series(day, "WhatsApp"), "2025-10-13")["value"] == "20.0000"


def test_non_breakdown_trend_omits_bucket_share() -> None:
    body = _get(_d3_store(), {"grain": "month"}).json()
    assert body["selection"]["breakdown"] is None
    point = _series(body)["points"][0]
    assert point["value"] == "60.0000"
    assert point["comparison_value"] == "10.0000"
    assert "bucket_share" not in point
    day = _get(_d3_store()).json()
    assert day["selection"]["breakdown"] is None
    assert _point(_series(day), "2025-10-06")["value"] == "10.0000"
    assert "bucket_share" not in _point(_series(day), "2025-10-06")
    assert _point(_series(day), OCT_MON.isoformat())["secondary_value"] is None
    dual = _get(_d3_store(), {"metric": "total_cost", "secondary": "delivered"}).json()
    dual_point = _point(_series(dual), OCT_WED.isoformat())
    assert dual_point["value"] == "30.0000"
    assert dual_point["secondary_value"] == 10
    assert "bucket_share" not in dual_point
    assert _point(_series(day), OCT_NEXT_MON.isoformat())["value"] == "20.0000"
