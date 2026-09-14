-- Inspector company-registry writes under SET LOCAL ROLE dfip_api.
-- Additive only. Does not change client SELECT isolation.
-- Does not enable RLS on client_membership or app_user.
-- Does not grant dfip_app or dfip_worker.
-- UPDATE is membership-scoped: dfip_inspector_client requires an inspector
-- role and the row id in dfip.client_ids (the Python helper copies only
-- client_membership inspector ids for the bound user).

GRANT INSERT ON TABLE app_user TO dfip_api;
GRANT UPDATE ON TABLE client TO dfip_api;

CREATE POLICY client_inspector_update ON client
    FOR UPDATE TO dfip_api
    USING (dfip_inspector_client(id))
    WITH CHECK (dfip_inspector_client(id));
