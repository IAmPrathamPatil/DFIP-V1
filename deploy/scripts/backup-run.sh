#!/bin/sh
# Coordinated P13C backup: stop API, dump, copy archive, verify, start API.
# REPOSITORY-DEFINED commands. HOST-DEFINED: systemctl, stamp directory.
# Do not echo DATABASE_URL or other secrets.

set -eu

ROOT="${DFIP_RELEASE_ROOT:-/opt/dfip/current}"
BACKUP_PARENT="${DFIP_BACKUP_PARENT:-/var/lib/dfip/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_PARENT}/${STAMP}"
PYTHON="${ROOT}/.venv/bin/python"
LAST="${DFIP_BACKUP_LAST_DIR:-${BACKUP_PARENT}/last}"

cd "${ROOT}"
mkdir -p "${BACKUP_PARENT}" "${OUT}"

started=0
stop_api() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop dfip-api || true
    started=1
  fi
}

start_api() {
  if [ "${started}" -eq 1 ] && command -v systemctl >/dev/null 2>&1; then
    systemctl start dfip-api || true
  fi
}

trap start_api EXIT

echo "dfip-backup: stopping API for coordinated backup ${STAMP}" >&2
stop_api

"${PYTHON}" -m dfip_api.backup backup --output "${OUT}"
"${PYTHON}" -m dfip_api.backup verify --backup "${OUT}"

ln -sfn "${OUT}" "${LAST}"
echo "dfip-backup: verified ${OUT}" >&2
