-- Operator-only reporting LOGINs for Power BI Desktop (P9).
-- NOT a migration. Do not apply via dfip_db.migrate. Do not commit passwords.
-- dfip_api is NOLOGIN; Desktop cannot connect as that role name.
-- Each login assumes dfip_api and encodes one client in role defaults so
-- DirectQuery sessions get tenant GUCs without a session-start script.
-- Replace REPLACE_ME_CLIENT_A / REPLACE_ME_CLIENT_B before running.

CREATE ROLE dfip_desktop_a LOGIN PASSWORD 'REPLACE_ME_CLIENT_A';
GRANT dfip_api TO dfip_desktop_a;
ALTER ROLE dfip_desktop_a SET ROLE dfip_api;
ALTER ROLE dfip_desktop_a SET dfip.role = 'reader';
ALTER ROLE dfip_desktop_a SET dfip.client_ids = 'a0000000-0000-4000-8000-000000000001';
ALTER ROLE dfip_desktop_a SET dfip.platform_admin = 'false';

CREATE ROLE dfip_desktop_b LOGIN PASSWORD 'REPLACE_ME_CLIENT_B';
GRANT dfip_api TO dfip_desktop_b;
ALTER ROLE dfip_desktop_b SET ROLE dfip_api;
ALTER ROLE dfip_desktop_b SET dfip.role = 'reader';
ALTER ROLE dfip_desktop_b SET dfip.client_ids = 'a0000000-0000-4000-8000-000000000002';
ALTER ROLE dfip_desktop_b SET dfip.platform_admin = 'false';
