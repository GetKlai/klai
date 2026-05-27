#!/usr/bin/env bash
# audit-monitor-coverage.sh — CI guard against monitor drift.
#
# Every compose service in deploy/docker-compose.yml that runs a long-lived
# user-impacting process MUST be referenced from deploy/scripts/push-health.sh
# (as a resolve_container call) or be on the explicit IGNORE list below
# with a documented reason.
#
# Failure modes this prevents:
# - New service added to compose without a Kuma monitor → silent gap
# - Service renamed in compose but resolver in push-health.sh not updated
#
# Usage:
#   ./scripts/audit-monitor-coverage.sh         # exit 1 on uncovered service
#   ./scripts/audit-monitor-coverage.sh --list  # show coverage matrix

set -eo pipefail

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || dirname "$(dirname "$(readlink -f "$0")")")
COMPOSE="$ROOT/deploy/docker-compose.yml"
PUSH_HEALTH="$ROOT/deploy/scripts/push-health.sh"
GPU_HEALTH="$ROOT/deploy/scripts/gpu-health.sh"
GPU_COMPOSE="$ROOT/deploy/docker-compose.gpu.yml"
PUSH_HEALTH_PUBLIC="$ROOT/deploy/scripts/push-health-public01.sh"

# Services intentionally NOT individually monitored.
# Format: "name|rationale" — one per line. Use a plain array for bash 3 (macOS) compat.
IGNORE_LIST=(
    # ── Pure internal infra, no user-facing failure mode ──
    "cadvisor|metrics collector, scraped by victoriametrics"
    "docker-socket-proxy|sidecar for socket access, lockstep with portal-api"
    "runtime-api-socket-proxy|sidecar for runtime-api → docker access"

    # ── Dependency sidecars, implicitly monitored via parent service ──
    "firecrawl-postgres|sidecar of firecrawl-api (Web Content Fetcher)"
    "firecrawl-rabbitmq|sidecar of firecrawl-api"
    "vexa-redis|sidecar of vexa V3 stack (Meeting service)"
    "glitchtip-worker|sidecar of glitchtip-web (Error tracking)"

    # ── Other valid reasons ──
    "zitadel|covered by 'Login system' http monitor on auth.getklai.com"
    "librechat-getklai|covered by 'Chat' product monitor (probed by name)"
    "caddy|covered by 'Website' + per-app http monitors (Caddy = reverse proxy)"
    "grafana|covered by 'Monitoring' http monitor on grafana.getklai.com"
    "glitchtip-migrate|one-shot init container (db migration), exits 0 on success"
    "glitchtip-web|covered by 'Error tracking' http monitor on errors.getklai.com"
)

ignore_reason() {
    local svc="$1" entry
    for entry in "${IGNORE_LIST[@]}"; do
        if [ "${entry%%|*}" = "$svc" ]; then
            echo "${entry#*|}"
            return 0
        fi
    done
    return 1
}

# Extract top-level service names from a compose file (skip nested 'services:').
extract_services() {
    local file="$1"
    awk '
        /^services:/ { in_services=1; next }
        in_services && /^[a-zA-Z]/ { in_services=0 }
        in_services && /^  [a-zA-Z][a-zA-Z0-9_-]*:/ {
            gsub(/[ :]/, ""); print
        }
    ' "$file"
}

