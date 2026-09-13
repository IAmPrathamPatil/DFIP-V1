# DFIP V1 — Security and client exposure

**Never copy secret values into documentation.** If a secret exists in an environment or tmp file, record only `[SECRET — VALUE NOT DOCUMENTED]`.

This is a documentation-level audit of **this repository and designed client artifacts**, not a penetration test.

---

## Authentication model (VERIFIED)

| Mechanism | Where | Notes |
|---|---|---|
| Bearer `dev_token` | `authenticate_bearer` | Constant compare (`hmac.compare_digest`). Dev/test only. Empty token = all requests fail. With DSN, `DFIP_DEV_AUTH_CLIENT_ID` required. |
| HS256 JWT | PyJWT | `exp`+`sub` required; `algorithms=["HS256"]`; `alg=none` rejected; leeway 0; optional `iss`/`aud`; `role` in `{admin,publisher,reader,client}`; optional `client_id`, `ver`. |
| Password login | `auth_routes` + `password.py` | PBKDF2; issues JWT. A publisher/admin with two or more inspector-only memberships may omit `client_id`; the JWT is unbound until `POST /auth/select-client`. Request `client_id` cannot widen a bound JWT. Not a hosted IdP. One-time operator publisher setup: `POST /api/v1/auth/setup-publisher` (username/password/confirm; 409 if a publisher already exists). Production-grade setup is denied unless header `X-DFIP-Bootstrap-Token` matches `DFIP_BOOTSTRAP_TOKEN`. Operator-provisioned client logins: `POST /api/v1/clients/{client_id}/users` (inspector; operator-chosen username/password/confirm; hash only; role `client` only; minimum 12 characters). Local demo seed is a test fixture, not product setup. |
| Excel live | Settings `BearerToken` at refresh time → M `Authorization` header | Git template stays empty. JWT `client_id` is authoritative; Settings ClientId cannot widen. Excel web connection should be Anonymous. |
| Excel static recovery | Credentials **cleared** | Query tables stripped. |
| Excel refreshable | ApiBaseUrl plus the caller's **short-lived access JWT** in BearerToken; ClientId empty | Same session token as the download request. Not a permanent secret. History stays authenticated. Excel web connection should be Anonymous. While the stamped JWT is still valid, Refresh All calls `POST /auth/refresh`. After expiry, re-download. |
| Power BI | Postgres reporting LOGIN | Spec only; no pbix in git. |
| SPA | `sessionStorage` (`apps/web/static/js/auth.js`) | XSS would expose token — residual browser risk. Not `localStorage`. |
| Health | Unauthenticated | No secrets. |
| `/config.json` | Unauthenticated | API URL only. |

Production-grade (`DFIP_ENV=staging` or `production`): `DFIP_AUTH_MODE=jwt`,
secret ≥ 32 characters (known-bad values rejected), OpenAPI disabled,
`dev_token` refused, `DATABASE_URL` required, remote DSNs require TLS,
HTTPS origins required, local `.env` is not read, first publisher setup
requires `X-DFIP-Bootstrap-Token`.

**Authorization is application-level.** PostgreSQL RLS is **defense in depth** when DSN is set. Exact policies: [V1_RLS.md](V1_RLS.md). Login/identity queries use `transaction(..., rls=None)` and **do not** `SET LOCAL ROLE dfip_api`. README: V1 is **not** production tenant isolation. P13F company deactivation is also application-layer: inactive tenants can still appear in `rpt_*` / direct SQL if membership/RLS allows. Permanent purge is an operator CLI with a privileged DSN; `dfip_api` is not granted DELETE on `client`, `app_user`, `client_membership`, `publication_fact`, or catalog tables.

---

## Intentionally exposed to clients

- Published fact slice (JSON/CSV/XLSX) for **their** `client_id` after publish.
- Static `Client_Report.xlsx`: PublishedFacts cells, nine PivotTables, group captions AZ:BA, lineage columns that are **on the fact row** (run ids, match status, etc.).
- SPA `/client/*` published facts UI.

Lineage columns (`processing_run_id`, version ids, `label_match_status`, …) **are in FACT_HEADERS** and therefore **are on the client sheet**. That is **intentional in the current schema**, not hidden. Operators who consider run ids sensitive should treat this as **MEDIUM** residual exposure. Stored catalog version IDs must belong to the run's `client_id`; packaged fallback must not masquerade as a different tenant's catalog version (RUN 004B-3). `fact_campaign_day.rate_card_rule_id` is stored only when the rule belongs to the run's client (packaged P1 rules are default-client owned); otherwise it is NULL while Total Cost still uses packaged rates.

---

## Not exposed (by design)

