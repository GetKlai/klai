#!/usr/bin/env bash
# audit-alert-uid-length.sh — fail if any Grafana provisioning UID exceeds
# the 40-character limit that Grafana enforces on alert-rule and dashboard
# UIDs (and silently rejects with `UID is longer than 40 symbols` at
# provisioning time).
#
# Why this exists: SPEC-INFRA-CONTAINER-HYGIENE-001 stage 6 first deploy
# (PR #296, 2026-05-04 12:11 CEST) used UIDs of 49–55 chars
# (`spec-infra-container-hygiene-001-tenant-no-route` etc.) and crashed
# Grafana into a restart loop the moment deploy-compose.yml synced the
# files. Grafana's provisioning is fail-loud — a single oversized UID
# refuses the entire provisioning step. Recovery required revert (#297)
# + manual rm of the synced-but-not-deleted files on core-01 (rsync
# without --delete keeps stale destination files).
#
# This guard runs in `Alerting provisioning checks` CI on every PR that
# touches `deploy/grafana/provisioning/alerting/**` or
# `deploy/grafana/provisioning/dashboards/**` and fails the build if any
# UID is over the limit.
#
# Reference: pitfall `grafana-uid-40-char-limit (HIGH)` in
# .claude/rules/klai/pitfalls/process-rules.md.

set -euo pipefail

LIMIT=40
ERRORS=0

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

# Dependencies: jq is required for robust JSON top-level-uid extraction.
# The alerting-check workflow installs jq in its first step. Locally jq
# is near-universal — ubuntu/macOS default toolboxes both include it.
command -v jq >/dev/null 2>&1 || {
    red "FAIL: jq is required (sudo apt install jq / brew install jq)"
    exit 2
}

check_uid() {
    local uid="$1" file="$2" line="${3:-?}"
    local len=${#uid}
    if [[ "$len" -gt "$LIMIT" ]]; then
        red "FAIL: $file:$line — UID '$uid' has $len chars (limit $LIMIT)"
        ERRORS=$((ERRORS + 1))
    else
        echo "OK: $file:$line — $uid ($len chars)"
    fi
}

echo "─── Alerting YAML uid: fields ───"
# Match `uid: <value>` or `uid: "<value>"` lines under groups[].rules[].
# Tolerant to single/double-quoted and unquoted forms; ignores
# datasourceUid (which references the datasource by its own UID —
# different namespace, managed via datasources.yaml).
shopt -s globstar nullglob
for file in deploy/grafana/provisioning/alerting/**/*.yaml deploy/grafana/provisioning/alerting/**/*.yml; do
    [[ -f "$file" ]] || continue
    while IFS=: read -r line content; do
        # Capture value of `uid: ...`, stripping leading/trailing
        # whitespace + quotes. Both `- uid: <v>` (rule-level) and
        # `uid: <v>` (model-level) shapes match. Skip `datasourceUid:` —
        # different field, different limit considerations.
        if [[ "$content" =~ ^[[:space:]]*-?[[:space:]]*uid:[[:space:]]+(.+)$ ]]; then
            value="${BASH_REMATCH[1]}"
            value="${value#\"}"; value="${value%\"}"
            value="${value#\'}"; value="${value%\'}"
            value="${value%%#*}"
            value="${value%"${value##*[![:space:]]}"}"
            check_uid "$value" "$file" "$line"
        fi
    done < <(grep -nE '^[[:space:]]*-?[[:space:]]*uid:[[:space:]]+' "$file")
done

echo ""
echo "─── Dashboard JSON top-level \"uid\" ───"
# jq extracts only the TOP-LEVEL uid. Nested uids (datasource refs in
# panels) are not subject to the dashboard-uid 40-char limit — they
# point at a datasource configured in datasources.yaml. Use `// empty`
# so dashboards without a top-level uid (rare; node-metrics.json) are
# silently skipped, not flagged.
# Bash globstar `**` already includes the directory itself, so a
# separate `*.json` pattern would double-count.
for file in deploy/grafana/provisioning/dashboards/**/*.json; do
    [[ -f "$file" ]] || continue
    value=$(jq -r '.uid // empty' "$file" 2>/dev/null || echo "")
    if [[ -z "$value" ]]; then
        yellow "SKIP: $file — no top-level uid"
        continue
    fi
    check_uid "$value" "$file" ""
done

echo ""
if [[ "$ERRORS" -gt 0 ]]; then
    red "FAIL: $ERRORS UID(s) exceed Grafana's $LIMIT-char limit."
    red "      Grafana refuses to provision these — deploy will crash-loop."
    red "      See pitfall \`grafana-uid-40-char-limit (HIGH)\` in process-rules.md."
    exit 1
fi
green "OK: all alert-rule and dashboard UIDs are within the $LIMIT-char limit."
