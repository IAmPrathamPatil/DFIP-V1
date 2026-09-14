-- Inspector Create Company under SET LOCAL ROLE dfip_api.
-- Additive only. Does not change client SELECT isolation.
-- Does not enable RLS on client_membership.
-- Does not grant dfip_app or dfip_worker.
-- A new client.id is not yet in dfip.client_ids, so INSERT cannot use
-- dfip_member_client. Application authorization remains InspectorDep.

GRANT INSERT ON TABLE client TO dfip_api;
GRANT INSERT ON TABLE client_membership TO dfip_api;

CREATE POLICY client_inspector_insert ON client
    FOR INSERT TO dfip_api
    WITH CHECK (
        dfip_current_role() IN ('admin', 'publisher')
        OR dfip_is_platform_admin()
    );
