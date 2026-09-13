# DFIP V1 — Production deployment (P13G)

Locked V1 architecture: **one DigitalOcean Droplet in BLR1**, PostgreSQL 16
**on the same Droplet**, persistent block volume for the source archive,
systemd for `python -m dfip_api` and `python -m dfip_web`, Caddy for HTTPS.
Exactly one API process. No Redis, Kubernetes, managed Postgres, object-storage
SDK, WAF, API gateway, Power BI deployment, or Dashboard deployment.

This file is the production runbook. Artifacts live under `deploy/`.

**This repository does not provision DigitalOcean, DNS, or production secrets.**
Host-level systemd/Caddy execution is pending a real Droplet rehearsal.

## Labels

**REPOSITORY-DEFINED:** ports, environment variables, migrations, CLI commands,
storage key layout, auth, backup/restore/purge semantics, loopback bind.

**HOST-DEFINED:** OS image, firewall syntax, disk size, TLS certificate, DNS,
SSH, systemd installation, Caddy installation, block-device names, Droplet size.

---

## Topology

```
Internet
   |  TCP 443 (and 80 redirect)
   v
Caddy (HTTPS, HTTP->HTTPS)
   |                    |
   | 127.0.0.1:3000     | 127.0.0.1:8000
   v                    v
dfip_web             dfip_api  (+ in-process ThreadPoolExecutor)
                           |
                           +--> PostgreSQL 16 on 127.0.0.1:5432  (not public)
                           +--> /var/lib/dfip/source-storage      (block volume)
                           +--> /var/lib/dfip/backups             (restricted)
```

| Role | Bind | Public |
|---|---|---|
| `dfip-api` | `127.0.0.1:8000` | No |
| `dfip-web` | `127.0.0.1:3000` | No |
| PostgreSQL | `127.0.0.1:5432` | No |
| Caddy | `:443` / `:80` | Yes |

`/config.json` is served by the web process. Caddy does not need a special
route: `/api/v1*` and `/health` go to the API; everything else, including
`/config.json`, goes to the web origin.

Public `GET /health` is API liveness. It does not prove database health.
Authenticated `GET /api/v1/ops/ready` is operator diagnostics only. Do not put
operator credentials in Caddy or a load balancer.

---

## Secrets

| File | Owner | Mode | Loaded by |
|---|---|---|---|
| `/etc/dfip/dfip.env` | `root:dfip` | `0640` | `dfip-api`, `dfip-web` |
| `/etc/dfip/dfip-operator.env` | `root:root` | `0600` | backup/purge only |

Never git. Never image layers. Never Excel. Never browser. Never systemd unit
files. Templates: `deploy/env/production.env.example` and
`deploy/env/operator.env.example`.

`DFIP_AUTH_SECRET` ≥ 32 characters, not a known-bad value. Production ignores
a repository-local `.env` (P13A).

---

## First boot (do not execute against a real environment from this phase)

HOST-DEFINED steps are marked. REPOSITORY-DEFINED commands are literal.

1. **Provision Droplet** (HOST) — BLR1. One VM. Record the public IPv4.
2. **Attach persistent source volume** (HOST) — mount at
   `/var/lib/dfip/source-storage` (example placeholder).
3. **Prepare backup storage** (HOST) — `/var/lib/dfip/backups`, not inside the
   live archive tree.
4. **Install runtime** (HOST) — Python 3.11+, `pip`, PostgreSQL client tools
   (`pg_dump` / `pg_restore`) matching server major 16.
5. **Install PostgreSQL 16** (HOST) — listen on loopback only.
6. **Create roles/database** — see Database below. Migrations create
   `dfip_migrator` / `dfip_api` / `dfip_worker` as `NOLOGIN`.
7. **Inject production environment** — copy the template to `/etc/dfip/dfip.env`.
   Include `DFIP_BOOTSTRAP_TOKEN` only for this window.
