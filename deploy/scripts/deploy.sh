#!/bin/sh
# Recreate-in-place application deploy. Not zero-downtime.
# REPOSITORY-DEFINED: pip install -e ., python -m dfip_db, health.
# HOST-DEFINED: /opt/dfip layout, systemctl.

set -eu

ROOT="${DFIP_INSTALL_ROOT:-/opt/dfip}"
SOURCE="${1:-}"
if [ -z "${SOURCE}" ]; then
  echo "usage: deploy.sh /path/to/unpacked-release" >&2
  exit 2
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RELEASE="${ROOT}/releases/${STAMP}"
PYTHON_BIN="${DFIP_PYTHON:-python3.11}"

mkdir -p "${ROOT}/releases"
cp -a "${SOURCE}" "${RELEASE}"
cd "${RELEASE}"
"${PYTHON_BIN}" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e .

echo "dfip-deploy: stopping services" >&2
systemctl stop dfip-web || true
systemctl stop dfip-api

echo "dfip-deploy: applying pending migrations" >&2
.venv/bin/python -m dfip_db

if [ -L "${ROOT}/current" ] || [ -e "${ROOT}/current" ]; then
  rm -f "${ROOT}/previous"
  cp -P "${ROOT}/current" "${ROOT}/previous" 2>/dev/null || ln -sfn "$(readlink "${ROOT}/current")" "${ROOT}/previous"
fi
ln -sfn "${RELEASE}" "${ROOT}/current"

systemctl start dfip-api
systemctl start dfip-web

echo "dfip-deploy: ${RELEASE} is current. previous kept. run smoke.py next." >&2
