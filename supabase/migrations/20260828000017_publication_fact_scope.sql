-- Additive publication.fact_scope. Existing rows stay processing_run.
-- Does not rewrite facts, drop processing_run_id, or move publication_current.

ALTER TABLE publication
    ADD COLUMN IF NOT EXISTS fact_scope text NOT NULL DEFAULT 'processing_run';

DO $$
BEGIN
    ALTER TABLE publication
        ADD CONSTRAINT publication_fact_scope_check
        CHECK (fact_scope IN ('processing_run', 'client_current'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN publication.fact_scope IS
    'processing_run: published slice is current facts for publication.processing_run_id. client_current: all current facts for the client, clipped by period_start/period_end.';

CREATE OR REPLACE VIEW published_fact_campaign_day AS
SELECT
    f.client_id,
    f.campaign_id,
    f.variation_id,
    f.variation_id_key,
    f.day,
    f.month_start,
    f.month_label,
    f.campaign_name,
    f.variation_name,
    f.channel,
    f.type_of_campaign,
    f.start_date,
    f.template_name_whatsapp,
    f.sent,
    f.failed,
    f.delivered,
    f.unique_impressions,
    f.unique_clicks,
    f.unique_conversions,
    f.unique_impression_through_conversions,
    f.unique_click_through_conversions,
    f.revenue_inr,
    f.impression_through_revenue_inr,
    f.click_through_revenue_inr,
    f.filter_logic_1,
    f.filter_logic_2,
    f.template_status,
    f.amc_status_filter_logic_3,
    f.amc_device_category_filter_logic_4,
    f.amc_product_cat_filter_logic_5,
    f.manual_or_automated,
    f.total_cost,
    f.hhh,
    f.filter_logic_1_group,
    f.label_match_status,
    f.template_match_status,
    f.rate_card_rule_id,
    f.processing_run_id,
    f.batch_id,
    f.campaign_label_version_id,
    f.template_label_version_id,
    f.rate_card_version_id,
    f.label_group_version_id,
    f.first_seen_at,
    f.last_seen_at
FROM publication_current AS pc
JOIN publication AS p ON p.id = pc.publication_id
JOIN fact_campaign_day AS f
    ON f.client_id = p.client_id
    AND (
        p.fact_scope = 'client_current'
        OR f.processing_run_id = p.processing_run_id
    )
WHERE dfip_member_client(p.client_id)
    AND f.day IS NOT NULL
    AND (p.period_start IS NULL OR f.day >= p.period_start)
    AND (p.period_end IS NULL OR f.day <= p.period_end);

COMMENT ON VIEW published_fact_campaign_day IS
    'Published slice: publication_current -> publication -> fact_campaign_day. fact_scope processing_run keeps the run match; client_current uses current client grains. Tenant filter uses request GUCs. Not PostgREST.';

GRANT SELECT ON TABLE published_fact_campaign_day TO dfip_api;
GRANT SELECT ON TABLE published_fact_campaign_day TO dfip_worker;
