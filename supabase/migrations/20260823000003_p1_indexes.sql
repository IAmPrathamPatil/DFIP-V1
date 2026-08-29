-- DFIP V1 P1 — indexes for known lookup / query patterns.
-- Unique constraints already created supporting indexes in 000002.

CREATE INDEX campaign_label_row_lookup_idx
    ON campaign_label_row (version_id, campaign_name_key, row_order);

CREATE INDEX template_label_row_lookup_idx
    ON template_label_row (version_id, template_name_key, row_order);

CREATE INDEX rate_card_rule_version_priority_idx
    ON rate_card_rule (version_id, priority);

CREATE INDEX label_group_member_lookup_idx
    ON label_group_member (version_id, filter_logic_1_value);

CREATE INDEX batch_client_status_idx
    ON batch (client_id, status);

CREATE INDEX processing_run_batch_idx
    ON processing_run (batch_id);

CREATE INDEX stg_source_row_batch_idx
    ON stg_source_row (batch_id);

CREATE INDEX stg_source_row_key_idx
    ON stg_source_row (campaign_id, variation_id, day);

CREATE INDEX stg_rejected_row_batch_idx
    ON stg_rejected_row (batch_id);

CREATE INDEX fact_campaign_day_day_idx
    ON fact_campaign_day (client_id, day);

CREATE INDEX fact_campaign_day_batch_idx
    ON fact_campaign_day (batch_id);

CREATE INDEX fact_campaign_day_run_idx
    ON fact_campaign_day (processing_run_id);

CREATE INDEX fact_campaign_day_channel_idx
    ON fact_campaign_day (client_id, channel);

CREATE INDEX fact_history_key_idx
    ON fact_campaign_day_history (client_id, campaign_id, variation_id_key, day);

CREATE INDEX publication_client_idx
    ON publication (client_id, published_at DESC);

CREATE INDEX audit_log_at_idx
    ON audit_log (at);

CREATE INDEX kpi_definition_namespace_idx
    ON kpi_definition (namespace, solve_order);
