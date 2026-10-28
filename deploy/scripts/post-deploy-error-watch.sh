#!/usr/bin/env bash
# /opt/klai/scripts/post-deploy-error-watch.sh
#
# Count a service's error lines in VictoriaLogs for a window after a deploy and
# say whether the deploy made things worse. SPEC-INFRA-AUTO-ROLLBACK-001 slice
# S11, the fourth gate of section 4.a; called by deploy-with-rollback.sh
# --error-watch, runnable by hand on core-01.
#
# Usage:
#   post-deploy-error-watch.sh <service> <deployed-at> [--window <seconds>] [--threshold <n>]
#
#   <service>      the compose service, which is also the `service` log field of
#                  every service in scope. caddy has no error level for its own
#                  failures, so it counts `status:5*` access-log lines instead.
#   <deployed-at>  epoch seconds or UTC `YYYY-MM-DDTHH:MM:SSZ`.
#   --window       seconds after <deployed-at> to keep watching (default 300;
#                  the caller holds the per-service deploy lock for this long).
#                  0 = one check of everything logged so far.
#   --threshold    error lines that count as a breach (default 10).
#
# A breach is BOTH of
#   - more than THRESHOLD error lines since <deployed-at>, and
#   - more than twice the error lines in the same length of time just before it.
# The first clause is the measured floor: over 30 days the p95 of 15-minute
# error counts was 0 for every service except knowledge-ingest (2), so
# 3x p95 never exceeded 10 and the floor decides. The second clause keeps a
# trouble that began before the deploy (an upstream already down, a database
# still loading) from being blamed on it. Known ceiling: a regression that
# lands on top of an equally noisy period and does not double it goes unseen.
#
# A breach is not attributed to the deploy when another container on the host
# started inside the window: the window is restarted from that moment, at most
# MAX_RESTARTS times, and only the last window decides. On 2026-10-28 23:07 UTC
# knowledge-ingest was rolled back on 12 "LLM connection error" lines while the
# LiteLLM hook deploy recreated litellm twice in those minutes; the same lines
# kept coming on the old image. The service's own container does not count.
#
# Exit codes
#   0  the window closed without a breach
#   3  breach; the count and the threshold are printed
#   1  the query failed (no credentials, VictoriaLogs unreachable, a reply that
#      is not well-formed JSON with a count) or bad arguments. Fail open: the
#      caller keeps the deploy, because an observability outage is not evidence
#      against the new image.
#
# VictoriaLogs is only on Docker's `monitoring` network, so the query runs in a
# throwaway curl container there. The basic-auth pair is read from
# /opt/klai/.env and handed over on stdin as a curl config, which keeps it out
# of `ps` and out of this script's output.

set -uo pipefail

KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"
ENV_FILE="${KLAI_ENV_FILE:-$KLAI_DIR/.env}"
VL_URL="${KLAI_VICTORIALOGS_URL:-http://victorialogs:9428}"
# The Docker network VictoriaLogs sits on is klai-monitoring (compose network
# `monitoring` with `name: klai-monitoring`); `monitoring` did not exist on
# core-01 and gave "no verdict" on the first three watched deploys (2026-10-28).
VL_NETWORK="${KLAI_VICTORIALOGS_NETWORK:-klai-monitoring}"
CURL_IMAGE="${KLAI_CURL_IMAGE:-curlimages/curl:8.11.1}"
PYTHON="${KLAI_PYTHON:-python3}"
POLL_INTERVAL="${KLAI_ERROR_WATCH_INTERVAL:-30}"
# Consecutive failed polls tolerated before giving up; one dropped query in a
# watch is not an outage.
MAX_QUERY_FAILURES=3
# Windows restarted because another container started inside one. Two keeps a
# deploy wave from stretching a watch past three windows (15 minutes at 300 s).
MAX_RESTARTS=2

usage() {
    echo "usage: ${0##*/} <service> <deployed-at> [--window <seconds>] [--threshold <n>]" >&2
    exit 1
}

