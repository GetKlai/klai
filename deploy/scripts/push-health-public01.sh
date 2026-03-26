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
resolve_container() {
    docker ps --filter "label=coolify.resourceName=$1" --format "{{.Names}}" | head -1
}

# Push based on Docker-native healthcheck status
push_healthcheck() {
    local container="$1" token="$2" label="$3"
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

# ── Resolve containers ────────────────────────────────────────────────────────

UMAMI=$(resolve_container "umami-analytics")

# ── Services ──────────────────────────────────────────────────────────────────

# Umami: web analytics (Docker healthcheck available)
push_healthcheck "$UMAMI" "${KUMA_TOKEN_UMAMI}" "Web analytics"
