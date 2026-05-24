#!/usr/bin/env bash
# verify_widget_cohort.sh — SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
#
# Runs the cohort-impact SQL for the allow_any_origin migration and exits
# non-zero if the deploy should abort (too many impacted widgets, or any
# external tenant is affected).
#
# Usage (from GitHub Actions SSH step):
#   bash scripts/verify_widget_cohort.sh
#
# Environment variables:
#   PLATFORM_ORG_SLUG  — slug(s) that are considered "internal". Comma-separated.
#                        Defaults to "getklai". Matches settings.platform_org_slug.
#   MAX_IMPACTED       — abort threshold for impacted_widgets. Defaults to 5.
#
# Exit codes:
#   0  — safe to proceed; cohort within limits
#   1  — abort; impacted_widgets > MAX_IMPACTED or external tenant affected

set -euo pipefail

PLATFORM_ORG_SLUG="${PLATFORM_ORG_SLUG:-getklai}"
MAX_IMPACTED="${MAX_IMPACTED:-5}"

# Convert comma-separated list to SQL IN-list: "getklai,klai" -> "'getklai','klai'"
IFS=',' read -ra SLUGS <<< "$PLATFORM_ORG_SLUG"
SQL_IN_LIST=$(printf "'%s'," "${SLUGS[@]}")
SQL_IN_LIST="${SQL_IN_LIST%,}"  # strip trailing comma

QUERY="
SELECT
  COUNT(*) FILTER (
    WHERE jsonb_array_length(COALESCE(widget_config->'allowed_origins', '[]'::jsonb)) = 0
      AND public_share_enabled = false
  ) AS impacted_widgets,
  COUNT(DISTINCT w.org_id) FILTER (
    WHERE jsonb_array_length(COALESCE(widget_config->'allowed_origins', '[]'::jsonb)) = 0
      AND public_share_enabled = false
      AND o.slug NOT IN (${SQL_IN_LIST})
  ) AS external_tenant_count
FROM widgets w
JOIN portal_orgs o ON o.id = w.org_id;
"

echo "[cohort-gate] Running widget cohort impact check..."
echo "[cohort-gate] Platform org slugs: ${PLATFORM_ORG_SLUG}"
echo "[cohort-gate] Max impacted threshold: ${MAX_IMPACTED}"

RESULT=$(docker exec klai-core-postgres-1 sh -c \
    "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -t -A -F'|' -c \"${QUERY}\"" 2>&1)

echo "[cohort-gate] Raw result: ${RESULT}"

IMPACTED_WIDGETS=$(echo "$RESULT" | cut -d'|' -f1 | tr -d ' ')
EXTERNAL_COUNT=$(echo "$RESULT" | cut -d'|' -f2 | tr -d ' ')

echo "[cohort-gate] impacted_widgets=${IMPACTED_WIDGETS}  external_tenant_count=${EXTERNAL_COUNT}"

if [ "${EXTERNAL_COUNT}" -gt 0 ]; then
    echo "[cohort-gate] ABORT: ${EXTERNAL_COUNT} external tenant(s) would be affected." >&2
    echo "[cohort-gate] REQ-2 requires the 7-day customer-communication protocol for external tenants." >&2
    echo "[cohort-gate] Open a follow-up SPEC before deploying." >&2
    exit 1
fi

if [ "${IMPACTED_WIDGETS}" -gt "${MAX_IMPACTED}" ]; then
    echo "[cohort-gate] ABORT: ${IMPACTED_WIDGETS} widgets impacted (threshold ${MAX_IMPACTED})." >&2
    echo "[cohort-gate] Re-run with MAX_IMPACTED=${IMPACTED_WIDGETS} after manual review," >&2
    echo "[cohort-gate] or open a follow-up SPEC with the 7-day communication protocol." >&2
    exit 1
fi

echo "[cohort-gate] OK — ${IMPACTED_WIDGETS} internal widget(s) impacted. Safe to proceed."
exit 0
