-- P13F — company lifecycle status. Additive. Does not CASCADE, purge, or
-- change reporting views / RLS. Existing rows stay active.

ALTER TABLE client
    ADD COLUMN IF NOT EXISTS lifecycle_status text NOT NULL DEFAULT 'active';

ALTER TABLE client
    ADD COLUMN IF NOT EXISTS deactivated_at timestamptz;

ALTER TABLE client
    ADD COLUMN IF NOT EXISTS purge_eligible_after timestamptz;

DO $$
BEGIN
    ALTER TABLE client
        ADD CONSTRAINT client_lifecycle_status_chk
        CHECK (lifecycle_status IN ('active', 'inactive'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN client.lifecycle_status IS
    'Application-layer company lifecycle. active or inactive. Not a SQL RLS filter.';
COMMENT ON COLUMN client.deactivated_at IS
    'Set when the company is deactivated. Cleared on reactivate. Not a delete.';
COMMENT ON COLUMN client.purge_eligible_after IS
    'Frozen eligibility timestamp from the deactivation-time policy. NULL means not clock-eligible. Purge is never automatic.';
