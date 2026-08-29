-- DFIP V2 Phase 1 — database roles, grants, RLS, and published-fact view.
-- HTTP authorization remains the API's responsibility. These policies are
-- defense in depth. No PostgREST grants. No passwords. No BYPASSRLS for
-- dfip_api. Hosted identity-provider runtime is a later V2 phase.

CREATE ROLE dfip_migrator NOLOGIN;
CREATE ROLE dfip_api NOLOGIN NOBYPASSRLS;
CREATE ROLE dfip_worker NOLOGIN NOBYPASSRLS;

COMMENT ON ROLE dfip_migrator IS
    'DDL role for applying migrations. Not an application login.';
COMMENT ON ROLE dfip_api IS
    'DML role used by the API after SET LOCAL ROLE. No BYPASSRLS.';
COMMENT ON ROLE dfip_worker IS
    'Prepared for a later worker runtime. Not used in Phase 1.';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO dfip_migrator;
GRANT USAGE ON SCHEMA public TO dfip_api;
GRANT USAGE ON SCHEMA public TO dfip_worker;

CREATE FUNCTION dfip_is_platform_admin() RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE
AS $$SELECT COALESCE(current_setting('dfip.platform_admin', true), '') = 'true'$$;

CREATE FUNCTION dfip_current_role() RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE
AS $$SELECT COALESCE(current_setting('dfip.role', true), '')$$;

CREATE FUNCTION dfip_client_ids() RETURNS text[]
LANGUAGE sql STABLE PARALLEL SAFE
AS $$SELECT COALESCE(string_to_array(NULLIF(COALESCE(current_setting('dfip.client_ids', true), ''), ''), ','), ARRAY[]::text[])$$;

CREATE FUNCTION dfip_client_allowed(p_client_id uuid) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE
AS $$SELECT p_client_id IS NOT NULL AND p_client_id::text = ANY (dfip_client_ids())$$;

CREATE FUNCTION dfip_inspector_client(p_client_id uuid) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE
AS $$SELECT dfip_current_role() IN ('admin', 'publisher') AND (dfip_is_platform_admin() OR dfip_client_allowed(p_client_id))$$;

CREATE FUNCTION dfip_member_client(p_client_id uuid) RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE
AS $$SELECT dfip_is_platform_admin() OR dfip_client_allowed(p_client_id)$$;

