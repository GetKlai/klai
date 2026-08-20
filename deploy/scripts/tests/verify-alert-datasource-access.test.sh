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
  "spec-priv-001-tenant-stuck-full	portal_telemetry_mode_changes" \
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

# ── 3. Every allowlist entry is valid, and its validator rejects rot ───────
# KNOWN_BLIND records rules we know are blind. Two ways it goes bad: an entry
# whose rule no longer exists (a lie), and an entry nobody ever revisits (a
# permanent exception wearing a reason). Both must fail CI.
echo "3. KNOWN_BLIND hygiene"
if plan "$REPO_ROOT/deploy/grafana/provisioning/alerting" >/dev/null; then
  ok "all current exemptions pass live-uid, statement and expiry validation"
else
  bad "one or more current exemptions are malformed, expired or stale"
fi

# Drive the validator directly with hostile entries; building fixture YAML for
# each case would exercise the parser, not the rot rules.
cat >"$TMP/val.py" <<'PY'
import json, pathlib, sys
src = pathlib.Path(sys.argv[1]).read_text()
ns = {}
exec(compile(src, "chk", "exec"), ns)
ns["KNOWN_BLIND"].clear()
ns["KNOWN_BLIND"].update(json.loads(sys.argv[2]))
problems = ns["_validate_allowlist"]({"live-rule"})
print("\n".join(problems))
sys.exit(1 if problems else 0)
PY

check_rejected() {
  local label="$1" entries="$2" expect="$3"
  set +e
  out="$("$PYTHON" "$TMP/val.py" "$SCRIPT" "$entries" 2>&1)"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qi "$expect"; then
    ok "rejects $label"
  else
    bad "should reject $label (rc=$rc): $out"
  fi
}

FUTURE="$("$PYTHON" -c 'import datetime;print(datetime.date.today()+datetime.timedelta(days=30))')"
PAST="$("$PYTHON" -c 'import datetime;print(datetime.date.today()-datetime.timedelta(days=1))')"
FAR="$("$PYTHON" -c 'import datetime;print(datetime.date.today()+datetime.timedelta(days=400))')"
LONG="a reason long enough to clear the forty character minimum"

check_rejected "a uid that no longer exists" \
  "{\"ghost-rule\": {\"statement\": \"$LONG\", \"expired_at\": \"$FUTURE\"}}" "no longer excuses"
check_rejected "a too-short statement" \
  "{\"live-rule\": {\"statement\": \"known issue\", \"expired_at\": \"$FUTURE\"}}" "at least 40"
check_rejected "a missing expiry" \
  "{\"live-rule\": {\"statement\": \"$LONG\"}}" "expired_at"
check_rejected "an expiry already past" \
  "{\"live-rule\": {\"statement\": \"$LONG\", \"expired_at\": \"$PAST\"}}" "expired on"
check_rejected "an expiry indistinguishable from permanent" \
  "{\"live-rule\": {\"statement\": \"$LONG\", \"expired_at\": \"$FAR\"}}" "permanent"

set +e
"$PYTHON" "$TMP/val.py" "$SCRIPT" "{\"live-rule\": {\"statement\": \"$LONG\", \"expired_at\": \"$FUTURE\"}}" >/dev/null 2>&1
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  ok "accepts a well-formed exemption"
else
  bad "a live uid with a long statement and a near-future expiry should pass"
fi

# The deploy invokes the checker without --plan. Keep that real mode behind the
# same validator; otherwise CI can validate exemptions while production ignores
# an entry that expired between review and deploy.
mkdir -p "$TMP/runtime"
cat >"$TMP/runtime/rules.yaml" <<'YAML'
apiVersion: 1
groups:
  - name: runtime
    rules:
      - uid: live-rule
        data:
          - model:
              datasource:
                type: postgres
              rawSql: SELECT count(*) FROM live_table
YAML
cat >"$TMP/run.py" <<'PY'
import json, pathlib, sys
src = pathlib.Path(sys.argv[1]).read_text()
ns = {}
exec(compile(src, "chk", "exec"), ns)
ns["KNOWN_BLIND"].clear()
ns["KNOWN_BLIND"].update(json.loads(sys.argv[3]))
ns["_check_rule"] = lambda _uid, _sql: []
sys.exit(ns["main"](["chk", sys.argv[2]]))
PY

set +e
out="$($PYTHON "$TMP/run.py" "$SCRIPT" "$TMP/runtime" \
  "{\"live-rule\": {\"statement\": \"$LONG\", \"expired_at\": \"$PAST\"}}" 2>&1)"
rc=$?
set -e
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qi "expired on"; then
  ok "real run mode rejects an expired exemption before datasource checks"
else
  bad "real run mode should reject an expired exemption (rc=$rc): $out"
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

# ── 5. Prose in SQL comments is not a relation ────────────────────────────
# The regex matches FROM/JOIN anywhere, and English is full of both words. A
# comment reading "from the day it was provisioned" made the checker look for a
# table called "the", fail, and report a working rule as blind.
echo "5. comments are stripped before matching"
mkdir -p "$TMP/comments"
cat >"$TMP/comments/rules.yaml" <<'YAML'
apiVersion: 1
groups:
  - name: commented
    rules:
      - uid: commented-rule
        data:
          - refId: query
            model:
              refId: query
              datasource:
                type: postgres
                uid: portal-postgres
              format: table
              rawSql: |
                -- Reads the view, not the base table: blind from the day it
                -- shipped, and join us in never doing that again.
                /* block comment: select from nowhere, join nothing */
                SELECT count(*) AS value FROM real_view
YAML
out5="$(plan "$TMP/comments")"
if printf '%s\n' "$out5" | grep -qE "[[:space:]]real_view$"; then
  ok "extracts the real relation"
else
  bad "missed real_view: $out5"
fi
for ghost in the us nowhere nothing; do
  if printf '%s\n' "$out5" | grep -qE "[[:space:]]$ghost$"; then
    bad "prose word '$ghost' extracted as a relation"
  else
    ok "prose word '$ghost' ignored"
  fi
done

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