8. **Install application** — unpack a release under `/opt/dfip/releases/<id>`,
   `pip install -e .` into that tree's `.venv`, symlink `/opt/dfip/current`.
   Editable install is required so `dfip_web` finds `apps/web/static` and
   `python -m dfip_db` finds `supabase/migrations`.
9. **Run migrations** — `python -m dfip_db` with a migrator-capable DSN.
10. **Start API/web privately** — systemd units in `deploy/systemd/`. Confirm
    they bind loopback (`ss` / equivalent). Do not publish DNS yet.
11. **Bootstrap publisher** — from the Droplet, loopback only:

    ```text
    curl -sS http://127.0.0.1:8000/api/v1/auth/setup-publisher \
      -H "Content-Type: application/json" \
      -H "X-DFIP-Bootstrap-Token: <token>" \
      -d '{"username":"<operator>","password":"<password>","confirm_password":"<password>"}'
    ```

    Do not put the token in a query string.
12. **Remove bootstrap token** — delete `DFIP_BOOTSTRAP_TOKEN` from
    `/etc/dfip/dfip.env` and restart `dfip-api`.
13. **Verify `/health`** — `curl http://127.0.0.1:8000/health` → `dfip-api`.
    `GET /api/v1/auth/setup-status` must not advertise the production window.
14. **Verify readiness** — `GET /api/v1/ops/ready` with the publisher JWT.
15. **Local/synthetic smoke** — `python deploy/scripts/smoke.py` plus the
    manual remainder in Production smoke below. Synthetic workbooks only.
16. **Configure Caddy** — `deploy/Caddyfile`, set `DFIP_DOMAIN`.
17. **Configure DNS** (HOST) — A/AAAA to the Droplet. Placeholder documented
    as `https://dfip.example.com`. Not the final domain in this repository.
18. **Enable HTTPS** — Caddy obtains certificates. Confirm HTTP redirects.
19. **Public smoke** — `DFIP_SMOKE_BASE_URL=https://dfip.example.com python deploy/scripts/smoke.py`.
20. **First coordinated backup + verify** — operator gate before public traffic:

    ```text
    systemctl stop dfip-api
    python -m dfip_api.backup backup --output /var/lib/dfip/backups/<stamp>
    python -m dfip_api.backup verify --backup /var/lib/dfip/backups/<stamp>
    systemctl start dfip-api
    ```

    Do not launch public traffic before a verified recovery point exists.

---

## Database

OS is HOST-DEFINED. DigitalOcean Droplets commonly run Ubuntu; treat the
commands below as **one example**, not a repository requirement.

Example (Ubuntu 24.04, PostgreSQL 16, loopback):

1. Install PostgreSQL 16 from the operator's chosen package source.
2. `listen_addresses = 'localhost'` (or `127.0.0.1`).
3. Host firewall: do not publish 5432.
4. Create database `dfip`.
5. Run `python -m dfip_db` as a superuser/migrator so cluster roles exist.
6. Create a LOGIN role for the API that is **not** `BYPASSRLS` and can
   `SET LOCAL ROLE dfip_api`. Put that DSN in `/etc/dfip/dfip.env`.
7. Create a separate LOGIN superuser (or BYPASSRLS) for backup/purge. Put that
   DSN only in `/etc/dfip/dfip-operator.env`.
8. **Do not grant BYPASSRLS to `dfip_api`.**

`DATABASE_URL` must use host `127.0.0.1` or `localhost` so P13A loopback TLS
exemption applies and so restore/purge hosted-DSN guards still pass. Do not
point production at a hostname containing `amazonaws.com`, `supabase`, or
other hosted markers.

P13G adds no SQL migration. Latest schema file:
`supabase/migrations/20260901000020_p13f_client_lifecycle.sql` (additive).
V1 practice: **stop the API before migrating.** Do not invent down migrations.

---

## Filesystem permissions

| Path | Owner | Mode | Who |
|---|---|---|---|
| `/opt/dfip/current` | `dfip:dfip` | `0755` | application tree |
| `/var/lib/dfip/source-storage` | `dfip:dfip` | `0750` | API read/write; not world; not HTTP |
| `/var/lib/dfip/backups` | `root:dfip` or `dfip:dfip` | `0750` | operator + backup job |
| `/etc/dfip/dfip.env` | `root:dfip` | `0640` | API/web |
| `/etc/dfip/dfip-operator.env` | `root:root` | `0600` | backup/purge |

