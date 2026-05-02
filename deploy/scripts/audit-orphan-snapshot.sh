#!/usr/bin/env bash
# /opt/klai/scripts/audit-orphan-snapshot.sh
#
# Post-deploy orphan-snapshot. Emits structlog-events to stdout (picked
# up by Alloy → VictoriaLogs) for any running container that does NOT
# carry one of the canonical management labels:
#
#   - klasse A: com.docker.compose.project=klai-core
#   - klasse B: klai.managed_by=portal-api-provisioning
#   - opt-in:   klai.adhoc=*  (REQ-7 ad-hoc debug containers)
#
# REPORT-ONLY. Never deletes anything.
#
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2d.
#
# Usage:
#   audit-orphan-snapshot.sh                    — full snapshot
#   audit-orphan-snapshot.sh <service-or-all>   — context tag for the
#                                                 emitted events

set -euo pipefail

CONTEXT="${1:-all}"
TIMESTAMP="$(date -Iseconds)"
EVENT_COUNT=0

emit_event() {
    local event_type="$1"
    local container_name="$2"
    local image="$3"
    # Plain JSON line — no jq dependency. Alloy parses by default.
    printf '{"service":"klai-orphan-snapshot","level":"warning","event":"%s","container_name":"%s","image":"%s","deploy_context":"%s","_time":"%s"}\n' \
        "$event_type" "$container_name" "$image" "$CONTEXT" "$TIMESTAMP"
    EVENT_COUNT=$((EVENT_COUNT + 1))
}

# Iterate over running containers
docker ps --format '{{.Names}}' | while read -r name; do
    [[ -z "$name" ]] && continue

    proj=$(docker inspect "$name" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || echo "")
    managed_by=$(docker inspect "$name" --format '{{index .Config.Labels "klai.managed_by"}}' 2>/dev/null || echo "")
    adhoc=$(docker inspect "$name" --format '{{index .Config.Labels "klai.adhoc"}}' 2>/dev/null || echo "")
    image=$(docker inspect "$name" --format '{{.Config.Image}}' 2>/dev/null || echo "unknown")

    # Klasse A or B or ad-hoc → legitimate, skip
    if [[ "$proj" == "klai-core" ]] || [[ "$managed_by" == "portal-api-provisioning" ]] || [[ -n "$adhoc" ]]; then
        continue
    fi

    emit_event "orphan_post_deploy" "$name" "$image"
done

# Always emit a run-completed marker so absence-of-events is
# distinguishable from absence-of-runs in Grafana queries.
printf '{"service":"klai-orphan-snapshot","level":"info","event":"snapshot_run_completed","deploy_context":"%s","orphan_count":%d,"_time":"%s"}\n' \
    "$CONTEXT" "$EVENT_COUNT" "$TIMESTAMP"
