#!/usr/bin/env bash
# Regression guard for the Vexa bot -> STT network path.
#
# Vexa meeting-api passes TRANSCRIPTION_SERVICE_URL into each spawned bot, and
# the bot itself POSTs captured PCM to that URL. Bots are deliberately blocked
# from every host-bound port by harden-docker-user.sh, so the URL must name a
# narrow in-network proxy. The proxy may reach only the host's transcription
# tunnel; it must not weaken the bot-subnet INPUT deny.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE="$REPO_ROOT/deploy/docker-compose.yml"
FAIL=0

service_block() {
    local service="$1"
    awk -v service="$service" '
        $0 == "  " service ":" { capture=1; next }
        capture && /^  [a-zA-Z0-9_-]+:$/ { exit }
        capture { print }
    ' "$COMPOSE"
}

check() {
    local description="$1"
    shift
    if "$@"; then
        echo "OK:   $description"
    else
        echo "FAIL: $description" >&2
        FAIL=1
    fi
}

echo "-- Vexa 0.12 bot transcription path --"

MEETING_API_BLOCK="$(service_block vexa12-meeting-api)"
PROXY_BLOCK="$(service_block vexa12-transcription-proxy)"

check "meeting-api passes bots the network-local transcription proxy URL" \
    grep -Fq 'TRANSCRIPTION_SERVICE_URL: http://vexa12-transcription-proxy:8000/v1/audio/transcriptions' \
    <<<"$MEETING_API_BLOCK"

check "meeting-api no longer passes bots the host bridge address" \
    bash -c '! grep -Fq "TRANSCRIPTION_SERVICE_URL: http://172.18.0.1:8000" <<<"$0"' \
    "$MEETING_API_BLOCK"

check "the proxy exposes only a TCP listener for STT" \
    grep -Fq 'TCP-LISTEN:8000,fork,reuseaddr' <<<"$PROXY_BLOCK"

check "the proxy forwards only to the host transcription tunnel" \
    grep -Fq 'TCP:172.18.0.1:8000' <<<"$PROXY_BLOCK"

check "the proxy is reachable from spawned bots" \
    grep -Fq 'vexa12-bots:' <<<"$PROXY_BLOCK"

check "the proxy has a trusted address outside the dynamic bot pool" \
    grep -Fq 'ipv4_address: 172.29.0.11' <<<"$PROXY_BLOCK"

check "the proxy does not bridge bots onto klai-net" \
    bash -c '! grep -Fq -- "- klai-net" <<<"$0"' "$PROXY_BLOCK"

check "the proxy has no environment or env_file secret surface" \
    bash -c '! grep -Eq "^[[:space:]]+(environment|env_file):" <<<"$0"' \
    "$PROXY_BLOCK"

check "the proxy drops Linux capabilities" \
    grep -Fq -- '- ALL' <<<"$PROXY_BLOCK"

check "the proxy filesystem is read-only" \
    grep -Fq 'read_only: true' <<<"$PROXY_BLOCK"

echo "----------------------------------------"
if [ "$FAIL" -eq 0 ]; then
    echo "Vexa 0.12 bot transcription path: OK"
else
    echo "Vexa 0.12 bot transcription path: FAILED" >&2
fi
exit "$FAIL"
