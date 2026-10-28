#!/usr/bin/env bash
# /opt/klai/scripts/deploy-with-rollback.sh
#
# Deploy one stateless image service to core-01 and put the previous image back
# when the new one does not hold. SPEC-INFRA-AUTO-ROLLBACK-001 slice S1.
#
# Usage:
#   deploy-with-rollback.sh <compose-service> <image-ref-with-sha>
#                           [--probe <url>] [--probe-network <net>] [--compose-tag <tag>]
#                           [--error-watch <seconds>]
#
#   <image-ref-with-sha>  ghcr.io/getklai/<image>:<git sha>, the tag the build
#                         workflow pushed. Rule R1: the exact sha is deployed,
#                         never :latest from the registry.
#   --probe-network       docker network the probe runs on (default klai-net; a
#                         service that is not on klai-net names its own).
#   --compose-tag         the image reference deploy/docker-compose.yml uses for
#                         the service. Default: the image-ref repository with
#                         :latest, which is what every service in scope uses.
#   --probe               HTTP 2xx expected from this URL within 60 s (every
#                         5 s), fetched from inside core-01 on klai-net.
#   --error-watch         after the probe is green, keep the deploy under watch
#                         for this many seconds (gate 4, slice S11): the helper
#                         post-deploy-error-watch.sh counts the service's error
#                         lines in VictoriaLogs since the deploy. The per-service
#                         lock stays held for the whole watch, and so does the
#                         calling ssh step.
#
# Steps
#   1. Record the image the service runs now and tag it klai/<svc>:previous (R2).
#   2. Pull <image-ref-with-sha> and tag it as the compose tag.
#   3. compose-up.sh --no-pull <svc>. Its verification is gate 1. --no-pull
#      matters: without it compose pulls :latest from the registry and replaces
#      the tag this script just set, which on a rollback would bring the bad
#      image back.
#   4. The --probe URL is gate 2.
#   5. --error-watch is gate 4, error-rate. Fail open on observability, fail
#      closed on real errors: helper exit 3 (a breach) is a red gate; any other
#      non-zero exit (query error, no credentials) logs a warning and keeps the
#      deploy, because an outage of the log store is no evidence against the new
#      image.
#   6. A red gate retags klai/<svc>:previous to the compose tag and runs
#      compose-up.sh again (once, R6), then the probe again. The rollback is
#      not error-watched.
#
# Exit codes
#   0  deployed
#   3  a gate was red and the previous image is serving again   (rolled_back)
#   4  a gate was red and the rollback failed, or there was no
#      previous image to go back to                             (rollback_failed)
#   1  nothing was changed: bad arguments, previous image could not be tagged,
#      or the pull failed. The running container is untouched, no report.
#
# Every exit 0/3/4 appends one line to the deploy ledger, writes
# <reports>/<service>.json, prints it as "ROLLBACK_REPORT <json>" and, for 3 and
# 4, appends an auto_rollback event line that Alloy ships to VictoriaLogs.

set -uo pipefail

# Seams for deploy/scripts/tests/deploy-with-rollback.test.sh. In production
# every one keeps its default.
KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"
COMPOSE_UP="${KLAI_COMPOSE_UP:-$KLAI_DIR/scripts/compose-up.sh}"
# A second deploy of the same service waits for the first instead of overwriting
# its klai/<svc>:previous. Separate from compose-up.sh's own lock, which is taken
# later by the nested call and would otherwise deadlock on this one.
LOCK_DIR="${KLAI_DEPLOY_LOCK_DIR:-/tmp}"
LOCK_WAIT="${KLAI_DEPLOY_LOCK_WAIT:-600}"
LEDGER="${KLAI_DEPLOY_LEDGER:-$KLAI_DIR/deploy-ledger.log}"
REPORT_DIR="${KLAI_DEPLOY_REPORTS:-$KLAI_DIR/deploy-reports}"
# Alloy tails this file (deploy/alloy/config.alloy, same pattern as the probes).
EVENT_LOG="${KLAI_AUTO_ROLLBACK_LOG:-$KLAI_DIR/logs/auto-rollback.log}"
PROBE_TIMEOUT="${KLAI_PROBE_TIMEOUT:-60}"
PROBE_INTERVAL="${KLAI_PROBE_INTERVAL:-5}"
PROBE_NETWORK="${KLAI_PROBE_NETWORK:-klai-net}"
ERROR_WATCH_BIN="${KLAI_ERROR_WATCH:-$KLAI_DIR/scripts/post-deploy-error-watch.sh}"
# Same pinned image persistence-smoke.sh and backup.sh use.
CURL_IMAGE="${KLAI_CURL_IMAGE:-curlimages/curl:8.11.1}"

usage() {
    echo "usage: ${0##*/} <compose-service> <image-ref-with-sha> [--probe <url>] [--probe-network <net>] [--compose-tag <tag>] [--error-watch <seconds>]" >&2
    exit 1
}

