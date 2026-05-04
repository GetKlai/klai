#!/usr/bin/env bash
# reset-grafana-orphan-alert.sh — delete an orphaned alert rule from Grafana's
# SQLite database.
#
# Why this exists: file-based alert provisioning in Grafana is purely
# ADDITIVE. Changing a rule's `uid:` in YAML creates a NEW rule with the
# new uid; the old rule (keyed by its old uid) stays behind as an orphan
# with provenance="file" even though nothing in the filesystem references
# it anymore. The Grafana 13 API refuses to delete file-provisioned rules
# (409 alerting.provenanceMismatch), and `X-Disable-Provenance: true`
# header does not bypass this cleanly.
#
# The cleanup path: stop Grafana briefly, exec sqlite3 against its
# embedded DB from a throwaway alpine container that mounts the volume
# RW, DELETE the orphan row, restart Grafana.
#
# Usage:
#   ./scripts/reset-grafana-orphan-alert.sh <orphan-uid>
#
# Example:
#   ./scripts/reset-grafana-orphan-alert.sh obs-001-caddy-5xx-count-high
#
# Runs on any machine that can `ssh core-01`. Grafana downtime ~10-15s.

set -euo pipefail

UID_TO_DELETE="${1:-}"
if [[ -z "${UID_TO_DELETE}" ]]; then
  echo "usage: $0 <orphan-uid>" >&2
  exit 2
fi

# Defense against foot-gun: the orphan uid MUST look like an alert rule uid
# (prefix like obs-001, spec-sec-024, spec-infra-005). Refuses to run on
# arbitrary strings that could be an accident.
if ! [[ "${UID_TO_DELETE}" =~ ^(obs-[0-9]+|spec-[a-z]+-[0-9]+)- ]]; then
  echo "refusing to delete uid '${UID_TO_DELETE}' — doesn't match expected prefix pattern" >&2
  exit 2
fi

echo "[1/4] Preview: rules matching UID ${UID_TO_DELETE} on server..."
ssh core-01 "
  sudo docker run --rm --user root \
    -v klai-core_grafana-data:/db:ro alpine:3 \
    sh -c 'apk add -q sqlite && sqlite3 /db/grafana.db \"
      SELECT id, uid, title, provenance FROM alert_rule WHERE uid = \\\"${UID_TO_DELETE}\\\"
    \"'
"

echo ""
echo "[2/4] Stopping Grafana (brief downtime, ~10s)..."
ssh core-01 "docker stop klai-core-grafana-1 >/dev/null"

echo ""
echo "[3/4] Deleting orphan row..."
ssh core-01 "
  sudo docker run --rm --user root \
    -v klai-core_grafana-data:/db:rw alpine:3 \
    sh -c 'apk add -q sqlite && sqlite3 /db/grafana.db \"
      DELETE FROM alert_rule WHERE uid = \\\"${UID_TO_DELETE}\\\";
      SELECT \\\"deleted rows: \\\" || changes();
    \"'
"

echo ""
echo "[4/4] Restarting Grafana..."
ssh core-01 "docker start klai-core-grafana-1 >/dev/null"

echo ""
echo "Orphan ${UID_TO_DELETE} cleanup complete. Grafana should be healthy"
echo "within ~30s — verify with:"
echo "  ssh core-01 \"docker ps --filter name=grafana --format '{{.Status}}'\""
