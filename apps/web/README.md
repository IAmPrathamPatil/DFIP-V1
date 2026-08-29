# apps/web

Professional Publisher/Admin and Client SPA.

The browser talks only to the P5/P7 API (`/health` and `/api/v1/*`). This folder
does not contain React/Vite/Next, does not open a database, and does not
implement a KPI calculator, Excel processing, or RLS. Review displays inspector
QA findings from `GET /api/v1/processing-runs/{id}/qa-findings` when the API
returns them. Admin `/admin/facts` uses `GET /api/v1/facts` (working set;
admin/publisher only). Client `/client/facts` uses
`GET /api/v1/publications/current/facts` (published slice). Client/reader
cannot call working-set or staging routes. **This is application-level
authorization, not PostgreSQL RLS and not production tenant isolation.**

Screen inventory and workflows: `documentation/WEBSITE.md`.

## Run

Terminal 1 — API:

```powershell
$env:DFIP_AUTH_SECRET="choose-a-local-jwt-secret"
$env:DFIP_AUTH_MODE="jwt"
# Optional local curl path (not used by the SPA):
# $env:DFIP_AUTH_MODE="dev_token"
# $env:DFIP_DEV_AUTH_TOKEN="choose-a-local-token"
# $env:DFIP_DEV_AUTH_ROLE="publisher"
# When DATABASE_URL is set with dev_token, also set DFIP_DEV_AUTH_CLIENT_ID.
python -m dfip_api
```

Terminal 2 — web origin (CORS already allows `DFIP_WEB_ORIGIN`):

```powershell
python -m dfip_web
```

Open `http://127.0.0.1:3000` and sign in with an `app_user` username and
password. The SPA never accepts a pasted developer token. A client or
reader session can open Client pages but receives 403 from working-set APIs.

Password sign-in requires `DFIP_AUTH_SECRET` so the API can issue JWTs. In
development you may still set `DFIP_AUTH_MODE=dev_token` and
`DFIP_DEV_AUTH_TOKEN` for curl; if `DFIP_AUTH_SECRET` is also set, the same
process accepts both. The in-memory API starts with an empty user directory
unless you inject users (tests do). PostgreSQL mode uses `app_user.password_hash`
and `client_membership`.

Default in-memory API stores are empty. Empty tables are expected: API startup
does not ingest an XLSX. Publishers upload a `.xlsx` from Upload Center
(`POST /api/v1/uploads` returns 202 and the page polls batch/run status; does not publish) or call the same endpoint directly
(see `documentation/HTTP_WORKFLOW.md`). Client home, `/client/facts`, and
`/admin/downloads` can download the published slice as CSV or XLSX.

## Auth limitation

The SPA stores the **issued** access JWT in `sessionStorage` (tab-scoped).
That is not a developer token and not an identity-provider cookie. JWT signing
secrets never leave the API process. Sign-out clears the tab key and increments
`app_user.token_version`. Application-level authorization is not PostgreSQL RLS.