(( $# >= 2 )) || usage
SERVICE="$1"
IMAGE_REF="$2"
shift 2
PROBE_URL=""
COMPOSE_TAG=""
ERROR_WATCH=0
while (( $# > 0 )); do
    case "$1" in
        --probe)       (( $# >= 2 )) || usage; PROBE_URL="$2"; shift 2 ;;
        --probe-network) (( $# >= 2 )) || usage; PROBE_NETWORK="$2"; shift 2 ;;
        --compose-tag) (( $# >= 2 )) || usage; COMPOSE_TAG="$2"; shift 2 ;;
        --error-watch) (( $# >= 2 )) || usage; ERROR_WATCH="$2"; shift 2 ;;
        *) usage ;;
    esac
done
[[ "$IMAGE_REF" == *:* ]] || usage
[[ "$ERROR_WATCH" =~ ^[0-9]+$ ]] || usage
# The ssh step starts in the deploy user's home directory, and `docker compose
# ps` finds the stack from the working directory.
cd "$KLAI_DIR" || exit 1
# KLAI_DEPLOY_LOCK_HELD=1: the caller (deploy-portal-api.sh) already holds this
# service's lock and must keep it across its own steps; taking it again here
# would wait on the parent forever.
if [[ "${KLAI_DEPLOY_LOCK_HELD:-}" != 1 ]]; then
    if ! command -v flock >/dev/null 2>&1; then
        echo "ERROR: flock not found; refusing to deploy $SERVICE without the per-service lock" >&2
        exit 1
    fi
    exec 9>"$LOCK_DIR/klai-deploy-$SERVICE.lock"
    if ! flock -w "$LOCK_WAIT" 9; then
        echo "ERROR: another deploy of $SERVICE held $LOCK_DIR/klai-deploy-$SERVICE.lock for ${LOCK_WAIT}s; not starting" >&2
        exit 1
    fi
fi
COMPOSE_TAG="${COMPOSE_TAG:-${IMAGE_REF%:*}:latest}"
COMMIT="${GITHUB_SHA:-${IMAGE_REF##*:}}"
PREVIOUS_TAG="klai/$SERVICE:previous"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

short_id() { local id="${1#sha256:}"; printf '%s' "${id:0:12}"; }

json_str() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

running_image_id() {
    local cid
    cid="$("$DOCKER" compose ps -q "$SERVICE" 2>/dev/null | head -n1)"
    [[ -n "$cid" ]] || return 0
    "$DOCKER" inspect --format '{{.Image}}' "$cid" 2>/dev/null || true
}

# 0 when the URL answers 2xx within PROBE_TIMEOUT; at least one attempt is made.
LAST_PROBE_CODE=""
probe() {
    local url="$1" deadline
    "$DOCKER" image inspect "$CURL_IMAGE" >/dev/null 2>&1 || "$DOCKER" pull -q "$CURL_IMAGE" >/dev/null
    deadline=$(( SECONDS + PROBE_TIMEOUT ))
    while :; do
        LAST_PROBE_CODE="$("$DOCKER" run --rm --network "$PROBE_NETWORK" "$CURL_IMAGE" \
            -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)" || LAST_PROBE_CODE="000"
        echo "probe $url -> HTTP $LAST_PROBE_CODE"
        [[ "$LAST_PROBE_CODE" =~ ^2[0-9][0-9]$ ]] && return 0
        (( SECONDS + PROBE_INTERVAL >= deadline )) && return 1
        sleep "$PROBE_INTERVAL"
    done
}

# Both gates for one attempt. Sets GATE to the red one; 0 = green.
GATE=""
run_gates() {
    local flags=("$@")
    # tee keeps the compose-up output readable in the Actions log and also on
    # disk for the report. PIPESTATUS, not $?, so the pipe cannot hide a failure.
    "$COMPOSE_UP" --no-pull "${flags[@]}" "$SERVICE" 2>&1 | tee "$WORK/compose-up.out"
    if (( PIPESTATUS[0] != 0 )); then GATE="compose-up"; return 1; fi
    if [[ -n "$PROBE_URL" ]] && ! probe "$PROBE_URL"; then GATE="probe"; return 1; fi
    return 0
}

FROM="none"
OUTCOME=""
LOG_PATH=""
REPORT_GATE="none"

finish() {
    local code="$1" ts report
    ts="$(now)"
    mkdir -p "$REPORT_DIR"
    if [[ ! -e "$LEDGER" ]]; then
        : >> "$LEDGER" && chmod 0644 "$LEDGER"
    fi
    printf '%s\timage\t%s\t%s\t%s\t%s\t%s\n' \
        "$ts" "$SERVICE" "$FROM" "$IMAGE_REF" "$COMMIT" "$OUTCOME" >> "$LEDGER"
    report="$(printf '{"service":"%s","kind":"image","from":"%s","to":"%s","commit":"%s","gate":"%s","outcome":"%s","ts":"%s","log":"%s"}' \
        "$(json_str "$SERVICE")" "$(json_str "$FROM")" "$(json_str "$IMAGE_REF")" "$(json_str "$COMMIT")" \
        "$(json_str "$REPORT_GATE")" "$OUTCOME" "$ts" "$(json_str "$LOG_PATH")")"
    printf '%s\n' "$report" > "$REPORT_DIR/$SERVICE.json"
    if [[ "$OUTCOME" != "deployed" ]]; then
        # service is the stream label (as in the probe logs); the compose
        # service is deploy_service. Alert rules filter on event:auto_rollback.
        local event
        event="$(printf '{"service":"auto-rollback","level":"error","event":"auto_rollback","outcome":"%s","deploy_service":"%s","gate":"%s","commit":"%s","_time":"%s"}' \
            "$OUTCOME" "$(json_str "$SERVICE")" "$(json_str "$REPORT_GATE")" "$(json_str "$COMMIT")" "$ts")"
        printf '%s\n' "$event"
        if ! { mkdir -p "$(dirname "$EVENT_LOG")" && printf '%s\n' "$event" >> "$EVENT_LOG"; } 2>/dev/null; then
            echo "WARN: could not append to $EVENT_LOG; the auto_rollback event will not reach VictoriaLogs" >&2
        fi
    fi
    echo "ROLLBACK_REPORT $report"
    exit "$code"
}

# Evidence has to be captured before the rollback removes the failed container.
capture_log() {
    local cid
    mkdir -p "$REPORT_DIR"
    LOG_PATH="$REPORT_DIR/$SERVICE.log"
    cid="$("$DOCKER" ps -aq --filter "label=com.docker.compose.service=$SERVICE" 2>/dev/null | head -n1)"
    {
        echo "== $SERVICE gate=$GATE image=$IMAGE_REF"
        echo "== compose-up output (last 200 lines)"
        tail -n 200 "$WORK/compose-up.out" 2>/dev/null
        [[ -z "$PROBE_URL" ]] || echo "== last probe: $PROBE_URL -> HTTP ${LAST_PROBE_CODE:-none}"
        if [[ -n "$cid" ]]; then
            echo "== docker logs --tail 200 $cid"
            "$DOCKER" logs --tail 200 "$cid" 2>&1
        else
            echo "== no container found for $SERVICE"
        fi
    } > "$LOG_PATH"
}

# --- 1. record the image that runs now ---------------------------------------
echo "==> $SERVICE: deploying $IMAGE_REF (compose tag $COMPOSE_TAG)"
PREV_ID="$(running_image_id)"
if [[ -n "$PREV_ID" ]]; then
    if ! "$DOCKER" tag "$PREV_ID" "$PREVIOUS_TAG"; then
        echo "::error::could not tag the running image $PREV_ID as $PREVIOUS_TAG; refusing to deploy without a way back" >&2
        exit 1
    fi
    FROM="$(short_id "$PREV_ID")"
    echo "==> previous image $FROM kept as $PREVIOUS_TAG"
else
    echo "==> no running container for $SERVICE; there is no previous image to roll back to"
fi

# --- 2. pull the exact sha, point the compose tag at it ----------------------
echo "==> pulling $IMAGE_REF"
if ! "$DOCKER" pull "$IMAGE_REF"; then
    echo "::error::docker pull $IMAGE_REF failed; $SERVICE untouched" >&2
    exit 1
fi
if ! "$DOCKER" tag "$IMAGE_REF" "$COMPOSE_TAG"; then
    echo "::error::could not tag $IMAGE_REF as $COMPOSE_TAG; $SERVICE untouched" >&2
    exit 1
fi

# --- 3 + 4. recreate and probe ------------------------------------------------
echo "==> recreating $SERVICE"
if run_gates; then
    # --- gate 4: error rate since the deploy ----------------------------------
    watch_rc=0
    if (( ERROR_WATCH > 0 )); then
        "$ERROR_WATCH_BIN" "$SERVICE" "$(now)" --window "$ERROR_WATCH"
        watch_rc=$?
        if (( watch_rc != 0 && watch_rc != 3 )); then
            echo "::warning::$SERVICE error watch gave no verdict (exit $watch_rc); keeping the deploy"
            watch_rc=0
        fi
    fi
    if (( watch_rc == 0 )); then
        echo "==> $SERVICE is up on $IMAGE_REF"
        OUTCOME="deployed"
        finish 0
    fi
    GATE="error-rate"
fi

# --- 5. red gate: one rollback, never a loop ----------------------------------
FAILED_GATE="$GATE"
REPORT_GATE="$FAILED_GATE"
echo "::error::$SERVICE gate '$FAILED_GATE' is red on $IMAGE_REF"
capture_log

if [[ -z "$PREV_ID" ]]; then
    echo "::error::$SERVICE had no previous image; nothing to roll back to" >&2
    REPORT_GATE="no-previous-image"
    OUTCOME="rollback_failed"
    finish 4
fi

echo "==> rolling back $SERVICE to $FROM ($PREVIOUS_TAG)"
if "$DOCKER" tag "$PREVIOUS_TAG" "$COMPOSE_TAG" && run_gates --force-recreate; then
    echo "::error::$SERVICE rolled back to $FROM after gate '$FAILED_GATE' failed on $IMAGE_REF"
    OUTCOME="rolled_back"
    finish 3
fi
echo "::error::rollback of $SERVICE to $FROM failed too ($GATE); stopping, no second attempt"
REPORT_GATE="rollback-${GATE:-tag}"
OUTCOME="rollback_failed"
finish 4
