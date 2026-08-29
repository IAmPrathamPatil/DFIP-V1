"""Phase 2B: PostgreSQL rpt_* totals match post-aggregation KPI engine / DAX."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dfip_analytics.aggregate import sum_additive_measures
from dfip_analytics.divide import MONEY_SCALE, RATE_SCALE, safe_divide
from dfip_analytics.kpis import compute_kpis
from psycopg import connect
from psycopg.rows import dict_row

from postgres_support import (
    CLIENT_A,
    RUN_A,
    postgres_only,
    requires_postgres,
    sample_fact,
    seed_working_set,
)

pytestmark = [postgres_only, requires_postgres]


def _as_api(conn, *, role: str, client_ids: str):
    conn.execute("BEGIN")
    conn.execute("SET LOCAL ROLE dfip_api")
    conn.execute("SELECT set_config('dfip.role', %s, true)", (role,))
    conn.execute("SELECT set_config('dfip.client_ids', %s, true)", (client_ids,))
    conn.execute("SELECT set_config('dfip.platform_admin', %s, true)", ("false",))
    return conn


def _publish(pubs) -> None:
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="dev",
        notes=None,
    )


def test_sql_divide_matches_engine_and_not_average_of_daily_ctr(
    pg_conn, pg_stores, postgres_url
) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    day1 = sample_fact(
        unique_clicks=1,
        delivered=10,
        sent=12,
        failed=1,
        unique_impressions=8,
        unique_conversions=1,
        unique_click_through_conversions=1,
        unique_impression_through_conversions=0,
        revenue_inr=Decimal("10.0000"),
        total_cost=Decimal("2.0000"),
        click_through_revenue_inr=Decimal("4.0000"),
        impression_through_revenue_inr=Decimal("1.0000"),
    )
    day2 = sample_fact(
        day=date(2025, 8, 2),
        unique_clicks=1,
        delivered=90,
        sent=100,
        failed=5,
        unique_impressions=70,
        unique_conversions=3,
        unique_click_through_conversions=2,
        unique_impression_through_conversions=1,
        revenue_inr=Decimal("90.0000"),
        total_cost=Decimal("18.0000"),
        click_through_revenue_inr=Decimal("36.0000"),
        impression_through_revenue_inr=Decimal("9.0000"),
    )
    facts.upsert(day1)
    facts.upsert(day2)
    facts.upsert(
        sample_fact(
            campaign_id="camp-unpublished",
            day=date(2025, 8, 3),
            unique_clicks=999,
            delivered=999,
            processing_run_id=RUN_A,
        )
    )
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=date(2025, 8, 1),
        period_end=date(2025, 8, 2),
        published_by="dev",
        notes=None,
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        totals = conn.execute(
            """
            SELECT
                SUM(unique_clicks) AS clicks,
                SUM(delivered) AS delivered,
                SUM(unique_clicks)::numeric / NULLIF(SUM(delivered), 0) AS ctr,
                SUM(revenue_inr) / NULLIF(SUM(total_cost), 0) AS roas,
                COUNT(*) AS rows
            FROM rpt_published_fact
            """
        ).fetchone()
        daily = conn.execute(
            """
            SELECT SUM(unique_clicks)::numeric / NULLIF(SUM(delivered), 0) AS daily_ctr
            FROM rpt_published_fact
            GROUP BY day
            """
        ).fetchall()
        unpublished = conn.execute(
            """
            SELECT COUNT(*) AS n FROM rpt_published_fact WHERE campaign_id = 'camp-unpublished'
            """
        ).fetchone()
        working = conn.execute("SELECT COUNT(*) AS n FROM fact_campaign_day").fetchone()
        conn.execute("ROLLBACK")
    assert totals is not None
    assert int(totals["rows"]) == 2
    engine = compute_kpis(
        {
            "unique_clicks": Decimal("2"),
            "delivered": Decimal("100"),
            "revenue_inr": Decimal("100.0000"),
            "total_cost": Decimal("20.0000"),
            "sent": Decimal("112"),
            "failed": Decimal("6"),
            "unique_impressions": Decimal("78"),
            "unique_conversions": Decimal("4"),
            "unique_click_through_conversions": Decimal("3"),
            "unique_impression_through_conversions": Decimal("1"),
            "impression_through_revenue_inr": Decimal("10.0000"),
            "click_through_revenue_inr": Decimal("40.0000"),
        },
        namespace="qa",
    )
    assert engine["ctr"] == Decimal("0.020000")
    combined = Decimal(totals["ctr"]).quantize(RATE_SCALE)
    assert combined == engine["ctr"]
    assert engine["roas"] == Decimal("5.0000")
    daily_ctrs = [Decimal(row["daily_ctr"]) for row in daily]
    average = (daily_ctrs[0] + daily_ctrs[1]) / Decimal("2")
    assert combined != average.quantize(RATE_SCALE)
    assert combined == safe_divide(Decimal("2"), Decimal("100"), scale=RATE_SCALE)
    assert unpublished is not None
    assert int(unpublished["n"]) == 0
    assert working is not None
    assert int(working["n"]) == 0


def test_zero_denominator_sql_is_null(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    facts.upsert(sample_fact(unique_clicks=5, delivered=0, sent=0, total_cost=Decimal("0.0000")))
    _publish(pubs)
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        row = conn.execute(
            """
            SELECT
                SUM(unique_clicks)::numeric / NULLIF(SUM(delivered), 0) AS ctr,
                SUM(revenue_inr) / NULLIF(SUM(total_cost), 0) AS roas
            FROM rpt_published_fact
            """
        ).fetchone()
        conn.execute("ROLLBACK")
    assert row is not None
    assert row["ctr"] is None
    assert row["roas"] is None
    engine = compute_kpis(
        {
            "unique_clicks": Decimal("5"),
            "delivered": Decimal("0"),
            "revenue_inr": Decimal("0"),
            "total_cost": Decimal("0"),
            "sent": Decimal("0"),
            "failed": Decimal("0"),
            "unique_impressions": Decimal("0"),
            "unique_conversions": Decimal("0"),
            "unique_click_through_conversions": Decimal("0"),
            "unique_impression_through_conversions": Decimal("0"),
            "impression_through_revenue_inr": Decimal("0"),
            "click_through_revenue_inr": Decimal("0"),
        },
        namespace="qa",
    )
    assert engine["ctr"] is None
    assert engine["roas"] is None


KPI_SQL = """
SELECT
    SUM(sent) AS sent,
    SUM(failed) AS failed,
    SUM(delivered) AS delivered,
    SUM(unique_impressions) AS impressions,
    SUM(unique_clicks) AS clicks,
    SUM(unique_conversions) AS conversions,
    SUM(unique_click_through_conversions) AS uct,
    SUM(unique_impression_through_conversions) AS uit,
    SUM(revenue_inr) AS revenue,
    SUM(click_through_revenue_inr) AS ct_revenue,
    SUM(impression_through_revenue_inr) AS it_revenue,
    SUM(total_cost) AS total_cost,
    SUM(unique_clicks)::numeric / NULLIF(SUM(delivered), 0) AS ctr,
    SUM(unique_conversions)::numeric / NULLIF(SUM(delivered), 0) AS conversion_rate,
    SUM(revenue_inr) / NULLIF(SUM(total_cost), 0) AS roas,
    SUM(delivered)::numeric / NULLIF(SUM(sent), 0) AS delivery_rate,
    SUM(total_cost) / NULLIF(SUM(unique_click_through_conversions), 0) AS cost_conv,
    SUM(unique_click_through_conversions)::numeric
        / NULLIF(SUM(delivered), 0) AS click_through_conv_rate,
    SUM(unique_impression_through_conversions)::numeric
        / NULLIF(SUM(delivered), 0) AS view_through_conv_rate,
    SUM(unique_clicks)::numeric / NULLIF(SUM(unique_impressions), 0) AS ctr_impr_to_click,
    SUM(unique_impression_through_conversions)
        + SUM(unique_click_through_conversions) AS click_plus_view_conv,
    SUM(total_cost) / NULLIF(
        SUM(unique_impression_through_conversions)
        + SUM(unique_click_through_conversions),
        0
    ) AS cost_click_plus_view_conv,
    (
        SUM(unique_impression_through_conversions)
        + SUM(unique_click_through_conversions)
    )::numeric / NULLIF(SUM(delivered), 0) AS all_click_plus_view_conv_rate,
    SUM(sent) - SUM(failed) AS actual_sent,
    SUM(revenue_inr) / NULLIF(SUM(total_cost), 0) AS overall_roas
