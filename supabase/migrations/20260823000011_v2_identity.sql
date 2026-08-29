-- DFIP V2 Phase 1 — identity and membership.
-- Does not create a foreign key to Supabase auth.users.
-- Hosted identity-provider runtime is a later V2 phase.

CREATE TABLE app_user (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject text NOT NULL,
    is_platform_admin boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT app_user_subject_key UNIQUE (subject)
);

CREATE TABLE client_membership (
    user_id uuid NOT NULL REFERENCES app_user (id),
    client_id uuid NOT NULL REFERENCES client (id),
    role text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT client_membership_pkey PRIMARY KEY (user_id, client_id),
    CONSTRAINT client_membership_role_chk
        CHECK (role IN ('admin', 'publisher', 'reader', 'client'))
);

COMMENT ON TABLE app_user IS
    'Application identity keyed by JWT subject. No FK to auth.users in Phase 1.';
COMMENT ON COLUMN app_user.is_platform_admin IS
    'When true, the principal may access every client. Not a user-management product.';
COMMENT ON TABLE client_membership IS
    'Per-client role for an app_user. Source of truth for database-mode authorization.';

CREATE INDEX client_membership_client_idx
    ON client_membership (client_id);
