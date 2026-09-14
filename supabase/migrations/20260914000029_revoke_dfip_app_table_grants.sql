-- Production LOGIN dfip_app must not hold table privileges.
-- Password hashes live on app_user. Identity DML uses SET LOCAL ROLE dfip_api.
-- Host drift: a production LOGIN can accumulate SELECT on identity tables.
-- This revokes public table/sequence/function privileges if dfip_app exists.
-- Additive. Does not create dfip_app. Does not GRANT dfip_app.
-- Does not change RLS policies or dfip_api grants.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dfip_app') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM dfip_app;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM dfip_app;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM dfip_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE ALL ON TABLES FROM dfip_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE ALL ON SEQUENCES FROM dfip_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            REVOKE ALL ON FUNCTIONS FROM dfip_app;
    END IF;
END $$;
