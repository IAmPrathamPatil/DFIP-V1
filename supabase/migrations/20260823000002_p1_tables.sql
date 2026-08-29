-- DFIP V1 P1 — core tables
-- Contract source: M1.4 lock + M2.1 architecture.
-- This migration defines structure only. It does not ingest, transform, or calculate.

-- ---------------------------------------------------------------------------
-- Tenant placeholder (V1 is single-client; identity of the client is not a
-- locked business fact). Used as an FK root so later rows are scoped.
-- ---------------------------------------------------------------------------
CREATE TABLE client (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT client_code_key UNIQUE (code)
);

-- ---------------------------------------------------------------------------
-- Workbook column catalog: Excel letter + exact header <-> database name.
-- Covers all 67 columns A:BO. source_role distinguishes K:BO vs A:J.
-- ---------------------------------------------------------------------------
CREATE TABLE we_source_column (
    excel_position integer NOT NULL,
    excel_letter text NOT NULL,
    excel_header text NOT NULL,
    db_column text NOT NULL,
    source_role text NOT NULL,
    value_kind text NOT NULL,
    notes text,
    CONSTRAINT we_source_column_pkey PRIMARY KEY (excel_position),
    CONSTRAINT we_source_column_letter_key UNIQUE (excel_letter),
    CONSTRAINT we_source_column_header_key UNIQUE (excel_header),
    CONSTRAINT we_source_column_db_column_key UNIQUE (db_column),
    CONSTRAINT we_source_column_role_chk
        CHECK (source_role IN ('derived_excel', 'web_engage_source')),
    CONSTRAINT we_source_column_position_chk
        CHECK (excel_position >= 1 AND excel_position <= 67)
);

-- ---------------------------------------------------------------------------
-- Source file custody metadata. Bytes live in object storage later (P3).
-- ---------------------------------------------------------------------------
CREATE TABLE source_file (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES client (id),
    sha256 char(64) NOT NULL,
    original_filename text NOT NULL,
    byte_size bigint,
    storage_uri text,
    uploaded_by text,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    source_kind text NOT NULL,
    declared_period_start date,
    declared_period_end date,
    CONSTRAINT source_file_sha256_key UNIQUE (sha256),
    CONSTRAINT source_file_kind_chk
        CHECK (source_kind IN ('native_export', 'legacy_workbook'))
);

CREATE TABLE batch (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_file_id uuid NOT NULL REFERENCES source_file (id),
    client_id uuid NOT NULL REFERENCES client (id),
    status text NOT NULL,
    row_count_declared integer,
    row_count_staged integer,
    row_count_rejected integer,
    observed_day_min date,
    observed_day_max date,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error_summary text,
    CONSTRAINT batch_status_chk
        CHECK (status IN ('received', 'staged', 'validated', 'failed', 'processed'))
);

-- ---------------------------------------------------------------------------
-- Independently versioned configuration (three axes + FL1 grouping).
-- ---------------------------------------------------------------------------
CREATE TABLE campaign_label_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES client (id),
    version_label text NOT NULL,
    effective_from date,
    effective_to date,
    source_file_id uuid REFERENCES source_file (id),
    row_count integer,
    distinct_key_count integer,
    duplicate_key_count integer,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text,
    notes text,
    CONSTRAINT campaign_label_version_label_key UNIQUE (client_id, version_label),
    CONSTRAINT campaign_label_version_status_chk
        CHECK (status IN ('draft', 'active', 'superseded'))
);

-- Duplicate Campaign Name keys are ALLOWED. First-match-wins is represented
-- by row_order (source sheet row). There is intentionally NO unique constraint
-- on campaign_name or campaign_name_key.
CREATE TABLE campaign_label_row (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id uuid NOT NULL REFERENCES campaign_label_version (id),
    row_order integer NOT NULL,
    campaign_name text NOT NULL,
    campaign_name_key text GENERATED ALWAYS AS (lower(campaign_name)) STORED,
    campaign_id text,
    channel text,
    type_of_campaign text,
    journey_name text,
    journey_id text,
    filter_logic_1 text,
    filter_logic_2 text,
    unused_sheet_template_status text,
    amc_status_filter_logic_3 text,
    amc_device_category_filter_logic_4 text,
    amc_product_cat_filter_logic_5 text,
    manual_or_automated text,
    CONSTRAINT campaign_label_row_version_order_key UNIQUE (version_id, row_order),
    CONSTRAINT campaign_label_row_order_chk CHECK (row_order >= 1)
);

