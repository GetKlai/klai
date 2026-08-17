#!/usr/bin/env bash
# Self-test for verify-alert-datasource-access.py's parser.
#
# The DB half of that script cannot run here (no postgres, by design -- an
# earlier guard in this repo shipped with a mockable target and passed against
# an image that did not exist). What IS testable offline is the fragile half:
# which relations a rule really reads. Get that wrong and the checker silently
# skips the relation that is actually broken, which is the same failure mode it
# exists to prevent.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/deploy/scripts/verify-alert-datasource-access.py"
PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c 'import yaml' 2>/dev/null; then
  echo "FAIL: $PYTHON cannot import yaml -- install python3-yaml (the checker needs it on core-01 too)" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

ok() { echo "  ok: $1"; pass=$((pass + 1)); }
bad() { echo "  FAIL: $1" >&2; fail=$((fail + 1)); }

plan() { "$PYTHON" "$SCRIPT" --plan "$1" 2>&1; }

# ── 1. The repo's own rules parse, and CTEs are not mistaken for relations ──
echo "1. real alerting directory"
if out="$(plan "$REPO_ROOT/deploy/grafana/provisioning/alerting")"; then
  ok "exits 0"
else
  bad "exits non-zero: $out"
fi
for expected in \
  "spec-kb-015-feedback-correlation-low	portal_feedback_correlation_stats" \
  "spec-kb-015-feedback-correlation-low	portal_orgs" \
  "spec-priv-001-tenant-stuck-full	portal_audit_log" \
  "rag-eval-001-faithfulness-low	knowledge.rag_eval_results"; do
  if printf '%s\n' "$out" | grep -qF "$expected"; then
    ok "finds ${expected//	/ -> }"
  else
    bad "missing ${expected//	/ -> }"
  fi
done
# rag-eval wraps its query in `WITH last_two AS (...), ranked AS (...)`. A CTE
# name appears after FROM exactly like a table does, but has no grants -- a
# count on one would error and be reported as a finding that does not exist.
for cte in last_two ranked; do
  if printf '%s\n' "$out" | grep -qE "[[:space:]]$cte$"; then
    bad "CTE '$cte' leaked into the relation list"
  else
    ok "CTE '$cte' excluded"
  fi
done

# ── 2. A directory with no Postgres rules must refuse, not report success ──
echo "2. no Postgres-datasource rules"
mkdir -p "$TMP/empty"
cat >"$TMP/empty/only-logs.yaml" <<'YAML'
apiVersion: 1
groups:
  - name: logs-only
    rules:
      - uid: some-victorialogs-rule
        data:
          - refId: query
            model:
              refId: query
              datasource:
                type: victoriametrics-logs-datasource
                uid: victorialogs
              expr: 'service:portal-api'
YAML
set +e
plan "$TMP/empty" >"$TMP/out2" 2>&1
rc=$?
set -e
if [ "$rc" -eq 2 ]; then
  ok "exits 2 rather than claiming success on an empty check"
else
  bad "expected exit 2, got $rc: $(cat "$TMP/out2")"
fi

# ── 3. A stale allowlist entry must fail ──────────────────────────────────
# KNOWN_BLIND records rules we know are blind. If a uid in there no longer
# exists, the entry is a lie that makes a future reader think a rule is
# accounted for. This fixture has a Postgres rule but not the allowlisted uid.
echo "3. stale KNOWN_BLIND entry"
mkdir -p "$TMP/stale"
cat >"$TMP/stale/rules.yaml" <<'YAML'
apiVersion: 1
groups:
  - name: pg-only
    rules:
      - uid: some-other-rule
        data:
          - refId: query
            model:
              refId: query
              datasource:
                type: postgres
                uid: portal-postgres
              format: table
              rawSql: |
                SELECT count(*) AS value FROM portal_orgs
YAML
set +e
plan "$TMP/stale" >"$TMP/out3" 2>&1
rc=$?
set -e
if [ "$rc" -eq 1 ] && grep -q "no longer exist" "$TMP/out3"; then
  ok "exits 1 and names the stale uid"
else
  bad "expected exit 1 naming the stale uid, got $rc: $(cat "$TMP/out3")"
fi

# ── 4. Schema-qualified names and aliases survive extraction ──────────────
echo "4. schema qualification and aliases"
mkdir -p "$TMP/shapes"
cat >"$TMP/shapes/rules.yaml" <<'YAML'
apiVersion: 1
groups:
  - name: shapes
    rules:
      - uid: spec-priv-001-tenant-stuck-full
        data:
          - refId: query
            model:
              refId: query
              datasource:
                type: postgres
                uid: portal-postgres
              format: table
              rawSql: |
                WITH recent AS (
                  SELECT id FROM some_schema.some_table WHERE x > 1
                )
                SELECT count(*) AS value
                  FROM portal_orgs o
                  JOIN recent r ON r.id = o.id
                 WHERE o.id IN (SELECT org_id FROM nested_table)
YAML
out4="$(plan "$TMP/shapes")"
for expected in some_schema.some_table portal_orgs nested_table; do
  if printf '%s\n' "$out4" | grep -qE "[[:space:]]$expected$"; then
    ok "extracts $expected"
  else
    bad "missed $expected"
  fi
done
if printf '%s\n' "$out4" | grep -qE "[[:space:]]recent$"; then
  bad "CTE 'recent' leaked (aliased JOIN onto a CTE)"
else
  ok "aliased CTE 'recent' excluded"
fi

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
