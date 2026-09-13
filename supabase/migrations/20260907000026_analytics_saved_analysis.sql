-- D9 saved analytical workspace state.
-- Configuration JSON only. Not a fact dump. Not a share token table.
-- Application authorization binds owner_subject + JWT client_id.
-- Identity metadata: no FORCE RLS (same pattern as excel_workbook_grant).

CREATE TABLE analytics_saved_analysis (
    id uuid NOT NULL,
    owner_subject text NOT NULL,
    user_id uuid,
    client_id uuid NOT NULL REFERENCES client (id),
    title text NOT NULL,
    state jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT analytics_saved_analysis_pkey PRIMARY KEY (id),
    CONSTRAINT analytics_saved_analysis_title_len CHECK (char_length(title) BETWEEN 1 AND 80)
);

COMMENT ON TABLE analytics_saved_analysis IS
    'Per-user saved Overview analytical state for one company. Stores filters/metric/view configuration, not published fact rows.';

CREATE INDEX analytics_saved_analysis_owner_client_idx
    ON analytics_saved_analysis (owner_subject, client_id, updated_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE analytics_saved_analysis TO dfip_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE analytics_saved_analysis TO dfip_worker;
GRANT ALL ON TABLE analytics_saved_analysis TO dfip_migrator;

-- Permanent company delete must wipe saved analyses so they cannot survive
-- as an orphaned private-analytics handle after the company is gone.
CREATE OR REPLACE FUNCTION dfip_delete_company(p_client_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    rec record;
    orphan_ids uuid[];
BEGIN
    SELECT id, code, COALESCE(lifecycle_status, 'active') AS lifecycle_status
    INTO rec
    FROM client
    WHERE id = p_client_id;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF rec.code = 'default' THEN
        RAISE EXCEPTION 'COMPANY_PROTECTED' USING ERRCODE = 'P0003';
    END IF;
    IF rec.lifecycle_status IS DISTINCT FROM 'inactive' THEN
        RAISE EXCEPTION 'COMPANY_ACTIVE' USING ERRCODE = 'P0001';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM processing_run
        WHERE client_id = p_client_id
          AND status IN ('pending', 'running')
    ) THEN
        RAISE EXCEPTION 'COMPANY_IN_FLIGHT' USING ERRCODE = 'P0002';
    END IF;

    SELECT COALESCE(array_agg(u.id), ARRAY[]::uuid[])
    INTO orphan_ids
    FROM app_user AS u
    JOIN client_membership AS m ON m.user_id = u.id
    WHERE m.client_id = p_client_id
      AND u.is_platform_admin = false
      AND (
          SELECT COUNT(*) FROM client_membership AS other
          WHERE other.user_id = u.id
      ) = 1;

    DELETE FROM analytics_saved_analysis WHERE client_id = p_client_id;
    DELETE FROM excel_workbook_grant WHERE client_id = p_client_id;
    DELETE FROM publication_current WHERE client_id = p_client_id;
    DELETE FROM publication_history_grain WHERE client_id = p_client_id;
    DELETE FROM publication_fact WHERE client_id = p_client_id;
    DELETE FROM publication WHERE client_id = p_client_id;
    DELETE FROM qa_finding WHERE client_id = p_client_id;
    DELETE FROM fact_campaign_day WHERE client_id = p_client_id;
    DELETE FROM fact_campaign_day_history WHERE client_id = p_client_id;
    DELETE FROM processing_run WHERE client_id = p_client_id;
    DELETE FROM stg_source_row WHERE client_id = p_client_id;
    DELETE FROM stg_rejected_row WHERE client_id = p_client_id;
    DELETE FROM batch WHERE client_id = p_client_id;
    DELETE FROM campaign_label_row
    WHERE version_id IN (SELECT id FROM campaign_label_version WHERE client_id = p_client_id);
    DELETE FROM label_group_member
    WHERE version_id IN (SELECT id FROM label_group_version WHERE client_id = p_client_id);
    DELETE FROM template_label_row
    WHERE version_id IN (SELECT id FROM template_label_version WHERE client_id = p_client_id);
    DELETE FROM rate_card_rule
    WHERE version_id IN (SELECT id FROM rate_card_version WHERE client_id = p_client_id);
    DELETE FROM campaign_label_version WHERE client_id = p_client_id;
    DELETE FROM label_group_version WHERE client_id = p_client_id;
    DELETE FROM template_label_version WHERE client_id = p_client_id;
    DELETE FROM rate_card_version WHERE client_id = p_client_id;
    DELETE FROM source_file WHERE client_id = p_client_id;
    DELETE FROM client_membership WHERE client_id = p_client_id;
    IF orphan_ids IS NOT NULL AND array_length(orphan_ids, 1) IS NOT NULL THEN
        DELETE FROM app_user
        WHERE id = ANY (orphan_ids)
          AND is_platform_admin = false
          AND NOT EXISTS (
              SELECT 1 FROM client_membership AS m
              WHERE m.user_id = app_user.id
          );
    END IF;
    DELETE FROM audit_log WHERE client_id = p_client_id;
    DELETE FROM client WHERE id = p_client_id;
    RETURN true;
END;
$$;

REVOKE ALL ON FUNCTION dfip_delete_company(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dfip_delete_company(uuid) TO dfip_api;

COMMENT ON FUNCTION dfip_delete_company(uuid) IS
    'Permanently delete one inactive company and its tenant-scoped rows, including saved analyses. No CASCADE.';
