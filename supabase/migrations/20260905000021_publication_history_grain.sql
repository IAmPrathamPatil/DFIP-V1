-- Newest-wins cumulative published history serving table.
-- Same logical result as DISTINCT ON (campaign_id, variation_id_key, day)
-- ordered by published_at DESC, id DESC across complete publication snapshots.
-- Not the current-pointer view published_fact_campaign_day.
-- Does not rewrite publication_fact, publication_current, or working-set facts.

CREATE TABLE publication_history_grain (
    client_id uuid NOT NULL REFERENCES client (id),
    campaign_id text NOT NULL,
    variation_id text,
    variation_id_key text NOT NULL,
    day date NOT NULL,
    month_start date,
    month_label text,
    campaign_name text,
    variation_name text,
    channel text,
    type_of_campaign text,
    start_date timestamptz,
    template_name_whatsapp text,
    sent bigint,
    failed bigint,
    delivered bigint,
    unique_impressions bigint,
    unique_clicks bigint,
    unique_conversions bigint,
    unique_impression_through_conversions bigint,
    unique_click_through_conversions bigint,
    revenue_inr numeric(18, 4),
    impression_through_revenue_inr numeric(18, 4),
    click_through_revenue_inr numeric(18, 4),
    filter_logic_1 text,
    filter_logic_2 text,
    template_status text,
    amc_status_filter_logic_3 text,
    amc_device_category_filter_logic_4 text,
    amc_product_cat_filter_logic_5 text,
    manual_or_automated text,
    total_cost numeric(18, 4),
    hhh text,
    filter_logic_1_group text,
    label_match_status text,
    template_match_status text,
    rate_card_rule_id uuid,
    processing_run_id uuid,
    batch_id uuid,
    campaign_label_version_id uuid,
    template_label_version_id uuid,
    rate_card_version_id uuid,
    label_group_version_id uuid,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    CONSTRAINT publication_history_grain_pkey
        PRIMARY KEY (client_id, campaign_id, variation_id_key, day)
);

COMMENT ON TABLE publication_history_grain IS
    'Per-tenant newest-wins union of complete publication_fact snapshots. Serving copy for GET /publications/history/facts. Rebuilt after a successful complete publish. Incomplete/none publications are excluded.';

CREATE INDEX publication_history_grain_page_idx
    ON publication_history_grain (client_id, day, campaign_id, variation_id_key);

ALTER TABLE publication_history_grain ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication_history_grain FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS publication_history_grain_select ON publication_history_grain;
CREATE POLICY publication_history_grain_select ON publication_history_grain
    FOR SELECT TO dfip_api, dfip_worker
    USING (dfip_member_client(client_id));

DROP POLICY IF EXISTS publication_history_grain_insert ON publication_history_grain;
CREATE POLICY publication_history_grain_insert ON publication_history_grain
    FOR INSERT TO dfip_api, dfip_worker
    WITH CHECK (dfip_inspector_client(client_id));

DROP POLICY IF EXISTS publication_history_grain_update ON publication_history_grain;
CREATE POLICY publication_history_grain_update ON publication_history_grain
    FOR UPDATE TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

DROP POLICY IF EXISTS publication_history_grain_delete ON publication_history_grain;
CREATE POLICY publication_history_grain_delete ON publication_history_grain
    FOR DELETE TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE publication_history_grain TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE publication_history_grain TO dfip_worker;
GRANT ALL ON TABLE publication_history_grain TO dfip_migrator;

INSERT INTO publication_history_grain (
    client_id,
    campaign_id,
    variation_id,
    variation_id_key,
    day,
    month_start,
    month_label,
    campaign_name,
    variation_name,
    channel,
    type_of_campaign,
    start_date,
    template_name_whatsapp,
    sent,
    failed,
    delivered,
    unique_impressions,
    unique_clicks,
    unique_conversions,
    unique_impression_through_conversions,
    unique_click_through_conversions,
    revenue_inr,
    impression_through_revenue_inr,
    click_through_revenue_inr,
    filter_logic_1,
    filter_logic_2,
    template_status,
    amc_status_filter_logic_3,
    amc_device_category_filter_logic_4,
    amc_product_cat_filter_logic_5,
    manual_or_automated,
    total_cost,
    hhh,
    filter_logic_1_group,
    label_match_status,
    template_match_status,
    rate_card_rule_id,
    processing_run_id,
    batch_id,
    campaign_label_version_id,
    template_label_version_id,
    rate_card_version_id,
    label_group_version_id,
    first_seen_at,
    last_seen_at
)
SELECT DISTINCT ON (pf.client_id, pf.campaign_id, pf.variation_id_key, pf.day)
    pf.client_id,
    pf.campaign_id,
    pf.variation_id,
    pf.variation_id_key,
    pf.day,
    pf.month_start,
    pf.month_label,
    pf.campaign_name,
    pf.variation_name,
    pf.channel,
    pf.type_of_campaign,
    pf.start_date,
    pf.template_name_whatsapp,
    pf.sent,
    pf.failed,
    pf.delivered,
    pf.unique_impressions,
    pf.unique_clicks,
    pf.unique_conversions,
    pf.unique_impression_through_conversions,
    pf.unique_click_through_conversions,
    pf.revenue_inr,
    pf.impression_through_revenue_inr,
    pf.click_through_revenue_inr,
    pf.filter_logic_1,
    pf.filter_logic_2,
    pf.template_status,
    pf.amc_status_filter_logic_3,
    pf.amc_device_category_filter_logic_4,
    pf.amc_product_cat_filter_logic_5,
    pf.manual_or_automated,
    pf.total_cost,
    pf.hhh,
    pf.filter_logic_1_group,
    pf.label_match_status,
    pf.template_match_status,
    pf.rate_card_rule_id,
    pf.processing_run_id,
    pf.batch_id,
    pf.campaign_label_version_id,
    pf.template_label_version_id,
    pf.rate_card_version_id,
    pf.label_group_version_id,
    pf.first_seen_at,
    pf.last_seen_at
FROM publication_fact pf
INNER JOIN publication p
    ON p.id = pf.publication_id
   AND p.client_id = pf.client_id
WHERE p.snapshot_status = 'complete'
ORDER BY pf.client_id, pf.campaign_id, pf.variation_id_key, pf.day,
         p.published_at DESC, p.id DESC;