Key layout is unchanged:
`{DFIP_STORAGE_ENDPOINT}/{bucket}/{client_id}/{source_file_id}/{sha256}.xlsx`.

The git repository is not production archive storage. Browsers reach files only
over HTTPS through Caddy.

---

## Firewall

Public: 443. 80 only for HTTP→HTTPS redirect.

Do not expose: 5432, 3000, 8000, source archive, backup directories.

SSH: restrict to operator networks where possible (HOST-DEFINED syntax).

---

## Reverse proxy and P13E IP handling

P13E throttles failed login/bootstrap by IP and username (5 / 10 minutes,
process-local). Direct public access to :8000 is firewalled.

Caddy on loopback is the only trusted proxy. `request_peer_ip` uses
`X-Forwarded-For` **only when the TCP peer is loopback**, and then only the
**rightmost** address. Spoofed `X-Forwarded-For` from a non-loopback peer is
ignored. Do not globally trust `X-Forwarded-For`. Do not add
`ProxyHeadersMiddleware` for arbitrary proxies.

Limitation: login throttling remains process-local (one API instance). That
matches the locked single-instance model.

---

## Backup scheduling

Example systemd timer: `deploy/systemd/dfip-backup.timer` (02:30 UTC-ish local,
plus jitter). **Operator-configurable. Not enabled until step 20 succeeds.**

The timer runs `deploy/scripts/backup-run.sh`:

1. Stop `dfip-api`.
2. `python -m dfip_api.backup backup --output DIR`.
3. `python -m dfip_api.backup verify --backup DIR`.
4. Logs go to journald (`SyslogIdentifier=dfip-backup`).
5. Restart `dfip-api` (shell trap).

P13F `python -m dfip_api.backup identify-eligible --parent DIR` lists extra
verified sets only. It never deletes.

---

## Restore

Disposable verification (unchanged):

```text
python -m dfip_api.backup restore --backup DIR \
  --target-database-url URL --target-archive-root DIR --confirm-disposable
```

Refuses hosted URLs and database names `dfip` / `postgres` / `template0` /
`template1`.

Production colocated restore (replacement Droplet / disaster recovery):

```text
python -m dfip_api.backup restore --backup DIR \
  --target-database-url postgresql://dfip_operator@127.0.0.1:5432/dfip \
  --target-archive-root /var/lib/dfip/source-storage.restore \
  --confirm-production-local
```

`--confirm-production-local` still refuses hosted/non-local URLs and allows
**only** database name `dfip`. It cannot be combined with
`--confirm-disposable`. `--target-archive-root` must not already exist; swap
onto the live mount after verify.

Do not re-apply `python -m dfip_db` on a complete dump. Inject P13A secrets
from the environment. Start the API so P13B can sweep leftover runs.

---

## Purge

CLI only. No HTTP purge. Do not broaden `dfip_api` DELETE grants.

Disposable:

```text
python -m dfip_api.purge dry-run --code CODE --confirm-disposable
python -m dfip_api.purge purge --code CODE --confirm-purge --confirm-disposable
```

Production colocated database name `dfip`:

```text
python -m dfip_api.purge dry-run --code CODE --confirm-production-local
python -m dfip_api.purge purge --code CODE --confirm-purge --confirm-production-local
```

Still refuses hosted URLs, the packaged/default owner, and companies that are
not inactive and eligible. Production-local confirmation cannot be combined
with `--confirm-disposable`.

---

## Release / rollback

Layout:

```text
/opt/dfip/releases/<stamp>/
/opt/dfip/current   -> releases/<stamp>
/opt/dfip/previous  -> previous stamp
```

`deploy/scripts/deploy.sh` copies a tree, installs the venv, stops services,
migrates, switches `current`, starts services. It does not delete `previous`.