REVOKE ALL ON FUNCTION dfip_is_platform_admin() FROM PUBLIC;
REVOKE ALL ON FUNCTION dfip_current_role() FROM PUBLIC;
REVOKE ALL ON FUNCTION dfip_client_ids() FROM PUBLIC;
REVOKE ALL ON FUNCTION dfip_client_allowed(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION dfip_inspector_client(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION dfip_member_client(uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION dfip_is_platform_admin() TO dfip_api;
GRANT EXECUTE ON FUNCTION dfip_is_platform_admin() TO dfip_worker;
GRANT EXECUTE ON FUNCTION dfip_current_role() TO dfip_api;
GRANT EXECUTE ON FUNCTION dfip_current_role() TO dfip_worker;
GRANT EXECUTE ON FUNCTION dfip_client_ids() TO dfip_api;
GRANT EXECUTE ON FUNCTION dfip_client_ids() TO dfip_worker;
GRANT EXECUTE ON FUNCTION dfip_client_allowed(uuid) TO dfip_api;
GRANT EXECUTE ON FUNCTION dfip_client_allowed(uuid) TO dfip_worker;
GRANT EXECUTE ON FUNCTION dfip_inspector_client(uuid) TO dfip_api;
GRANT EXECUTE ON FUNCTION dfip_inspector_client(uuid) TO dfip_worker;
GRANT EXECUTE ON FUNCTION dfip_member_client(uuid) TO dfip_api;
GRANT EXECUTE ON FUNCTION dfip_member_client(uuid) TO dfip_worker;

CREATE VIEW published_fact_campaign_day AS
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
    AND f.processing_run_id = p.processing_run_id
WHERE dfip_member_client(p.client_id)
    AND f.day IS NOT NULL
    AND (p.period_start IS NULL OR f.day >= p.period_start)
    AND (p.period_end IS NULL OR f.day <= p.period_end);

COMMENT ON VIEW published_fact_campaign_day IS
    'Published slice: publication_current -> publication -> fact_campaign_day. Tenant filter uses request GUCs. Not PostgREST.';

ALTER TABLE client ENABLE ROW LEVEL SECURITY;
ALTER TABLE client FORCE ROW LEVEL SECURITY;
ALTER TABLE source_file ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_file FORCE ROW LEVEL SECURITY;
ALTER TABLE batch ENABLE ROW LEVEL SECURITY;
ALTER TABLE batch FORCE ROW LEVEL SECURITY;
ALTER TABLE processing_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_run FORCE ROW LEVEL SECURITY;
ALTER TABLE stg_source_row ENABLE ROW LEVEL SECURITY;
ALTER TABLE stg_source_row FORCE ROW LEVEL SECURITY;
ALTER TABLE stg_rejected_row ENABLE ROW LEVEL SECURITY;
ALTER TABLE stg_rejected_row FORCE ROW LEVEL SECURITY;
ALTER TABLE fact_campaign_day ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_campaign_day FORCE ROW LEVEL SECURITY;
ALTER TABLE fact_campaign_day_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_campaign_day_history FORCE ROW LEVEL SECURITY;
ALTER TABLE publication ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication FORCE ROW LEVEL SECURITY;
ALTER TABLE publication_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication_current FORCE ROW LEVEL SECURITY;
ALTER TABLE campaign_label_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_label_version FORCE ROW LEVEL SECURITY;
ALTER TABLE campaign_label_row ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_label_row FORCE ROW LEVEL SECURITY;
ALTER TABLE template_label_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE template_label_version FORCE ROW LEVEL SECURITY;
ALTER TABLE template_label_row ENABLE ROW LEVEL SECURITY;
ALTER TABLE template_label_row FORCE ROW LEVEL SECURITY;
ALTER TABLE rate_card_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_card_version FORCE ROW LEVEL SECURITY;
ALTER TABLE rate_card_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_card_rule FORCE ROW LEVEL SECURITY;
ALTER TABLE label_group_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE label_group_version FORCE ROW LEVEL SECURITY;
ALTER TABLE label_group_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE label_group_member FORCE ROW LEVEL SECURITY;
ALTER TABLE we_source_column ENABLE ROW LEVEL SECURITY;
ALTER TABLE we_source_column FORCE ROW LEVEL SECURITY;
ALTER TABLE kpi_definition ENABLE ROW LEVEL SECURITY;
ALTER TABLE kpi_definition FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;

CREATE POLICY client_select ON client
    FOR SELECT TO dfip_api, dfip_worker
    USING (dfip_member_client(id));

CREATE POLICY source_file_inspector ON source_file
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY batch_inspector ON batch
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY processing_run_inspector ON processing_run
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY stg_source_row_inspector ON stg_source_row
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY stg_rejected_row_inspector ON stg_rejected_row
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY fact_campaign_day_inspector ON fact_campaign_day
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY fact_campaign_day_history_inspector ON fact_campaign_day_history
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY publication_select ON publication
    FOR SELECT TO dfip_api, dfip_worker
    USING (dfip_member_client(client_id));

CREATE POLICY publication_write ON publication
    FOR INSERT TO dfip_api, dfip_worker
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY publication_current_select ON publication_current
    FOR SELECT TO dfip_api, dfip_worker
    USING (dfip_member_client(client_id));

CREATE POLICY publication_current_insert ON publication_current
    FOR INSERT TO dfip_api, dfip_worker
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY publication_current_update ON publication_current
    FOR UPDATE TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY campaign_label_version_inspector ON campaign_label_version
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY campaign_label_row_inspector ON campaign_label_row
    FOR ALL TO dfip_api, dfip_worker
    USING (
        EXISTS (
            SELECT 1 FROM campaign_label_version AS v
            WHERE v.id = campaign_label_row.version_id
                AND dfip_inspector_client(v.client_id)
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM campaign_label_version AS v
            WHERE v.id = campaign_label_row.version_id
                AND dfip_inspector_client(v.client_id)
        )
    );

CREATE POLICY template_label_version_inspector ON template_label_version
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY template_label_row_inspector ON template_label_row
    FOR ALL TO dfip_api, dfip_worker
    USING (
        EXISTS (
            SELECT 1 FROM template_label_version AS v
            WHERE v.id = template_label_row.version_id
                AND dfip_inspector_client(v.client_id)
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM template_label_version AS v
            WHERE v.id = template_label_row.version_id
                AND dfip_inspector_client(v.client_id)
        )
    );

CREATE POLICY rate_card_version_inspector ON rate_card_version
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY rate_card_rule_inspector ON rate_card_rule
    FOR ALL TO dfip_api, dfip_worker
    USING (
        EXISTS (
            SELECT 1 FROM rate_card_version AS v
            WHERE v.id = rate_card_rule.version_id
                AND dfip_inspector_client(v.client_id)
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM rate_card_version AS v
            WHERE v.id = rate_card_rule.version_id
                AND dfip_inspector_client(v.client_id)
        )
    );

CREATE POLICY label_group_version_inspector ON label_group_version
    FOR ALL TO dfip_api, dfip_worker
    USING (dfip_inspector_client(client_id))
    WITH CHECK (dfip_inspector_client(client_id));

CREATE POLICY label_group_member_inspector ON label_group_member
    FOR ALL TO dfip_api, dfip_worker
    USING (
        EXISTS (
            SELECT 1 FROM label_group_version AS v
            WHERE v.id = label_group_member.version_id
                AND dfip_inspector_client(v.client_id)
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM label_group_version AS v
            WHERE v.id = label_group_member.version_id
                AND dfip_inspector_client(v.client_id)
        )
    );

CREATE POLICY we_source_column_select ON we_source_column
    FOR SELECT TO dfip_api, dfip_worker
    USING (true);

CREATE POLICY kpi_definition_select ON kpi_definition
    FOR SELECT TO dfip_api, dfip_worker
    USING (true);

CREATE POLICY audit_log_select ON audit_log
    FOR SELECT TO dfip_api, dfip_worker
    USING (
        dfip_is_platform_admin()
        OR (client_id IS NOT NULL AND dfip_inspector_client(client_id))
    );

CREATE POLICY audit_log_insert ON audit_log
    FOR INSERT TO dfip_api, dfip_worker
    WITH CHECK (
        dfip_is_platform_admin()
        OR (client_id IS NOT NULL AND dfip_inspector_client(client_id))
    );

GRANT SELECT ON TABLE client TO dfip_api;
GRANT SELECT ON TABLE client TO dfip_worker;
GRANT SELECT ON TABLE we_source_column TO dfip_api;
GRANT SELECT ON TABLE we_source_column TO dfip_worker;
GRANT SELECT ON TABLE kpi_definition TO dfip_api;
GRANT SELECT ON TABLE kpi_definition TO dfip_worker;
GRANT SELECT ON TABLE app_user TO dfip_api;
GRANT SELECT ON TABLE app_user TO dfip_worker;
GRANT SELECT ON TABLE client_membership TO dfip_api;
GRANT SELECT ON TABLE client_membership TO dfip_worker;
GRANT SELECT ON TABLE campaign_label_version TO dfip_api;
GRANT SELECT ON TABLE campaign_label_version TO dfip_worker;
GRANT SELECT ON TABLE campaign_label_row TO dfip_api;
GRANT SELECT ON TABLE campaign_label_row TO dfip_worker;
GRANT SELECT ON TABLE template_label_version TO dfip_api;
GRANT SELECT ON TABLE template_label_version TO dfip_worker;
GRANT SELECT ON TABLE template_label_row TO dfip_api;
GRANT SELECT ON TABLE template_label_row TO dfip_worker;
GRANT SELECT ON TABLE rate_card_version TO dfip_api;
GRANT SELECT ON TABLE rate_card_version TO dfip_worker;
GRANT SELECT ON TABLE rate_card_rule TO dfip_api;
GRANT SELECT ON TABLE rate_card_rule TO dfip_worker;
GRANT SELECT ON TABLE label_group_version TO dfip_api;
GRANT SELECT ON TABLE label_group_version TO dfip_worker;
GRANT SELECT ON TABLE label_group_member TO dfip_api;
GRANT SELECT ON TABLE label_group_member TO dfip_worker;
GRANT SELECT ON TABLE published_fact_campaign_day TO dfip_api;
GRANT SELECT ON TABLE published_fact_campaign_day TO dfip_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE source_file TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE source_file TO dfip_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE batch TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE batch TO dfip_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE processing_run TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE processing_run TO dfip_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE stg_source_row TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE stg_source_row TO dfip_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE stg_rejected_row TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE stg_rejected_row TO dfip_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE fact_campaign_day TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE fact_campaign_day TO dfip_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE fact_campaign_day_history TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE fact_campaign_day_history TO dfip_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE publication TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE publication TO dfip_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE publication_current TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE publication_current TO dfip_worker;
GRANT SELECT, INSERT ON TABLE audit_log TO dfip_api;
GRANT SELECT, INSERT ON TABLE audit_log TO dfip_worker;

GRANT ALL ON TABLE app_user TO dfip_migrator;
GRANT ALL ON TABLE client_membership TO dfip_migrator;
GRANT ALL ON SCHEMA public TO dfip_migrator;
