#!/bin/sh
# Apply pending SQL migrations. Stop the API first (caller responsibility).
# REPOSITORY-DEFINED: python -m dfip_db. Never re-run applied ledger rows.

set -eu

ROOT="${DFIP_RELEASE_ROOT:-/opt/dfip/current}"
cd "${ROOT}"
"${ROOT}/.venv/bin/python" -m dfip_db