**Application-only rollback:** `deploy/scripts/rollback.sh` (previous tree +
restart). Use when schema is still backward-compatible.

**Migration/data rollback:** no down migrations. Isolate the service, restore
the matching verified PostgreSQL dump **and** source-archive backup, restore
the previous application tree, start, verify. Not zero downtime.

P13F columns are additive with defaults. P13G has no schema change. Still stop
the API before applying pending SQL.

---

## Disaster recovery (server lost)

1. Provision a replacement Droplet (HOST).
2. Attach or recreate the backup volume / copy the latest verified backup set.
3. Install PostgreSQL 16 on loopback.
4. Create empty database `dfip` and cluster role prerequisites.
5. Restore dump + archive with `--confirm-production-local`.
6. Inject `/etc/dfip/dfip.env` (P13A). Do not restore secrets from the backup.
7. Do not migrate if the dump already contains the ledger.
8. Start API/web. P13B abandoned-run sweep runs on API start.
9. Verify backup mapping, `/health`, `/ops/ready`, tenant isolation, smoke.

There is no second recovery mechanism.

---

## Logging

stdout/stderr → journald (`SyslogIdentifier=dfip-api` / `dfip-web`).
Do not log passwords, `Authorization`, cookies, or `DATABASE_URL`.
Journal retention is HOST-DEFINED operator policy. No separate logging service.

---

## Monitoring

P13D only: `/health`, `/ops/ready`, journald, processing status.
No Prometheus, Grafana, or Sentry.

---

## Power BI / Dashboard

SPA analytics D0–D9 is complete on `/client/overview` (see
`documentation/D9_DASHBOARD_COMPLETE.md`). Power BI DirectQuery is not
deployed in P13G. Future DirectQuery would need a private path to colocated
PostgreSQL `rpt_*` views (do not expose 5432 publicly).

---

## CI/CD

GitHub Actions must not deploy. CI may lint and test, including
`tests/test_p13g_deploy.py` (artifact + confirmation-path unit tests).
Do not put production secrets in CI.

---

## Checklists

### Pre-deploy

- Tests green (`python -m pytest` plus postgres marker when available)
- Previous release preserved
- Pending migrations identified (`python -m dfip_db` is idempotent)
- Secrets present in `/etc/dfip/dfip.env` (not git)
- Source volume mounted
- Last backup verified if this is not first boot

### Deploy

- Stop API/web
- Coordinated backup + verify
- Migrate
- Switch `current`
- Start
- `/health`
- Authenticated `/ops/ready`
- Smoke

### Post-deploy

- Backup verify
- Logs clean (no 5xx storm)
- Operator login
- Upload / process / publish on a **disposable** company
- Report download
- Deactivate / reactivate

### Rollback

- Application failure → stop new version → previous release → restart → health → smoke
- Migration/data failure → isolate → restore matching DB/archive backup → previous release → start → verify

---

## Production smoke

Automated subset: `python deploy/scripts/smoke.py`.

Manual remainder (synthetic data only):

1. HTTPS web loads
2. `/config.json` contains only public API location
3. `/health` returns 200
4. Login works
5. Publisher sees Companies
6. Create/select disposable company
7. Upload synthetic workbook
8. Process
9. QA
10. Publish
11. Current report
12. Historical report
13. Refreshable workbook
14. Deactivate
15. Historical recovery remains
16. Reactivate
17. Backup verify
18. Purge disposable company
19. Company B remains untouched

---

## Disposable rehearsal (this repository)

```text
python deploy/scripts/rehearse.py
python -m pytest tests/test_p13g_deploy.py
```

On Windows, systemd and Caddy are **not** executed:
`deployment artifact generated; host-level execution pending P13G production rehearsal.`

PostgreSQL backup/restore/purge remain covered by P13C/P13F tests when
`DFIP_TEST_DATABASE_URL` points at a disposable local database.

---

## Confirmation (this implementation run)

- No DigitalOcean resource created
- No DNS changed
- No production database touched
- No production secrets created
- No hosted/live/August data touched
