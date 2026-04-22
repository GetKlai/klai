#!/usr/bin/env bash
# Apply every post_deploy_*.sql script in alembic/versions/ as the klai
# superuser, in alphabetical order. Each script must be idempotent
# (DROP POLICY IF EXISTS / CREATE OR REPLACE FUNCTION / etc.) — they
# are designed to run on every deploy, not just the first.
#
# Why this script exists
# ----------------------
# `alembic upgrade head` runs as the `portal_api` role, which cannot
# CREATE POLICY / ALTER TABLE / CREATE FUNCTION. RLS policies, triggers,
# and helper functions therefore live in `post_deploy_*.sql` files that
# run as the `klai` superuser AFTER alembic has applied schema changes.
# An operator who runs `alembic upgrade` but skips this step will leave
# the DB inconsistent with the deployed code (the 2026-04-21 RLS
# incident traced back exactly to this gap).
#
# Usage:
#     ./apply_post_deploy_sql.sh                          # production
#     ./apply_post_deploy_sql.sh --host staging-01        # alt host
#     ./apply_post_deploy_sql.sh --container my-postgres  # alt container
#     ./apply_post_deploy_sql.sh --dry-run                # list files, run nothing
#
# Idempotent: re-runs the full set every time. Total runtime in production
# is sub-second per script.
set -euo pipefail

HOST="core-01"
CONTAINER="klai-core-postgres-1"
DRY_RUN=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSIONS_DIR="$(cd "${SCRIPT_DIR}/../alembic/versions" && pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --container) CONTAINER="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//; /^set -euo/d'
            exit 0
            ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

mapfile -t SCRIPTS < <(find "${VERSIONS_DIR}" -maxdepth 1 -name 'post_deploy_*.sql' | sort)

if [[ ${#SCRIPTS[@]} -eq 0 ]]; then
    echo "No post_deploy_*.sql scripts found in ${VERSIONS_DIR}"
    exit 0
fi

echo "Found ${#SCRIPTS[@]} post-deploy script(s):"
for script in "${SCRIPTS[@]}"; do
    echo "  - $(basename "${script}")"
done

if [[ ${DRY_RUN} -eq 1 ]]; then
    echo ""
    echo "Dry-run: nothing applied."
    exit 0
fi

echo ""
echo "Applying as klai superuser to ${HOST}:${CONTAINER} ..."
echo ""

for script in "${SCRIPTS[@]}"; do
    name="$(basename "${script}")"
    # Skip the rollback file — the operator runs it explicitly when
    # needed; running it on every deploy would undo the forward script
    # we just applied.
    if [[ "${name}" == *_rollback_* ]]; then
        echo "  [skip] ${name} (rollback script — apply manually only)"
        continue
    fi
    echo "  [apply] ${name}"
    ssh "${HOST}" "docker exec -i ${CONTAINER} psql -U klai -d klai -v ON_ERROR_STOP=1" \
        < "${script}" > /tmp/post_deploy_$$.log 2>&1 || {
        echo ""
        echo "=== FAILED: ${name} ==="
        cat /tmp/post_deploy_$$.log
        rm -f /tmp/post_deploy_$$.log
        exit 1
    }
    # Show only the last line (the SELECT 'status' marker emitted by
    # well-formed scripts) so success output stays compact.
    tail -n 3 /tmp/post_deploy_$$.log | sed 's/^/      /'
    rm -f /tmp/post_deploy_$$.log
done

echo ""
echo "✓ All post-deploy SQL applied."
