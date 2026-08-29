-- DFIP V2 Phase 2A — QA findings and published-only reporting views.
-- Append-only. Does not edit migrations 01-13. Does not add KPI columns to
-- fact_campaign_day. Power BI must read rpt_* objects, never working-set tables.

CREATE TABLE qa_finding (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES client (id),
    processing_run_id uuid NOT NULL REFERENCES processing_run (id),
    batch_id uuid REFERENCES batch (id),
    rule_id text NOT NULL,
    severity text NOT NULL,
    status text NOT NULL,
    entity_type text NOT NULL,
    entity_key text NOT NULL,
    message text NOT NULL,
    diagnostics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT qa_finding_run_rule_entity_key UNIQUE (processing_run_id, rule_id, entity_key),
    CONSTRAINT qa_finding_severity_chk CHECK (severity IN ('error', 'warning', 'info')),
    CONSTRAINT qa_finding_status_chk CHECK (status IN ('detected', 'cleared'))
);

COMMENT ON TABLE qa_finding IS
    'Inspector-only QA results for a processing run. Not a Power BI source. Not published facts.';

CREATE INDEX qa_finding_client_run_idx ON qa_finding (client_id, processing_run_id);
CREATE INDEX qa_finding_client_rule_idx ON qa_finding (client_id, rule_id);
CREATE INDEX qa_finding_client_severity_idx ON qa_finding (client_id, severity);

CREATE TABLE rpt_dim_date (
    day date PRIMARY KEY,
    month_start date NOT NULL,
    month_label text NOT NULL,
    year integer NOT NULL,
    quarter integer NOT NULL,
    day_of_week integer NOT NULL,
    CONSTRAINT rpt_dim_date_quarter_chk CHECK (quarter BETWEEN 1 AND 4),
    CONSTRAINT rpt_dim_date_dow_chk CHECK (day_of_week BETWEEN 1 AND 7)
);

COMMENT ON TABLE rpt_dim_date IS
    'Static calendar 2020-01-01 through 2035-12-31. Not tenant-owned. month_label matches P4 MMM-YY.';
COMMENT ON COLUMN rpt_dim_date.day_of_week IS
    'ISO day of week: Monday = 1 through Sunday = 7';

INSERT INTO rpt_dim_date (day, month_start, month_label, year, quarter, day_of_week)
SELECT
    d::date,
    date_trunc('month', d)::date,
    (ARRAY['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])
        [EXTRACT(MONTH FROM d)::integer]
        || '-' || to_char(d, 'YY'),
    EXTRACT(YEAR FROM d)::integer,
    EXTRACT(QUARTER FROM d)::integer,
    EXTRACT(ISODOW FROM d)::integer
FROM generate_series(DATE '2020-01-01', DATE '2035-12-31', INTERVAL '1 day') AS d;

CREATE VIEW rpt_published_fact AS
SELECT
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
    pf.total_cost,
    pf.filter_logic_1,
    pf.filter_logic_2,
    pf.template_status,
    pf.amc_status_filter_logic_3,
    pf.amc_device_category_filter_logic_4,
    pf.amc_product_cat_filter_logic_5,
    pf.manual_or_automated,
    pf.hhh,
    pf.filter_logic_1_group,
    pf.label_match_status,
    pf.template_match_status,
    pf.processing_run_id,
    pf.batch_id,
    pf.first_seen_at,
    pf.last_seen_at
FROM published_fact_campaign_day AS pf;

COMMENT ON VIEW rpt_published_fact IS
    'BI fact grain from the published slice. Additive measures only. No row-level ratio columns.';

CREATE VIEW rpt_dim_client AS
SELECT
    c.id AS client_id,
    c.code,
    c.name,
    c.created_at
FROM client AS c
WHERE dfip_member_client(c.id);

COMMENT ON VIEW rpt_dim_client IS
    'Client slicer. Tenant filter uses request GUCs.';

CREATE VIEW rpt_dim_campaign AS
SELECT DISTINCT ON (pf.client_id, pf.campaign_id)
    pf.client_id,
    pf.campaign_id,
    pf.campaign_name,
    pf.channel,
    pf.type_of_campaign,
    pf.filter_logic_1,
    pf.filter_logic_2,
    pf.template_status,
    pf.amc_status_filter_logic_3,
    pf.amc_device_category_filter_logic_4,
    pf.amc_product_cat_filter_logic_5,
    pf.manual_or_automated,
    pf.filter_logic_1_group
FROM published_fact_campaign_day AS pf
ORDER BY pf.client_id, pf.campaign_id, pf.last_seen_at DESC, pf.day DESC;

COMMENT ON VIEW rpt_dim_campaign IS
    'Campaign dimension from published facts. amc_product_cat_filter_logic_5 is a slicer, not a product grain.';

CREATE VIEW rpt_dim_variation AS
SELECT DISTINCT ON (pf.client_id, pf.campaign_id, pf.variation_id_key)
    pf.client_id,
    pf.campaign_id,
    pf.variation_id,
    pf.variation_id_key,
    pf.variation_name
FROM published_fact_campaign_day AS pf
ORDER BY pf.client_id, pf.campaign_id, pf.variation_id_key, pf.last_seen_at DESC, pf.day DESC;

COMMENT ON VIEW rpt_dim_variation IS
    'Variation dimension from published facts. Empty variation_id_key is a valid OPEN-A6 key.';

ALTER TABLE qa_finding ENABLE ROW LEVEL SECURITY;
ALTER TABLE qa_finding FORCE ROW LEVEL SECURITY;

CREATE POLICY qa_finding_inspector ON qa_finding
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE qa_finding TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE qa_finding TO dfip_worker;
GRANT SELECT ON TABLE rpt_dim_date TO dfip_api;
GRANT SELECT ON TABLE rpt_dim_date TO dfip_worker;
GRANT SELECT ON TABLE rpt_published_fact TO dfip_api;
GRANT SELECT ON TABLE rpt_published_fact TO dfip_worker;
GRANT SELECT ON TABLE rpt_dim_client TO dfip_api;
GRANT SELECT ON TABLE rpt_dim_client TO dfip_worker;
GRANT SELECT ON TABLE rpt_dim_campaign TO dfip_api;
GRANT SELECT ON TABLE rpt_dim_campaign TO dfip_worker;
GRANT SELECT ON TABLE rpt_dim_variation TO dfip_api;
GRANT SELECT ON TABLE rpt_dim_variation TO dfip_worker;
