-- Application password sign-in fields for app_user.
-- Additive only. Does not enable RLS on app_user.
-- Does not modify fact tables, publication_fact, or August data.
-- password_hash NULL means password sign-in is disabled for that user.

ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS password_hash text;

ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 1;

COMMENT ON COLUMN app_user.password_hash IS
    'PBKDF2 password verifier for application sign-in. NULL disables password sign-in.';
COMMENT ON COLUMN app_user.token_version IS
    'Incremented on sign-out so previously issued access tokens are rejected.';

GRANT UPDATE (token_version, updated_at) ON TABLE app_user TO dfip_api;
GRANT UPDATE (token_version, updated_at) ON TABLE app_user TO dfip_worker;