CREATE TABLE template_label_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES client (id),
    version_label text NOT NULL,
    effective_from date,
    effective_to date,
    source_file_id uuid REFERENCES source_file (id),
    row_count integer,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text,
    notes text,
    CONSTRAINT template_label_version_label_key UNIQUE (client_id, version_label),
    CONSTRAINT template_label_version_status_chk
        CHECK (status IN ('draft', 'active', 'superseded'))
);

-- Lookup key is Template Name (WhatsApp). Result is New Logic column R
-- (header "Rate"), not column I. Duplicate template names allowed; row_order
-- is first-match-wins precedence.
CREATE TABLE template_label_row (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id uuid NOT NULL REFERENCES template_label_version (id),
    row_order integer NOT NULL,
    template_name text NOT NULL,
    template_name_key text GENERATED ALWAYS AS (lower(template_name)) STORED,
    template_status text,
    CONSTRAINT template_label_row_version_order_key UNIQUE (version_id, row_order),
    CONSTRAINT template_label_row_order_chk CHECK (row_order >= 1)
);

CREATE TABLE rate_card_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES client (id),
    version_label text NOT NULL,
    effective_from date,
    effective_to date,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text,
    notes text,
    CONSTRAINT rate_card_version_label_key UNIQUE (client_id, version_label),
    CONSTRAINT rate_card_version_status_chk
        CHECK (status IN ('draft', 'active', 'superseded'))
);

-- Ordered rules. First matching priority wins. No rates are hard-coded in
-- CHECKs; they live as numeric data.
CREATE TABLE rate_card_rule (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id uuid NOT NULL REFERENCES rate_card_version (id),
    priority integer NOT NULL,
    match_field text NOT NULL,
    match_value text NOT NULL,
    match_mode text NOT NULL,
    rate numeric(12, 6) NOT NULL,
    applies_to_measure text NOT NULL,
    CONSTRAINT rate_card_rule_version_priority_key UNIQUE (version_id, priority),
    CONSTRAINT rate_card_rule_priority_chk CHECK (priority >= 1),
    CONSTRAINT rate_card_rule_match_field_chk
        CHECK (match_field IN ('template_status', 'channel')),
    CONSTRAINT rate_card_rule_match_mode_chk
        CHECK (match_mode IN ('equals_ci')),
    CONSTRAINT rate_card_rule_applies_chk
        CHECK (applies_to_measure IN ('delivered'))
);

-- Filter Logic 1_2 grouping lifted from the client pivot cache.
CREATE TABLE label_group_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES client (id),
    version_label text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    notes text,
    CONSTRAINT label_group_version_label_key UNIQUE (client_id, version_label),
    CONSTRAINT label_group_version_status_chk
        CHECK (status IN ('draft', 'active', 'superseded'))
);

CREATE TABLE label_group_member (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id uuid NOT NULL REFERENCES label_group_version (id),
    group_name text NOT NULL,
    filter_logic_1_value text NOT NULL,
    row_order integer NOT NULL,
    CONSTRAINT label_group_member_version_order_key UNIQUE (version_id, row_order),
    CONSTRAINT label_group_member_order_chk CHECK (row_order >= 1)
);

CREATE TABLE processing_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES batch (id),
    campaign_label_version_id uuid REFERENCES campaign_label_version (id),
    template_label_version_id uuid REFERENCES template_label_version (id),
    rate_card_version_id uuid REFERENCES rate_card_version (id),
    label_group_version_id uuid REFERENCES label_group_version (id),
    engine_version text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL,
    qa_verdict text,
    CONSTRAINT processing_run_status_chk
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed'))
);

-- ---------------------------------------------------------------------------
-- Staging: retain the arriving 57-column payload as JSONB keyed by exact
-- Excel header text. No coercion here.
-- ---------------------------------------------------------------------------
CREATE TABLE stg_source_row (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES batch (id),
    source_row_number integer NOT NULL,
    raw jsonb NOT NULL,
    campaign_id text,
    variation_id text,
    day date,
    CONSTRAINT stg_source_row_batch_row_key UNIQUE (batch_id, source_row_number),
    CONSTRAINT stg_source_row_number_chk CHECK (source_row_number >= 1)
);

