#!/usr/bin/env bash
# Apply every post_deploy_*.sql script in alembic/versions/ as the klai
# superuser. Each script must be idempotent (DROP POLICY IF EXISTS /
# CREATE OR REPLACE FUNCTION / etc.) — they are designed to run on every
# deploy, not just the first.
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
# Ordering
# --------
# Files are normally applied alphabetically, but a handful of scripts
# create helper objects (e.g. `public._rls_current_org_id()`) that
# LATER scripts depend on. On a fresh DB the alphabetical order would
# fail with "function ... does not exist". The BOOTSTRAP list below
# pins the dependency order — these files run FIRST (in list order),
# then the remaining files run alphabetically. Bootstrap files are
# idempotent and run a second time during the alphabetical pass; that
# is intentional (CREATE OR REPLACE / DROP IF EXISTS).
#
# Usage:
#     ./apply_post_deploy_sql.sh                              # production via ssh
#     ./apply_post_deploy_sql.sh --host staging-01            # alt host
#     ./apply_post_deploy_sql.sh --container my-postgres      # alt container
#     ./apply_post_deploy_sql.sh --local                      # local docker, no ssh
#     ./apply_post_deploy_sql.sh --local --container my-pg-1  # local + explicit ctr
#     ./apply_post_deploy_sql.sh --dry-run                    # list files, run nothing
#
# Idempotent: re-runs the full set every time. Total runtime in production
# is sub-second per script.
set -euo pipefail

HOST="core-01"
CONTAINER="klai-core-postgres-1"
LOCAL=0
DRY_RUN=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSIONS_DIR="$(cd "${SCRIPT_DIR}/../alembic/versions" && pwd)"

# Bootstrap files — these MUST run before the alphabetical pass because
# later files depend on objects they create. Order matters within this
# list. Keep entries narrow; only add a file when it's a hard dependency
# (function/type/role used by another post_deploy script). Each file
# stays idempotent so the alphabetical re-run is safe.
BOOTSTRAP_FILES=(
    "post_deploy_rls_raise_on_missing_context.sql"
)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --container) CONTAINER="$2"; shift 2 ;;
        --local) LOCAL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//; /^set -euo/d'
            exit 0
            ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

# Default the container name in --local mode to whatever docker ps finds.
# Auto-discovery only kicks in when the caller did not pass --container
# AND --local is set, otherwise we'd silently shadow an explicit override.
if [[ ${LOCAL} -eq 1 && "${CONTAINER}" == "klai-core-postgres-1" ]]; then
    DISCOVERED=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^[a-z0-9_-]+-postgres-1$' | head -1 || true)
    if [[ -n "${DISCOVERED}" ]]; then
        CONTAINER="${DISCOVERED}"
    fi
fi

# Build the full execution order: bootstrap first (preserving list order),
# then everything else in alphabetical order. We let bootstrap files
# appear twice in the SCRIPTS array — the second run is a no-op for the
# idempotent ones, and the visibility helps operators see they ran.
# bash 3.2 compatible (macOS default) — no `mapfile`.
ALL_SQL=()
while IFS= read -r f; do
    ALL_SQL+=("$f")
done < <(find "${VERSIONS_DIR}" -maxdepth 1 -name 'post_deploy_*.sql' | sort)

if [[ ${#ALL_SQL[@]} -eq 0 ]]; then
    echo "No post_deploy_*.sql scripts found in ${VERSIONS_DIR}"
    exit 0
fi

SCRIPTS=()
for boot in "${BOOTSTRAP_FILES[@]}"; do
    boot_path="${VERSIONS_DIR}/${boot}"
    if [[ -f "${boot_path}" ]]; then
        SCRIPTS+=("${boot_path}")
    fi
done
for f in "${ALL_SQL[@]}"; do
    SCRIPTS+=("${f}")
done

echo "Found ${#ALL_SQL[@]} post-deploy script(s); ${#BOOTSTRAP_FILES[@]} bootstrap file(s) pinned to run first."
for script in "${SCRIPTS[@]}"; do
    echo "  - $(basename "${script}")"
done

if [[ ${DRY_RUN} -eq 1 ]]; then
    echo ""
    echo "Dry-run: nothing applied."
    exit 0
fi

echo ""
if [[ ${LOCAL} -eq 1 ]]; then
    echo "Applying as klai superuser locally to container ${CONTAINER} ..."
else
    echo "Applying as klai superuser to ${HOST}:${CONTAINER} ..."
fi
echo ""

# Helper: route a psql invocation through ssh (prod) or docker exec (local).
run_psql() {
    local sql_file="$1"
    if [[ ${LOCAL} -eq 1 ]]; then
        docker exec -i "${CONTAINER}" psql -U klai -d klai -v ON_ERROR_STOP=1 < "${sql_file}"
    else
        ssh "${HOST}" "docker exec -i ${CONTAINER} psql -U klai -d klai -v ON_ERROR_STOP=1" \
            < "${sql_file}"
    fi
}

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
    run_psql "${script}" > /tmp/post_deploy_$$.log 2>&1 || {
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
echo "All post-deploy SQL applied."
