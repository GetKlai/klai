#!/usr/bin/env bash
# push-health-public01.sh — Push public-01 service health to Uptime Kuma
#
# Runs every minute via cron (root).
# Same pattern as push-health.sh on core-01.
#
# To add a new service:
#   1. Create a push monitor in Uptime Kuma, copy the token
#   2. Add KUMA_TOKEN_<NAME>=<token> to /opt/klai/.env on public-01
#   3. Add push_healthcheck or push_exec line below
#   4. No crontab change needed
set -uo pipefail

[ -f /opt/klai/.env ] && source /opt/klai/.env

KUMA="${UPTIME_KUMA_PUSH_URL:-https://status.getklai.com/api/push}"
LOG=/opt/klai/logs/health.log

mkdir -p /opt/klai/logs

# Resolve container name by Coolify resource name label (stable across redeploys).
# subName narrows to the primary container when a Coolify resource has multiple
# (e.g. twenty bundles worker + postgres + redis under the same resourceName).
resolve_container() {
    docker ps --filter "label=coolify.resourceName=$1" --format "{{.Names}}" | head -1
}
resolve_coolify_subname() {
    docker ps --filter "label=coolify.service.subName=$1" --format "{{.Names}}" | head -1
}

# Resolve container by compose service label (for non-Coolify services like Alloy).
resolve_compose_container() {
    docker ps --filter "label=com.docker.compose.service=$1" --format "{{.Names}}" | head -1
}

# Push based on Docker-native healthcheck status
push_healthcheck() {
    local container="$1" token="$2" label="$3"
    [ -z "$token" ] && return
    local health
    if [ -z "$container" ]; then
        curl -sf "${KUMA}/${token}?status=down&msg=container-not-found" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: container not found" >> "$LOG"
        return
    fi
    health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")
    if [ "$health" = "healthy" ]; then
        curl -sf "${KUMA}/${token}?status=up&msg=OK" -o /dev/null
    else
        curl -sf "${KUMA}/${token}?status=down&msg=${health}" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: ${health}" >> "$LOG"
    fi
}

# Push based on container state (for containers without Docker healthcheck).
push_running() {
    local container="$1" token="$2" label="$3" probe_cmd="${4:-}"
    [ -z "$token" ] && return
    if [ -z "$container" ]; then
        curl -sf "${KUMA}/${token}?status=down&msg=container-not-found" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: container not found" >> "$LOG"
        return
    fi
    local state
    state=$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null || echo "missing")
    if [ "$state" != "running" ]; then
        curl -sf "${KUMA}/${token}?status=down&msg=${state}" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: ${state}" >> "$LOG"
        return
    fi
    # Optional in-container probe for deeper health beyond "process is up"
    if [ -n "$probe_cmd" ]; then
        if docker exec "$container" sh -c "$probe_cmd" &>/dev/null; then
            curl -sf "${KUMA}/${token}?status=up&msg=OK" -o /dev/null
        else
            curl -sf "${KUMA}/${token}?status=down&msg=probe-failed" -o /dev/null
            echo "$(date -Iseconds) WARN ${label}: probe failed" >> "$LOG"
        fi
    else
        curl -sf "${KUMA}/${token}?status=up&msg=running" -o /dev/null
    fi
}

# ── Resolve containers ────────────────────────────────────────────────────────

UMAMI=$(resolve_container "umami-analytics")
TWENTY=$(resolve_coolify_subname "twenty")   # twenty resource bundles worker/postgres/redis — narrow to the web container
ALLOY=$(resolve_compose_container "alloy")

# ── Services ──────────────────────────────────────────────────────────────────

# Umami: web analytics (Docker healthcheck available)
push_healthcheck "$UMAMI" "${KUMA_TOKEN_UMAMI:-}" "Web analytics"

# Twenty CRM: team CRM tool (Docker healthcheck available)
push_healthcheck "$TWENTY" "${KUMA_TOKEN_TWENTY:-}" "Twenty CRM"

# Alloy: log shipper for public-01 services. No probe tools in the Alloy image
# (wget/curl absent) and http binds to 127.0.0.1 — use bash built-in TCP test.
push_running "$ALLOY" "${KUMA_TOKEN_ALLOY:-}" "Log shipper public-01 (Alloy)" \
    "bash -c 'exec 3<>/dev/tcp/localhost/12345 && exec 3<&-'"
