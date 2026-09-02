#!/usr/bin/env bash
# Complete the database portion of `make dev-bootstrap` after Docker startup.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 POSTGRES_CONTAINER" >&2
    exit 2
fi

CONTAINER="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSIONS_DIR="${REPO_ROOT}/klai-portal/backend/alembic/versions"
FUNCTION_SOURCE="${VERSIONS_DIR}/post_deploy_rls_raise_on_missing_context.sql"
FUNCTION_SQL="$(mktemp "${TMPDIR:-/tmp}/klai-rls-function.XXXXXX")"
SQL_LOG="$(mktemp "${TMPDIR:-/tmp}/klai-post-deploy.XXXXXX")"
trap 'rm -f "${FUNCTION_SQL}" "${SQL_LOG}"' EXIT

is_known_widget_gap() {
    # Classify by the error, not the filename: a filename allowlist drifted the
    # day it was written (one member succeeds, the actual failer was missing).
    # The known gap is precisely "a widget_* table this chain does not create".
    grep -Eiq 'relation "?widget_[a-z_]+"? does not exist' "${SQL_LOG}"
}

echo "Waiting for PostgreSQL in ${CONTAINER}..."
postgres_ready=0
for _attempt in {1..30}; do
    if docker exec "${CONTAINER}" pg_isready -U klai -d klai >/dev/null 2>&1; then
        postgres_ready=1
        break
    fi
    sleep 1
done
if [[ ${postgres_ready} -ne 1 ]]; then
    echo "[ERROR] [REQUIRED] PostgreSQL did not become ready within 30 seconds." >&2
    exit 1
fi
echo "[OK] [REQUIRED] PostgreSQL is ready."

echo ""
echo "==> [2/4] Installing the migration prerequisite..."
awk '
    /^CREATE OR REPLACE FUNCTION _rls_current_org_id\(\)/ { capture = 1 }
    capture { print }
    capture && /^\$\$;$/ { exit }
' "${FUNCTION_SOURCE}" > "${FUNCTION_SQL}"

if ! grep -q '^CREATE OR REPLACE FUNCTION _rls_current_org_id()' "${FUNCTION_SQL}" ||
   [[ "$(tail -n 1 "${FUNCTION_SQL}")" != '$$;' ]]; then
    echo "[ERROR] [REQUIRED] Could not extract the complete _rls_current_org_id() definition." >&2
    exit 1
fi

if docker exec -i "${CONTAINER}" psql -U klai -d klai -v ON_ERROR_STOP=1 < "${FUNCTION_SQL}"; then
    echo "[OK] [REQUIRED] _rls_current_org_id() definition applied."
else
    echo "[ERROR] [REQUIRED] Failed to apply _rls_current_org_id(); migrations were not run." >&2
    exit 1
fi

echo ""
echo "==> [3/4] Running Alembic migrations..."
if (cd "${REPO_ROOT}" && make --no-print-directory migrate); then
    echo "[OK] [REQUIRED] Alembic migrations reached head."
else
    echo "[ERROR] [REQUIRED] Alembic migrations failed; post-deploy SQL was not run." >&2
    exit 1
fi

echo ""
echo "==> [4/4] Applying post-deploy SQL..."
success_count=0
known_failure_count=0
unexpected_failure_count=0
skipped_count=0

while IFS= read -r sql_file; do
    name="$(basename "${sql_file}")"
    if [[ "${name}" == *_rollback_* ]]; then
        echo "  [SKIP] ${name} (rollback)"
        skipped_count=$((skipped_count + 1))
        continue
    fi

    if docker exec -i "${CONTAINER}" psql -U klai -d klai -v ON_ERROR_STOP=1 \
        < "${sql_file}" > "${SQL_LOG}" 2>&1; then
        echo "  [OK] ${name}"
        tail -n 3 "${SQL_LOG}" | sed 's/^/       /'
        success_count=$((success_count + 1))
    elif is_known_widget_gap "${name}"; then
        echo "  [WARN] [TOLERATED] ${name} failed (widget tables are not created by this bootstrap chain)."
        sed 's/^/       /' "${SQL_LOG}"
        known_failure_count=$((known_failure_count + 1))
    else
        echo "  [ERROR] [REQUIRED] ${name} failed unexpectedly." >&2
        sed 's/^/       /' "${SQL_LOG}" >&2
        unexpected_failure_count=$((unexpected_failure_count + 1))
    fi
done < <(find "${VERSIONS_DIR}" -maxdepth 1 -type f -name 'post_deploy_*.sql' | sort)

echo ""
echo "Post-deploy summary: ${success_count} succeeded, ${known_failure_count} tolerated widget failure(s), ${unexpected_failure_count} unexpected failure(s), ${skipped_count} rollback file(s) skipped."

if [[ ${unexpected_failure_count} -ne 0 ]]; then
    echo "[ERROR] [REQUIRED] Bootstrap failed because unexpected post-deploy SQL errors occurred." >&2
    exit 1
fi
if [[ ${known_failure_count} -ne 0 ]]; then
    echo "[WARN] [TOLERATED] Widget SQL failures were reported above and did not fail the bootstrap."
fi

echo ""
echo "============================================"
echo "  Local database bootstrap complete."
echo ""
echo "  Next steps (run in separate terminals):"
echo "    make backend"
echo "    make frontend"
echo "============================================"