FROM rpt_published_fact
"""


def _seed_two_day_published(facts, pubs) -> None:
    facts.upsert(
        sample_fact(
            unique_clicks=1,
            delivered=10,
            sent=12,
            failed=1,
            unique_impressions=8,
            unique_conversions=1,
            unique_click_through_conversions=1,
            unique_impression_through_conversions=0,
            revenue_inr=Decimal("10.0000"),
            total_cost=Decimal("2.0000"),
            click_through_revenue_inr=Decimal("4.0000"),
            impression_through_revenue_inr=Decimal("1.0000"),
            variation_id=None,
            variation_id_key="",
        )
    )
    facts.upsert(
        sample_fact(
            campaign_id="camp-2",
            day=date(2025, 8, 2),
            unique_clicks=1,
            delivered=90,
            sent=100,
            failed=5,
            unique_impressions=70,
            unique_conversions=3,
            unique_click_through_conversions=2,
            unique_impression_through_conversions=1,
            revenue_inr=Decimal("90.0000"),
            total_cost=Decimal("18.0000"),
            click_through_revenue_inr=Decimal("36.0000"),
            impression_through_revenue_inr=Decimal("9.0000"),
        )
    )
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=date(2025, 8, 1),
        period_end=date(2025, 8, 2),
        published_by="dev",
        notes=None,
    )


def test_sql_kpis_match_engine_across_campaigns_and_empty_variation(
    pg_conn, pg_stores, postgres_url
) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    _seed_two_day_published(facts, pubs)
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        row = conn.execute(KPI_SQL).fetchone()
        campaigns = conn.execute(
            "SELECT campaign_id FROM rpt_dim_campaign ORDER BY campaign_id"
        ).fetchall()
        variations = conn.execute(
            "SELECT variation_id_key FROM rpt_dim_variation ORDER BY variation_id_key"
        ).fetchall()
        conn.execute("ROLLBACK")
    assert row is not None
    measures = {
        "unique_clicks": Decimal(row["clicks"]),
        "delivered": Decimal(row["delivered"]),
        "revenue_inr": Decimal(row["revenue"]),
        "total_cost": Decimal(row["total_cost"]),
        "sent": Decimal(row["sent"]),
        "failed": Decimal(row["failed"]),
        "unique_impressions": Decimal(row["impressions"]),
        "unique_conversions": Decimal(row["conversions"]),
        "unique_click_through_conversions": Decimal(row["uct"]),
        "unique_impression_through_conversions": Decimal(row["uit"]),
        "impression_through_revenue_inr": Decimal(row["it_revenue"]),
        "click_through_revenue_inr": Decimal(row["ct_revenue"]),
    }
    qa = compute_kpis(measures, namespace="qa")
    client = compute_kpis(measures, namespace="client")
    assert qa["ctr"] == Decimal("0.020000")
    assert Decimal(row["ctr"]).quantize(RATE_SCALE) == qa["ctr"]
    assert Decimal(row["conversion_rate"]).quantize(RATE_SCALE) == qa["conversion_rate"]
    assert Decimal(row["roas"]).quantize(MONEY_SCALE) == qa["roas"]
    assert Decimal(row["delivery_rate"]).quantize(RATE_SCALE) == client["delivery_rate"]
    assert Decimal(row["cost_conv"]).quantize(MONEY_SCALE) == qa["cost_conv"]
    assert (
        Decimal(row["click_through_conv_rate"]).quantize(RATE_SCALE) == qa["click_thou_conv_rate"]
    )
    assert (
        Decimal(row["view_through_conv_rate"]).quantize(RATE_SCALE) == qa["view_through_conv_rate"]
    )
    assert Decimal(row["ctr_impr_to_click"]).quantize(RATE_SCALE) == qa["ctr_impr_to_click"]
    assert Decimal(row["click_plus_view_conv"]) == qa["click_plus_view_conv"]
    assert (
        Decimal(row["cost_click_plus_view_conv"]).quantize(MONEY_SCALE)
        == qa["cost_click_plus_view_conv"]
    )
    assert (
        Decimal(row["all_click_plus_view_conv_rate"]).quantize(RATE_SCALE)
        == qa["all_click_plus_view_conv_rate"]
    )
    assert Decimal(row["actual_sent"]) == client["actual_sent"]
    assert Decimal(row["overall_roas"]).quantize(MONEY_SCALE) == client["overall_roas"]
    assert [item["campaign_id"] for item in campaigns] == ["camp-1", "camp-2"]
    keys = [item["variation_id_key"] for item in variations]
    assert "" in keys


def test_null_numerator_is_skipped_and_null_denominator_stays_null(
    pg_conn, pg_stores, postgres_url
) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    skip_null = sample_fact(
        unique_clicks=None,
        delivered=10,
        sent=10,
        failed=0,
        unique_impressions=8,
        unique_conversions=0,
        unique_click_through_conversions=0,
        unique_impression_through_conversions=0,
        revenue_inr=Decimal("0.0000"),
        total_cost=Decimal("1.0000"),
        click_through_revenue_inr=Decimal("0.0000"),
        impression_through_revenue_inr=Decimal("0.0000"),
    )
    counted = sample_fact(
        day=date(2025, 8, 2),
        unique_clicks=2,
        delivered=10,
        sent=10,
        failed=0,
        unique_impressions=8,
        unique_conversions=0,
        unique_click_through_conversions=0,
        unique_impression_through_conversions=0,
        revenue_inr=Decimal("0.0000"),
        total_cost=Decimal("1.0000"),
        click_through_revenue_inr=Decimal("0.0000"),
        impression_through_revenue_inr=Decimal("0.0000"),
    )
    facts.upsert(skip_null)
    facts.upsert(counted)
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=date(2025, 8, 1),
        period_end=date(2025, 8, 2),
        published_by="dev",
        notes=None,
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        skipped = conn.execute(
            """
            SELECT
                SUM(unique_clicks) AS clicks,
                SUM(delivered) AS delivered,
                SUM(unique_clicks)::numeric / NULLIF(SUM(delivered), 0) AS ctr
            FROM rpt_published_fact
            """
        ).fetchone()
        conn.execute("ROLLBACK")
    assert skipped is not None
    assert int(skipped["clicks"]) == 2
    assert int(skipped["delivered"]) == 20
    engine_skip = compute_kpis(sum_additive_measures([skip_null, counted]), namespace="qa")
    assert Decimal(skipped["ctr"]).quantize(RATE_SCALE) == engine_skip["ctr"]
    assert engine_skip["ctr"] == Decimal("0.100000")

    facts.upsert(
        sample_fact(
            campaign_id="camp-null-den",
            day=date(2025, 8, 3),
            unique_clicks=5,
            delivered=None,
            sent=5,
            failed=0,
            unique_impressions=None,
            unique_conversions=0,
            unique_click_through_conversions=0,
            unique_impression_through_conversions=0,
            revenue_inr=Decimal("1.0000"),
            total_cost=Decimal("0.0000"),
            click_through_revenue_inr=Decimal("0.0000"),
            impression_through_revenue_inr=Decimal("0.0000"),
        )
    )
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=date(2025, 8, 3),
        period_end=date(2025, 8, 3),
        published_by="dev",
        notes=None,
    )
    with connect(postgres_url, row_factory=dict_row) as conn:
        _as_api(conn, role="reader", client_ids=CLIENT_A)
        missing = conn.execute(
            """
            SELECT
                SUM(unique_clicks) AS clicks,
                SUM(delivered) AS delivered,
                SUM(unique_clicks)::numeric / NULLIF(SUM(delivered), 0) AS ctr,
                SUM(unique_clicks)::numeric / NULLIF(SUM(unique_impressions), 0) AS impr_ctr
            FROM rpt_published_fact
            """
        ).fetchone()
        conn.execute("ROLLBACK")
    assert missing is not None
    assert int(missing["clicks"]) == 5
    assert missing["delivered"] is None
    assert missing["ctr"] is None
    assert missing["impr_ctr"] is None
    null_den = compute_kpis(
        {
            "unique_clicks": Decimal("5"),
            "delivered": None,
            "unique_impressions": None,
            "revenue_inr": Decimal("1.0000"),
            "total_cost": Decimal("0.0000"),
            "sent": Decimal("5"),
            "failed": Decimal("0"),
            "unique_conversions": Decimal("0"),
            "unique_click_through_conversions": Decimal("0"),
            "unique_impression_through_conversions": Decimal("0"),
            "impression_through_revenue_inr": Decimal("0"),
            "click_through_revenue_inr": Decimal("0"),
        },
        namespace="qa",
    )
    assert null_den["ctr"] is None
    assert null_den["ctr_impr_to_click"] is None