| Asset | Control |
|---|---|
| Raw `stg_source_row` / source xlsx | Inspector API only; object store server-side |
| Working-set `GET /facts` | Inspector roles |
| Catalogs Logic/Labels HTTP | Inspector |
| Client passwords | Never returned; PBKDF2 hash only on `app_user` |
| `DATABASE_URL`, service-role, storage endpoint | Server env |
| `SUPABASE_ANON_KEY` / service role | Must never ship to Excel/SPA (`.env.example`) |
| Publisher JWT on static download | Cleared |
| Live API URL on static download | Cleared |
| Client-defined schema / extra trusted columns | Clients cannot register fields. Only `APPROVED_EXTRA_SOURCE_COLUMNS` in code. Unapproved extras fail ingest. Workbook-local headers are not authorization. |
| Backup manifest | No `DATABASE_URL`, passwords, JWT secrets, or bootstrap tokens |
| Backup dump / source archive | Operator filesystem only; not HTTP; not the SPA |

---

## Residual risks

| Risk | Severity | Evidence |
|---|---|---|
| DataMashup still contains `PublishedFacts.m` in static zip | **MEDIUM** | If user later pastes ApiBaseUrl+JWT, live query can return. Generator disables refresh flags but does not claim mashup binary is gone. |
| Lineage UUIDs on PublishedFacts | **MEDIUM** | `FACT_HEADERS` includes run/batch/version ids |
| HS256 shared secret rotation | **MEDIUM** for production | Replace `DFIP_AUTH_SECRET` and restart. Existing JWTs become invalid. Dual-key rotation is not implemented. |
| SPA token in JS storage | **MEDIUM** | Typical SPA |
| `dev_token` if mis-set in staging | **HIGH** | Config error |
| In-memory identity/stores on a shared host | **HIGH** | Process memory |
| CORS single origin | LOW if misconfigured | empty origin = no CORS middleware |
| OpenAPI in non-production | LOW | Documents API |
| Health `database.configured` | LOW | Reveals whether DSN set |
| Localhost Excel data sources leftover from other projects | **N/A to DFIP xlsx** | Prior investigation: other prototype CSVs on the machine, not in DFIP package |
| Hardcoded template client UUID `a0000000-0000-4000-8000-000000000001` | LOW | Seed/demo client, used in tests and `_TEMPLATE_SETTINGS_CLIENT_ID` |
| Example SQL reporting logins | LOW | No passwords in example; operator must not commit real passwords |
| Gitignored `tmp/local_demo_bearer.txt`, `tmp/local_demo_auth_secret.txt` | **HIGH if copied to git or chat** | Do not document values; do not commit |
| Unrestricted backup dump / archive | **HIGH** | Contains client data and `password_hash`. Restrict access; do not serve over HTTP. |

---

## Hardcoded JWTs / secrets in source

`.env.example` uses empty placeholders. Settings defaults empty.

**Do not** treat seed UUIDs (`a0000000-0000-4000-…`) as cryptographic secrets; they are well-known demo ids.

Search for TODO/FIXME in py/md/m/dax: **none found** in a repo grep at documentation time.

---

## Unsafe endpoints (design)

- `audit_log` table exists in SQL. The API writes best-effort rows for
  `auth.login`, `upload.accept`, and `publication.create`. Passwords, JWTs, and
  DSNs are not stored. Failures are swallowed. Identity queries still use
  `rls=None`.
- `storage_uri` never returned on source-file JSON.
- Public: `/health` (liveness only; no DB), web `/config.json`, `/auth/login` (credential stuffing surface — process-local 5 failures / 10 minutes by IP and username; 429 `Too many requests.` after threshold; no remaining-attempt leak). Authenticated `GET /api/v1/ops/ready` is publisher/inspector only and must not appear on the client portal. Production-grade `GET /auth/setup-status` does not reveal whether first-publisher setup is open. `POST /auth/setup-publisher` failures use the same process-local throttle. `X-Forwarded-For` is trusted only when the TCP peer is loopback (V1 Caddy).
- No anonymous published facts.
- Backup/restore is an operator CLI (`python -m dfip_api.backup`), not an HTTP route. Dump login must be superuser or BYPASSRLS; never `dfip_api`. Secrets are restored through the P13A environment, not from the backup set. Encryption-at-rest is a host concern (`documentation/V1_DEPLOYMENT.md`). `--confirm-production-local` unlocks colocated database name `dfip` only.
- Company purge is an operator CLI (`python -m dfip_api.purge`), not an HTTP route. Confirmation is immutable `client.code` plus `--confirm-purge`. Display name is not a token.

---

## Client Excel security checklist

1. Deliver **API client-report**, not the git template.  
2. Confirm Settings blank.  
3. Do not Refresh All.  
4. Do not paste production JWT into mashup.  
5. Machine-level Excel “allowed data sources” may still list loopback from authoring PCs — that does not publish DFIP raw CSV to the internet.

---

## Logging / tokens

Do not log Authorization headers, Cookie headers, passwords, or `DATABASE_URL`.
Application loggers do not emit those fields; `DFIP_LOG_LEVEL` sets uvicorn and
application verbosity. `redact_dsn()` strips userinfo from operator-facing DSN
messages. There is no global regex log scrubber. `issue_access_token` docstring:
never log the token.

---

## Related

[V1_EXCEL_REPORTING.md](V1_EXCEL_REPORTING.md), [V1_API_REFERENCE.md](V1_API_REFERENCE.md), `tests/test_security_hardening.py`, `tests/test_p9_authz.py`.
