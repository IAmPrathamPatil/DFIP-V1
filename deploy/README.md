# DFIP V1 deployment artifacts (P13G)

Production runbook: [`documentation/V1_DEPLOYMENT.md`](../documentation/V1_DEPLOYMENT.md).

This directory is a **package**, not a live environment. It does not create
DigitalOcean resources.

| Path | Purpose |
|---|---|
| `systemd/dfip-api.service` | API on 127.0.0.1:8000 |
| `systemd/dfip-web.service` | Web on 127.0.0.1:3000 |
| `systemd/dfip-backup.service` | Coordinated backup oneshot |
| `systemd/dfip-backup.timer` | Example daily schedule |
| `Caddyfile` | HTTPS edge; domain via `DFIP_DOMAIN` |
| `env/production.env.example` | API/web environment (no secrets) |
| `env/operator.env.example` | Backup/purge DSN overlay |
| `scripts/backup-run.sh` | Stop API, backup, verify, start API |
| `scripts/deploy.sh` | Recreate-in-place release |
| `scripts/migrate.sh` | `python -m dfip_db` |
| `scripts/rollback.sh` | Previous application tree |
| `scripts/smoke.py` | Health + config.json |
| `scripts/rehearse.py` | Disposable local start/restart |

If a Linux host reports `$'\r': command not found` on the `.sh` files, strip
CRLF (HOST-DEFINED). systemd/Caddy execution is pending production rehearsal.
