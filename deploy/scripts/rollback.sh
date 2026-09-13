#!/bin/sh
# Application-only rollback: point current at previous release and restart.
# Does not reverse SQL migrations. Data rollback is restore from a verified backup.

set -eu

ROOT="${DFIP_INSTALL_ROOT:-/opt/dfip}"
if [ ! -e "${ROOT}/previous" ]; then
  echo "dfip-rollback: previous release is missing." >&2
  exit 1
fi

systemctl stop dfip-web || true
systemctl stop dfip-api
ln -sfn "$(readlink -f "${ROOT}/previous")" "${ROOT}/current"
systemctl start dfip-api
systemctl start dfip-web
echo "dfip-rollback: current -> $(readlink -f "${ROOT}/current")" >&2