CREATE TABLE stg_rejected_row (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES batch (id),
    source_row_number integer,
    raw jsonb,
    reason_code text NOT NULL,
    reason_detail text,
    rejected_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Fact grain: one row per campaign variation per calendar day.
-- Business key = (campaign_id, variation_id, day) scoped by client.
-- variation_id_key is NOT NULL so the key is expressible in PostgreSQL.
-- The application convention for blank Variation ID is OPEN-A6 (not decided
-- here; the column exists so a later decision can be stored).
-- Derived A:J columns are nullable; P1 does not compute them.
-- ---------------------------------------------------------------------------
CREATE TABLE fact_campaign_day (
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
    rate_card_rule_id uuid REFERENCES rate_card_rule (id),
    processing_run_id uuid REFERENCES processing_run (id),
    batch_id uuid REFERENCES batch (id),
    campaign_label_version_id uuid REFERENCES campaign_label_version (id),
    template_label_version_id uuid REFERENCES template_label_version (id),
    rate_card_version_id uuid REFERENCES rate_card_version (id),
    label_group_version_id uuid REFERENCES label_group_version (id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fact_campaign_day_pkey
        PRIMARY KEY (client_id, campaign_id, variation_id_key, day),
    CONSTRAINT fact_campaign_day_label_match_chk
        CHECK (
            label_match_status IS NULL
            OR label_match_status IN ('matched', 'blank_key', 'unmatched')
        ),
    CONSTRAINT fact_campaign_day_template_match_chk
        CHECK (
            template_match_status IS NULL
            OR template_match_status IN ('matched', 'blank_key', 'unmatched')
        )
);

CREATE TABLE fact_campaign_day_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
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
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    superseded_at timestamptz NOT NULL DEFAULT now(),
    superseded_by_run_id uuid REFERENCES processing_run (id)
);

CREATE TABLE publication (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES client (id),
    processing_run_id uuid NOT NULL REFERENCES processing_run (id),
    period_start date,
    period_end date,
    published_at timestamptz NOT NULL DEFAULT now(),
    published_by text,
    notes text
);

CREATE TABLE publication_current (
    client_id uuid PRIMARY KEY REFERENCES client (id),
    publication_id uuid NOT NULL REFERENCES publication (id),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- KPI registry: metadata only. No row-level generated KPI columns.
-- ---------------------------------------------------------------------------
CREATE TABLE kpi_definition (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace text NOT NULL,
    display_name text NOT NULL,
    slug text NOT NULL,
    formula text NOT NULL,
    numerator_measure text,
    denominator_measure text,
    operation text NOT NULL,
    is_linear boolean NOT NULL,
    solve_order integer NOT NULL,
    depends_on_kpi_id uuid REFERENCES kpi_definition (id),
    is_used_by_reports boolean NOT NULL DEFAULT true,
    notes text,
    CONSTRAINT kpi_definition_namespace_slug_key UNIQUE (namespace, slug),
    CONSTRAINT kpi_definition_namespace_name_key UNIQUE (namespace, display_name),
    CONSTRAINT kpi_definition_namespace_order_key UNIQUE (namespace, solve_order),
    CONSTRAINT kpi_definition_namespace_chk CHECK (namespace IN ('qa', 'client')),
    CONSTRAINT kpi_definition_operation_chk
        CHECK (operation IN ('divide', 'subtract', 'add')),
    CONSTRAINT kpi_definition_solve_order_chk CHECK (solve_order >= 1)
);

CREATE TABLE audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor text,
    action text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid,
    before jsonb,
    after jsonb,
    at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE campaign_label_row IS
    'New Logic A:M rows. Duplicate campaign_name allowed. Lookup uses lower(campaign_name) with no trim; first row_order wins.';
COMMENT ON COLUMN campaign_label_row.unused_sheet_template_status IS
    'New Logic column I headed Template Status. Recovered lookups do not use this column.';
COMMENT ON COLUMN campaign_label_row.campaign_name_key IS
    'lower(campaign_name) stored generated. No whitespace trim. Not unique.';
COMMENT ON TABLE template_label_row IS
    'New Logic Q:R. template_status is column R (header Rate), the value used by Total Cost.';
COMMENT ON COLUMN fact_campaign_day.variation_id_key IS
    'NOT NULL stand-in for Variation ID so the locked three-part key is a PostgreSQL PK. Blank-ID sentinel is OPEN-A6.';
COMMENT ON TABLE kpi_definition IS
    'Post-aggregation KPI metadata. Formulas must not be applied at row grain.';
