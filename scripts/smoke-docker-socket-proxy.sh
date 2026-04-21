#!/usr/bin/env bash
#
# scripts/smoke-docker-socket-proxy.sh
#
# SPEC-SEC-024-R10 / R11 — post-deploy smoke-test voor docker-socket-proxy.
# Verifieert dat elke "keep"-verb (CONTAINERS / NETWORKS / POST / DELETE)
# nog steeds werkt en dat EXEC nog steeds 403 teruggeeft na een compose-sync.
#
# Run vanaf core-01:
#   ssh core-01 "/opt/klai/scripts/smoke-docker-socket-proxy.sh"
#
# Exit 0: alle keep-verbs werken + EXEC is 403 + throwaway container opgeruimd.
# Exit 1: een keep-verb faalde, OF EXEC is niet 403, OF throwaway blijft hangen.
#
# Geen side-effects op productie: we maken één wegwerp-busybox op --network none,
# probes draaien op --network klai-socket-proxy, en drievoudig cleanup-vangnet:
#   (1) trap EXIT removes throwaway on any exit route
#   (2) --rm on docker run auto-removes on container exit
#   (3) busybox "sleep 60" dies on its own if the script ever hangs

set -euo pipefail

# ─── Config ─────────────────────────────────────────────────────────────────

# Network name matches deploy/docker-compose.yml "name: klai-socket-proxy".
PROXY_NET="klai-socket-proxy"
PROXY_URL="http://docker-socket-proxy:2375/v1.47"

# Ephemeral curl image — pinned to a recent stable release.
CURL_IMAGE="curlimages/curl:8.11.1"

# Unique throwaway container name (timestamp + pid, never collides).
NOOP="smoke-sec-024-$(date +%s)-$$"

FAIL=0

# ─── Helpers ────────────────────────────────────────────────────────────────

# Drievoudig cleanup-vangnet, punt 1 van 3.
trap 'docker rm -f "$NOOP" >/dev/null 2>&1 || true' EXIT

log() { printf '%-14s %s\n' "$1" "$2"; }

# Run curl inside an ephemeral container on the internal proxy network.
# All arguments are passed verbatim to curl.
probe() {
  docker run --rm --network "$PROXY_NET" "$CURL_IMAGE" \
    -sS --max-time 10 "$@"
}

check_keep_verb() {
  local verb="$1" route="$2"; shift 2
  log "[*] $verb" "$route"
  if probe -fo /dev/null "$@"; then
    log "[OK]" "$verb reachable"
  else
    log "[FAIL]" "$verb probe returned non-2xx — keep-verb unexpectedly blocked!"
    FAIL=1
  fi
}

# ─── Setup ──────────────────────────────────────────────────────────────────

log "[*] setup" "starting throwaway container $NOOP on --network none"
# --rm = cleanup vangnet punt 2 van 3. busybox sleep 60 = punt 3 van 3.
docker run --rm -d --name "$NOOP" --network none busybox:musl sleep 60 >/dev/null

# ─── Keep-verb probes (must succeed) ────────────────────────────────────────

check_keep_verb "CONTAINERS" "GET /containers/json" \
  "$PROXY_URL/containers/json?limit=1"

check_keep_verb "NETWORKS" "GET /networks" \
  "$PROXY_URL/networks"

check_keep_verb "POST" "POST /containers/$NOOP/restart" \
  -X POST "$PROXY_URL/containers/$NOOP/restart?t=1"

check_keep_verb "DELETE" "DELETE /containers/$NOOP" \
  -X DELETE "$PROXY_URL/containers/$NOOP?force=true"

# ─── EXEC must be blocked (403) ─────────────────────────────────────────────

log "[*] EXEC" "POST /containers/deadbeef/exec — expecting 403"
EXEC_STATUS=$(probe -o /dev/null -w '%{http_code}' \
  -X POST -H 'Content-Type: application/json' \
  -d '{"Cmd":["true"]}' \
  "$PROXY_URL/containers/deadbeef/exec" || true)

if [ "$EXEC_STATUS" = "403" ]; then
  log "[OK]" "EXEC correctly blocked (403)"
else
  log "[FAIL]" "EXEC returned $EXEC_STATUS — MUST be 403. Proxy is misconfigured!"
  FAIL=1
fi

# ─── Hygiene: verify throwaway is gone ──────────────────────────────────────

if docker ps -a --filter "name=$NOOP" --format '{{.Names}}' | grep -q "^$NOOP$"; then
  log "[FAIL]" "Throwaway container $NOOP still present after probes — cleanup broken"
  FAIL=1
else
  log "[OK]" "Throwaway cleanup verified"
fi

# ─── Result ─────────────────────────────────────────────────────────────────

if [ "$FAIL" -eq 0 ]; then
  echo
  echo "SPEC-SEC-024 smoke-test: PASS"
else
  echo
  echo "SPEC-SEC-024 smoke-test: FAIL — see [FAIL] lines above"
fi

exit "$FAIL"
