-- DAX-equivalent validation against the published reporting layer.
-- DIVIDE(SUM(num), SUM(den)) == SQL SUM(num) / NULLIF(SUM(den), 0)
-- Never average daily ratios. Never query working-set tables.

SELECT
    SUM(sent) AS sent,
    SUM(failed) AS failed,
    SUM(delivered) AS delivered,
    SUM(unique_impressions) AS impressions,
    SUM(unique_clicks) AS clicks,
    SUM(unique_conversions) AS conversions,
    SUM(unique_click_through_conversions) AS click_through_conversions,
    SUM(unique_impression_through_conversions) AS impression_through_conversions,
    SUM(revenue_inr) AS revenue,
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
FROM rpt_published_fact;

-- Aggregation-before-ratio proof (must not equal AVG of daily CTR when denominators differ):
SELECT
    day,
    SUM(unique_clicks)::numeric / NULLIF(SUM(delivered), 0) AS daily_ctr
FROM rpt_published_fact
GROUP BY day
ORDER BY day;

-- Unpublished / working-set leak checks (must be zero for a reader GUC session):
-- SELECT COUNT(*) FROM fact_campaign_day;
-- SELECT COUNT(*) FROM qa_finding;
