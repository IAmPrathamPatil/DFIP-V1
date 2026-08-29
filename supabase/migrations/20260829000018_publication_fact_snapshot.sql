-- Additive immutable publication snapshots.
-- Does not rewrite facts, drop processing_run_id, drop fact_campaign_day_history,
-- delete publications, or move publication_current.
-- Do not apply this file to the live August database from this workflow.

ALTER TABLE publication
    ADD COLUMN IF NOT EXISTS snapshot_status text NOT NULL DEFAULT 'none';

ALTER TABLE publication
    ADD COLUMN IF NOT EXISTS snapshot_row_count integer;

DO $$
BEGIN
    ALTER TABLE publication
        ADD CONSTRAINT publication_snapshot_status_check
        CHECK (snapshot_status IN ('none', 'complete'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE publication
        ADD CONSTRAINT publication_id_client_key UNIQUE (id, client_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN unique_violation THEN NULL;
END $$;

COMMENT ON COLUMN publication.snapshot_status IS
    'none: legacy publication without a snapshot; published reads may follow live fact_campaign_day. complete: publication_fact rows are the immutable published set.';
COMMENT ON COLUMN publication.snapshot_row_count IS
    'Number of publication_fact rows written when snapshot_status is complete. NULL for legacy none.';

CREATE TABLE publication_fact (
    publication_id uuid NOT NULL,
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
    CONSTRAINT publication_fact_pkey
        PRIMARY KEY (publication_id, client_id, campaign_id, variation_id_key, day),
    CONSTRAINT publication_fact_publication_client_fkey
        FOREIGN KEY (publication_id, client_id)
        REFERENCES publication (id, client_id)
);

COMMENT ON TABLE publication_fact IS
    'Immutable published fact snapshot for one publication. Not the working-set fact_campaign_day table.';

CREATE INDEX publication_fact_pub_day_idx
    ON publication_fact (publication_id, day, campaign_id, variation_id_key);

CREATE INDEX publication_fact_client_idx
    ON publication_fact (client_id, publication_id);

ALTER TABLE publication_fact ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication_fact FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS publication_fact_select ON publication_fact;
CREATE POLICY publication_fact_select ON publication_fact
    FOR SELECT TO dfip_api, dfip_worker
    USING (dfip_member_client(client_id));

DROP POLICY IF EXISTS publication_fact_insert ON publication_fact;
CREATE POLICY publication_fact_insert ON publication_fact
    FOR INSERT TO dfip_api, dfip_worker
    WITH CHECK (dfip_inspector_client(client_id));

GRANT SELECT, INSERT ON TABLE publication_fact TO dfip_api;
GRANT SELECT, INSERT ON TABLE publication_fact TO dfip_worker;
GRANT ALL ON TABLE publication_fact TO dfip_migrator;

CREATE OR REPLACE VIEW published_fact_campaign_day AS
SELECT
    s.client_id,
    s.campaign_id,
    s.variation_id,
    s.variation_id_key,
    s.day,
    s.month_start,
    s.month_label,
    s.campaign_name,
    s.variation_name,
    s.channel,
    s.type_of_campaign,
    s.start_date,
    s.template_name_whatsapp,
    s.sent,
    s.failed,
    s.delivered,
    s.unique_impressions,
    s.unique_clicks,
    s.unique_conversions,
    s.unique_impression_through_conversions,
    s.unique_click_through_conversions,
    s.revenue_inr,
    s.impression_through_revenue_inr,
    s.click_through_revenue_inr,
    s.filter_logic_1,
    s.filter_logic_2,
    s.template_status,
    s.amc_status_filter_logic_3,
    s.amc_device_category_filter_logic_4,
    s.amc_product_cat_filter_logic_5,
    s.manual_or_automated,
    s.total_cost,
    s.hhh,
    s.filter_logic_1_group,
    s.label_match_status,
    s.template_match_status,
    s.rate_card_rule_id,
    s.processing_run_id,
    s.batch_id,
    s.campaign_label_version_id,
    s.template_label_version_id,
    s.rate_card_version_id,
    s.label_group_version_id,
    s.first_seen_at,
    s.last_seen_at
FROM publication_current AS pc
JOIN publication AS p ON p.id = pc.publication_id
JOIN publication_fact AS s ON s.publication_id = p.id AND s.client_id = p.client_id
WHERE p.snapshot_status = 'complete'
    AND dfip_member_client(p.client_id)
    AND s.day IS NOT NULL
UNION ALL
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
WHERE p.snapshot_status IS DISTINCT FROM 'complete'
    AND dfip_member_client(p.client_id)
    AND f.day IS NOT NULL
    AND (p.period_start IS NULL OR f.day >= p.period_start)
    AND (p.period_end IS NULL OR f.day <= p.period_end);

COMMENT ON VIEW published_fact_campaign_day IS
    'Published slice for publication_current. complete snapshots read publication_fact. Legacy none still joins live fact_campaign_day and is not immutable. Tenant filter uses request GUCs. Not PostgREST.';

GRANT SELECT ON TABLE published_fact_campaign_day TO dfip_api;
GRANT SELECT ON TABLE published_fact_campaign_day TO dfip_worker;