# Extract referenced compose-service names from push-health* scripts.
# Covers two patterns:
#   1. resolve_container <name>      — direct container resolution
#   2. http://<name>:<port>           — service URL inside push_exec probe
extract_resolved() {
    {
        # Pattern 1: resolve_* calls
        grep -hE "resolve_(compose_|coolify_subname_)?container[[:space:]]+[\"']?[a-zA-Z][a-zA-Z0-9_-]*" \
            "$PUSH_HEALTH" "$PUSH_HEALTH_PUBLIC" 2>/dev/null \
            | sed -E "s/.*resolve_[a-z_]*[[:space:]]+[\"']?([a-zA-Z][a-zA-Z0-9_-]*)[\"']?.*/\1/"
        # Pattern 2: http://service-name: inside probe commands
        grep -hoE "http://[a-zA-Z][a-zA-Z0-9_-]+:" "$PUSH_HEALTH" "$PUSH_HEALTH_PUBLIC" 2>/dev/null \
            | sed -E 's,http://,,; s/:$//'
        # Pattern 3: socket.create_connection(('service-name', PORT))
        grep -hoE "create_connection\(\('[a-zA-Z][a-zA-Z0-9_-]+'" "$PUSH_HEALTH" 2>/dev/null \
            | sed -E 's/.*\(\x27([a-zA-Z][a-zA-Z0-9_-]+)\x27.*/\1/'
    } | sort -u
}

# Build coverage report.
declare -a MISSING=()
declare -a COVERED=()
declare -a IGNORED=()

RESOLVED=$(extract_resolved)

while read -r svc; do
    [ -z "$svc" ] && continue
    if echo "$RESOLVED" | grep -qxF "$svc"; then
        COVERED+=("$svc")
    elif reason=$(ignore_reason "$svc"); then
        IGNORED+=("$svc ($reason)")
    else
        MISSING+=("$svc")
    fi
done < <(extract_services "$COMPOSE")

# GPU compose: separate check (gpu-health.sh handles those)
GPU_SERVICES=$(extract_services "$GPU_COMPOSE" 2>/dev/null || true)
# GPU services are referenced in gpu-health.sh either by container name
# (klai-gpu-<name>-1) or by tunnel port (172.18.0.1:<port>). The port map:
#   7997=tei, 7998=infinity, 8001=bge-m3-sparse, 8000=transcription-api
GPU_REFERENCED=$( {
    grep -oE 'klai-gpu-[a-z0-9-]+-1' "$GPU_HEALTH" 2>/dev/null | sed -E 's/klai-gpu-(.+)-1/\1/'
    grep -qE '172\.18\.0\.1:7997' "$GPU_HEALTH" 2>/dev/null && echo tei
    grep -qE '172\.18\.0\.1:7998' "$GPU_HEALTH" 2>/dev/null && echo infinity
    grep -qE '172\.18\.0\.1:8001' "$GPU_HEALTH" 2>/dev/null && echo bge-m3-sparse
    grep -qE '172\.18\.0\.1:8000' "$GPU_HEALTH" 2>/dev/null && echo transcription-api
} | sort -u || true)

# Output mode --list
if [ "${1:-}" = "--list" ]; then
    echo "── Covered (${#COVERED[@]}) ──"
    printf '  ✓ %s\n' "${COVERED[@]}"
    echo ""
    echo "── Ignored (${#IGNORED[@]}) ──"
    printf '  — %s\n' "${IGNORED[@]}"
    echo ""
    if [ ${#MISSING[@]} -gt 0 ]; then
        echo "── MISSING (${#MISSING[@]}) ──"
        printf '  ✗ %s\n' "${MISSING[@]}"
    fi
    echo ""
    echo "── GPU services (gpu-health.sh) ──"
    for s in $GPU_SERVICES; do
        if echo "$GPU_REFERENCED" | grep -qxF "$s" || ignore_reason "$s" >/dev/null; then
            echo "  ✓ $s"
        else
            echo "  ? $s (not in gpu-health.sh — verify intentional)"
        fi
    done
    exit 0
fi

# Default: fail if any missing
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "FAIL: ${#MISSING[@]} compose service(s) without monitor coverage:"
    printf '  - %s\n' "${MISSING[@]}"
    echo ""
    echo "Either add a push_healthcheck/push_exec call in deploy/scripts/push-health.sh,"
    echo "or add the service to the IGNORE map at the top of this script with a rationale."
    exit 1
fi

echo "PASS: all ${#COVERED[@]} user-facing compose services have monitor coverage (${#IGNORED[@]} intentionally ignored)."