(( $# >= 2 )) || usage
SERVICE="$1"
DEPLOYED_AT="$2"
shift 2
WINDOW=300
THRESHOLD=10
while (( $# > 0 )); do
    case "$1" in
        --window)    (( $# >= 2 )) || usage; WINDOW="$2"; shift 2 ;;
        --threshold) (( $# >= 2 )) || usage; THRESHOLD="$2"; shift 2 ;;
        *) usage ;;
    esac
done
[[ "$SERVICE" =~ ^[A-Za-z0-9_.-]+$ ]] || usage
[[ "$WINDOW" =~ ^[0-9]+$ && "$THRESHOLD" =~ ^[0-9]+$ ]] || usage

# GNU date on core-01, BSD date on a developer machine.
to_epoch() {
    if [[ "$1" =~ ^[0-9]+$ ]]; then printf '%s' "$1"; return; fi
    date -u -d "$1" +%s 2>/dev/null || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$1" +%s 2>/dev/null
}
to_iso() {
    date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ
}

START="$(to_epoch "$DEPLOYED_AT")"
[[ "$START" =~ ^[0-9]+$ ]] || { echo "error-watch: cannot read deployed-at '$DEPLOYED_AT'" >&2; exit 1; }

if [[ "$SERVICE" == "caddy" ]]; then
    FILTER="service:caddy AND status:5*"
else
    FILTER="service:$SERVICE AND level:error"
fi

env_value() {
    sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -n1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}
VL_USER="$(env_value VICTORIALOGS_AUTH_USER)"
VL_PASS="$(env_value VICTORIALOGS_AUTH_PASSWORD)"
# Backslash and double quote are special inside a curl config string.
CURL_USERPWD="$(printf '%s:%s' "$VL_USER" "$VL_PASS" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"
if [[ -z "$VL_USER" || -z "$VL_PASS" ]]; then
    echo "error-watch: VICTORIALOGS_AUTH_USER/PASSWORD not found in $ENV_FILE; not watching $SERVICE" >&2
    exit 1
fi

# count_between <from-epoch> <to-epoch>: error lines in [from, to). Prints the
# number; returns 1 when VictoriaLogs did not answer with one.
count_between() {
    local query reply
    query="$FILTER AND _time:[$(to_iso "$1"), $(to_iso "$2")) | stats count() as n"
    reply="$(printf 'user = "%s"\n' "$CURL_USERPWD" | \
        "$DOCKER" run --rm -i --network "$VL_NETWORK" "$CURL_IMAGE" \
            -sS -f -K - --max-time 20 --data-urlencode "query=$query" \
            "$VL_URL/select/logsql/stats_query" 2>&1)" || { echo "error-watch: query failed: ${reply:0:200}" >&2; return 1; }
    # Anything but a well-formed success reply is a query error, so a truncated
    # or mangled body can never read as a clean window. stats_query without a
    # by() clause answers with no series (no match) or exactly one.
    reply="$("$PYTHON" -c '
import json, sys
try:
    d = json.load(sys.stdin)
    assert d["status"] == "success" and isinstance(d["data"]["result"], list)
    r = d["data"]["result"]
    assert len(r) <= 1
    print(int(r[0]["value"][1]) if r else 0)
except Exception:
    sys.exit(1)
' <<< "$reply")" || { echo "error-watch: reply is not a well-formed count: ${reply:0:200}" >&2; return 1; }
    echo "$reply"
}

# The first running container other than this service that started at or
# after START, as "<started-at> <name>"; empty when there is none. StartedAt is
# RFC 3339 with nanoseconds, so a plain string compare against the ISO second
# orders correctly.
overlapping_start() {
    local ids
    ids="$("$DOCKER" ps -q 2>/dev/null)" || return 0
    [[ -n "$ids" ]] || return 0
    # shellcheck disable=SC2086 # one id per word
    "$DOCKER" inspect -f '{{.State.StartedAt}} {{index .Config.Labels "com.docker.compose.service"}} {{.Name}}' $ids 2>/dev/null \
        | awk -v since="$(to_iso "$START")" -v svc="$SERVICE" '$1 >= since && $2 != svc { print $1, $3; exit }'
}

END=$(( START + WINDOW ))
failures=0
restarts=0
echo "error-watch: $SERVICE since $(to_iso "$START"), window ${WINDOW}s, threshold $THRESHOLD ($FILTER)"
while :; do
    now="$(date -u +%s)"
    # Up to and including the current second; timestamps have second resolution.
    qend=$(( now + 1 ))
    (( WINDOW > 0 && qend > END )) && qend=$END
    span=$(( qend - START ))
    if after="$(count_between "$START" "$qend")"; then
        failures=0
        if (( after > THRESHOLD )); then
            # Same length of time just before the deploy.
            if before="$(count_between $(( START - span )) "$START")"; then
                if (( after > 2 * before )); then
                    other="$(overlapping_start)"
                    if [[ -n "$other" ]] && (( restarts < MAX_RESTARTS )); then
                        restarts=$(( restarts + 1 ))
                        echo "error-watch: $SERVICE has $after error lines since $(to_iso "$START") but another container started inside the window ($other); not attributable, window restarted ($restarts/$MAX_RESTARTS)"
                        START="$now"; END=$(( START + WINDOW ))
                        continue
                    fi
                    echo "error-watch: BREACH $SERVICE: $after error lines in ${span}s since deploy, threshold $THRESHOLD, $before in the ${span}s before"
                    exit 3
                fi
                echo "error-watch: $SERVICE has $after error lines but $before before the deploy; not blamed on it"
            else
                failures=$(( failures + 1 ))
            fi
        fi
    else
        failures=$(( failures + 1 ))
    fi
    if (( failures >= MAX_QUERY_FAILURES )); then
        echo "error-watch: cannot query VictoriaLogs; no verdict for $SERVICE" >&2
        exit 1
    fi
    if (( now >= END )); then
        if (( failures > 0 )); then
            echo "error-watch: the last query failed; no verdict for $SERVICE" >&2
            exit 1
        fi
        echo "error-watch: $SERVICE clean: ${after:-0} error lines in ${WINDOW}s (threshold $THRESHOLD)"
        exit 0
    fi
    remaining=$(( END - $(date -u +%s) ))
    (( remaining > 0 )) || remaining=0
    sleep "$(( POLL_INTERVAL < remaining ? POLL_INTERVAL : remaining ))"
done
