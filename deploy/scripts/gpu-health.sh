#!/usr/bin/env bash
# gpu-health.sh — GPU services health check (gpu-01 via autossh tunnel + ssh)
#
# Checks all 4 GPU inference services via the SSH tunnel bound at 172.18.0.1.
# Pushes a single combined UP/DOWN heartbeat to Uptime Kuma for "GPU Services".
#
# Separately pushes per-transcription-worker heartbeats via direct ssh to gpu-01
# (workers sit on internal transcription-net behind nginx LB on :8000, not
# reachable via the standard 172.18.0.1 tunnel). Workers run individual Docker
# healthchecks against http://127.0.0.1:8081/health — we read State.Health.Status.
#
# Called by: push-health.sh (every minute, via cron)
# Manual run: bash /opt/klai/scripts/gpu-health.sh
# Tunnel service: systemctl status gpu-tunnel.service
set -uo pipefail

[ -f /opt/klai/.env ] && source /opt/klai/.env

KUMA="${UPTIME_KUMA_PUSH_URL:-https://status.${DOMAIN}/api/push}"
TOKEN_GPU="${KUMA_TOKEN_GPU_SERVICES:-}"
TOKEN_W1="${KUMA_TOKEN_TRANSCRIPTION_W1:-}"
TOKEN_W2="${KUMA_TOKEN_TRANSCRIPTION_W2:-}"
LOG=/opt/klai/logs/health.log
GPU_SSH_KEY=/opt/klai/gpu-tunnel-key
GPU_HOST=root@5.9.10.215

mkdir -p /opt/klai/logs

# Find portal-api container (on klai-net, can reach 172.18.0.1 via bridge gateway)
PORTAL_API=$(docker ps \
    --filter "label=com.docker.compose.project=klai-core" \
    --filter "label=com.docker.compose.service=portal-api" \
    --format "{{.Names}}" | head -1)

# ── Part 1: Combined GPU services check (TEI / Infinity / sparse / Whisper LB) ─

if ! systemctl is-active --quiet gpu-tunnel.service; then
    [ -n "$TOKEN_GPU" ] && curl -sf "${KUMA}/${TOKEN_GPU}?status=down&msg=tunnel-service-inactive" -o /dev/null
    echo "$(date -Iseconds) WARN GPU tunnel: service inactive" >> "$LOG"
    # Don't exit — still try per-worker SSH probe below in case workers are reachable.
fi

check_endpoint() {
    local url="$1" label="$2"
    if ! docker exec "$PORTAL_API" \
        python3 -c "import urllib.request; urllib.request.urlopen('${url}', timeout=5)" 2>/dev/null; then
        echo "$(date -Iseconds) WARN GPU ${label}: unreachable (${url})" >> "$LOG"
        return 1
    fi
    return 0
}

errors=()
check_endpoint "http://172.18.0.1:7997/health" "TEI(embeddings)"    || errors+=("TEI:7997")
check_endpoint "http://172.18.0.1:7998/health" "Infinity(reranker)" || errors+=("Infinity:7998")
check_endpoint "http://172.18.0.1:8001/health" "BGE-M3-sparse"      || errors+=("sparse:8001")
check_endpoint "http://172.18.0.1:8000/health" "TranscriptionLB"    || errors+=("Whisper:8000")

if [ -n "$TOKEN_GPU" ]; then
    if [ ${#errors[@]} -eq 0 ]; then
        curl -sf "${KUMA}/${TOKEN_GPU}?status=up&msg=OK" -o /dev/null
    else
        msg=$(printf '%s,' "${errors[@]}" | sed 's/,$//')
        curl -sf "${KUMA}/${TOKEN_GPU}?status=down&msg=${msg}" -o /dev/null
    fi
fi

# ── Part 2: Per-worker monitoring via direct SSH (capacity visibility) ────────
# Workers sit on internal transcription-net behind nginx LB. We read their
# Docker healthcheck status via SSH to gpu-01 (uses the gpu-tunnel-key).

push_worker() {
    local container="$1" token="$2" label="$3"
    [ -z "$token" ] && return
    local health
    health=$(ssh -i "$GPU_SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        "$GPU_HOST" "docker inspect --format='{{.State.Health.Status}}' $container 2>/dev/null || echo ssh-fail" 2>/dev/null)
    health=$(echo "$health" | tr -d '\n\r')
    if [ "$health" = "healthy" ]; then
        curl -sf "${KUMA}/${token}?status=up&msg=OK" -o /dev/null
    else
        curl -sf "${KUMA}/${token}?status=down&msg=${health:-unreachable}" -o /dev/null
        echo "$(date -Iseconds) WARN ${label}: ${health:-unreachable}" >> "$LOG"
    fi
}

push_worker "klai-gpu-transcription-worker-1-1" "$TOKEN_W1" "Transcription Worker 1"
push_worker "klai-gpu-transcription-worker-2-1" "$TOKEN_W2" "Transcription Worker 2"

# Exit non-zero only if the combined GPU check failed (keeps cron output clean
# while still surfacing failures via the existing health.log + Kuma alerts).
[ ${#errors[@]} -eq 0 ] && exit 0 || exit 1
