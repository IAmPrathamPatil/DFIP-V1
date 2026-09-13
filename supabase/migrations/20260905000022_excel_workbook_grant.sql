-- Client/report-scoped Excel workbook grants.
-- jti of the JWT stamped into Settings BearerToken. Access JWT exp stays short.
-- After access exp, POST /auth/refresh may mint a new short-lived access JWT
-- only while this grant is unexpired and unrevoked. Not a publisher/admin token.
-- Identity metadata: no FORCE RLS (same pattern as app_user). API authorization
-- still binds client_id from the signed JWT and membership.

CREATE TABLE excel_workbook_grant (
    id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES app_user (id),
    client_id uuid NOT NULL REFERENCES client (id),
    jti uuid NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    last_used_at timestamptz,
    CONSTRAINT excel_workbook_grant_pkey PRIMARY KEY (id),
    CONSTRAINT excel_workbook_grant_jti_key UNIQUE (jti)
);

COMMENT ON TABLE excel_workbook_grant IS
    'Revocable Excel refresh grants. Access JWT exp remains short. Grant TTL allows post-expiry /auth/refresh for client/reader workbooks only.';

CREATE INDEX excel_workbook_grant_user_idx
    ON excel_workbook_grant (user_id);

CREATE INDEX excel_workbook_grant_client_idx
    ON excel_workbook_grant (client_id);

GRANT SELECT, INSERT, UPDATE ON TABLE excel_workbook_grant TO dfip_api;
GRANT SELECT, INSERT, UPDATE ON TABLE excel_workbook_grant TO dfip_worker;
GRANT ALL ON TABLE excel_workbook_grant TO dfip_migrator;
