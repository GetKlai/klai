#!/usr/bin/env bash
# Runs rls-smoke-test.sql against the production postgres container on core-01
# and fails (non-zero exit) if any assertion tripped.
#
# Usage:
#     ./rls-smoke-test.sh
#     ./rls-smoke-test.sh --host staging-01   # alternate host
#     ./rls-smoke-test.sh --container klai-core-postgres-1
set -euo pipefail

HOST="core-01"
CONTAINER="klai-core-postgres-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --container) CONTAINER="$2"; shift 2 ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

SQL_FILE="${SCRIPT_DIR}/rls-smoke-test.sql"
if [[ ! -f "${SQL_FILE}" ]]; then
    echo "ERROR: ${SQL_FILE} not found" >&2
    exit 2
fi

echo "Running RLS smoke test against ${HOST}:${CONTAINER} ..."
output=$(ssh "${HOST}" "docker exec -i ${CONTAINER} psql -U portal_api -d klai -v ON_ERROR_STOP=1" < "${SQL_FILE}" 2>&1) || {
    echo "=== psql exited non-zero ==="
    echo "${output}"
    exit 1
}

echo "${output}"

# Explicitly verify every expected NOTICE fired — this catches a
# regression where we silently skipped an assertion block (e.g. DO $$
# body was commented out by accident).
required_notices=(
    "portal_knowledge_bases raised insufficient_privilege as expected"
    "vexa_meetings UPDATE raised insufficient_privilege as expected"
)
for notice in "${required_notices[@]}"; do
    if ! grep -Fq "${notice}" <<< "${output}"; then
        echo "=== MISSING expected NOTICE: ${notice} ==="
        echo "RLS smoke test FAILED."
        exit 1
    fi
done

if ! grep -Fq "RLS smoke test complete" <<< "${output}"; then
    echo "=== MISSING completion marker ==="
    echo "RLS smoke test FAILED."
    exit 1
fi

echo ""
echo "✓ RLS smoke test PASSED"
