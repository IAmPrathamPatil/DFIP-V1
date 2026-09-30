-- Single active Publisher session per app_user.
--
-- The website bearer JWT is shared by every tab, so it cannot identify one
-- browser instance. This table is the authoritative Publisher session/lease
-- record. A Publisher page/tab registers a session here after authentication
-- and sends the returned opaque id as X-DFIP-Publisher-Session on protected
-- Publisher requests. Registering revokes the caller's previous session, so a
-- new tab or a new browser takes over and the older one stops being valid.
--
-- Identity metadata: no FORCE RLS (same pattern as app_user and
-- excel_workbook_grant). The session id is an opaque 256-bit token, carries no
-- user information, and is only issued to an already-authenticated caller.
-- Client/reader sessions are never tracked here: multi-client access is
-- unrestricted.
--
-- One active row per user is enforced by publisher_session_one_active.
-- Registration additionally serializes on a transaction-scoped advisory lock
-- derived from user_id so two simultaneous registrations cannot both commit.

CREATE TABLE publisher_session (
    id text NOT NULL,
    user_id uuid NOT NULL REFERENCES app_user (id),
    created_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    user_agent text,
    CONSTRAINT publisher_session_pkey PRIMARY KEY (id)
);

COMMENT ON TABLE publisher_session IS
    'One revocable, leased Publisher website session per app_user. The newest registration wins; the previous session is revoked in the same transaction.';

COMMENT ON COLUMN publisher_session.id IS
    'Opaque 256-bit URL-safe token issued to the authenticated caller. Not a JWT and not derived from app_user.subject.';

-- Hard invariant: at most one unrevoked session per user. Expired leases are
-- still unrevoked, so registration must revoke them before inserting.
CREATE UNIQUE INDEX publisher_session_one_active
    ON publisher_session (user_id)
    WHERE revoked_at IS NULL;

CREATE INDEX publisher_session_expires_idx
    ON publisher_session (expires_at);

GRANT SELECT, INSERT, UPDATE ON TABLE publisher_session TO dfip_api;
GRANT SELECT, INSERT, UPDATE ON TABLE publisher_session TO dfip_worker;
GRANT ALL ON TABLE publisher_session TO dfip_migrator;
