#!/usr/bin/env bash
# /opt/klai/scripts/rollback-since.sh
#
# Put core-01 back to what ran when E2E prod-tenant was last green
# (SPEC-INFRA-AUTO-ROLLBACK-001 section 6, slice S6). auto-rollback.yml runs it
# over ssh after a red E2E run on main has been confirmed by a second red run.
#
# Usage:
#   rollback-since.sh <since-ts> --until <until-ts> [--rehearse]
#
#   <since-ts>   start of the last green run's job
#   --until      creation time of the red run; a deploy after it was not what
#                that run saw, so it is left alone
#   --rehearse   handle the rollback-canary service only, so the whole chain can
#                be exercised on production without touching a real service
#   Times are UTC in the ledger's own format (2026-10-28T04:00:00Z) and are
#   compared to ledger lines as text.
#
# Manual full rollback (an operator on core-01, when auto-rollback.yml did not
# run): /opt/klai/scripts/rollback-since.sh <since-ts> --until "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# There is deliberately no workflow_dispatch for this.
#
# One pass over the deploy ledger, every service with a `deployed` line in
# (since, until], image services first and compose last, newest deploy first:
#   image    back to the `from` of its OLDEST deployed line in the window, which
#            is the image that ran when E2E was green, through
#            deploy-with-rollback.sh --rollback e2e (its gates, ledger line,
#            report and auto_rollback event), with the probe URL and network its
#            last report recorded. Skipped, not failed, when the container no
#            longer runs the newest `to` of the window (a later deploy is live).
#   compose  undone from its *.prev copies (compose-rollback.sh rb_replay, which
#            keeps the .compose-rollback-failed and self-migrating-image rules)
#            only when it is the only sync in the window and its
#            .compose-prev-state is still there. The copies reach back one sync,
#            so anything else is reported and counts as failed. Compose goes
#            last because it restores /opt/klai/scripts too; the scripts this
#            run calls are copied before anything changes.
#   rollback-canary is skipped, except with --rehearse, which skips the rest.
#
# Never loops (R6): a real run records its start time in .e2e-rollback-at, and
# later runs only read ledger lines after the later of <since-ts> and that time,
# so a deploy is undone once even when E2E stays red.
#
# Exit codes
#   0  nothing to roll back; nothing changed
#   3  every service found was rolled back                     (rolled_back)
#   4  at least one rollback failed or could not be attempted,
#      or the ledger is missing                                (rollback_failed)
#   1  bad arguments, or the scripts could not be copied
#
# Report: <reports>/e2e-rollback.json, one object in the shape
# ci-failure-notice.yml reads, also printed as "ROLLBACK_REPORT <json>"; the
# full output goes to <reports>/e2e-rollback.log.
#
# Tests: deploy/scripts/tests/rollback-since.test.sh

set -uo pipefail

# Seams for the test-suite; production keeps every default.
KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"
DEPLOY="${KLAI_DEPLOY_WITH_ROLLBACK:-$KLAI_DIR/scripts/deploy-with-rollback.sh}"
COMPOSE_ROLLBACK="${KLAI_COMPOSE_ROLLBACK:-$KLAI_DIR/scripts/compose-rollback.sh}"
LEDGER="${KLAI_DEPLOY_LEDGER:-$KLAI_DIR/deploy-ledger.log}"
REPORT_DIR="${KLAI_DEPLOY_REPORTS:-$KLAI_DIR/deploy-reports}"
EVENT_LOG="${KLAI_AUTO_ROLLBACK_LOG:-$KLAI_DIR/logs/auto-rollback.log}"
REPLAYED_AT="$KLAI_DIR/.e2e-rollback-at"
# Both written by compose-rollback.sh (RB_MARKER, RB_STATE).
COMPOSE_MARKER="$KLAI_DIR/.compose-commit"
PREV_STATE="$KLAI_DIR/.compose-prev-state"
CANARY="rollback-canary"
TS_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'

usage() { echo "usage: ${0##*/} <since-ts> --until <until-ts> [--rehearse]   (times YYYY-MM-DDTHH:MM:SSZ)" >&2; exit 1; }

(( $# >= 1 )) || usage
SINCE="$1"
shift
UNTIL=""
REHEARSE=0
while (( $# > 0 )); do
    case "$1" in
        --until) (( $# >= 2 )) || usage; UNTIL="$2"; shift 2 ;;
        --rehearse) REHEARSE=1; shift ;;
        *) usage ;;
    esac
done
[[ "$SINCE" =~ $TS_RE && "$UNTIL" =~ $TS_RE ]] || usage
# The canary sits in a compose profile; without it compose does not see it.
(( REHEARSE )) && export COMPOSE_PROFILES="rollback-rehearsal"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
json_str() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
# report_field <file> <key>: a string value from a one-line JSON report.
report_field() { sed -n "s/.*\"$2\":\"\\([^\"]*\\)\".*/\\1/p" "$1" 2>/dev/null; }

cd "$KLAI_DIR" || exit 1
mkdir -p "$REPORT_DIR"
LOG="$REPORT_DIR/e2e-rollback.log"
exec > >(tee "$LOG") 2>&1

TOOLS="$(mktemp -d)"
trap 'rm -rf "$TOOLS"' EXIT
# compose-rollback.sh sources datastore-rollback.sh from its own directory.
if ! cp "$DEPLOY" "$TOOLS/deploy-with-rollback.sh" || ! cp "$COMPOSE_ROLLBACK" "$TOOLS/compose-rollback.sh" \
    || ! cp "$(dirname "$COMPOSE_ROLLBACK")/datastore-rollback.sh" "$TOOLS/datastore-rollback.sh"; then
    echo "::error::could not copy the rollback scripts to $TOOLS; nothing changed"
    exit 1
fi
DEPLOY="$TOOLS/deploy-with-rollback.sh"
COMPOSE_ROLLBACK="$TOOLS/compose-rollback.sh"

STARTED="$(now)"
AFTER="$SINCE"
if (( ! REHEARSE )) && [[ -s "$REPLAYED_AT" ]]; then
    last="$(cat "$REPLAYED_AT")"
    if [[ "$last" > "$AFTER" ]]; then
        echo "==> an earlier replay ran at $last; only ledger lines after it count"
        AFTER="$last"
    fi
fi

SERVICES=""
FROMS=""
TOS=""
COMMITS=""
SKIPPED=""
FAILED=0
DONE=0

# fail_event <service> <gate> <commit>: for a rollback that never reached a
# script that writes its own auto_rollback event.
fail_event() {
    local event
    event="$(printf '{"service":"auto-rollback","level":"error","event":"auto_rollback","outcome":"rollback_failed","deploy_service":"%s","gate":"%s","commit":"%s","_time":"%s"}' \
        "$(json_str "$1")" "$(json_str "$2")" "$(json_str "$3")" "$(now)")"
    printf '%s\n' "$event"
    if ! { mkdir -p "$(dirname "$EVENT_LOG")" && printf '%s\n' "$event" >> "$EVENT_LOG"; } 2>/dev/null; then
        echo "WARN: could not append to $EVENT_LOG; the auto_rollback event will not reach VictoriaLogs"
    fi
}

# finish <outcome> <gate> <code>
finish() {
    local report
    report="$(printf '{"service":"%s","kind":"e2e","from":"%s","to":"%s","commit":"%s","gate":"%s","outcome":"%s","ts":"%s","since":"%s","until":"%s","skipped":"%s","log":"%s"}' \
        "$(json_str "${SERVICES% }")" "$(json_str "${FROMS% }")" "$(json_str "${TOS% }")" "$(json_str "${COMMITS% }")" \
        "$2" "$1" "$(now)" "$AFTER" "$UNTIL" "$(json_str "${SKIPPED% }")" "$(json_str "$LOG")")"
    printf '%s\n' "$report" > "$REPORT_DIR/e2e-rollback.json"
    echo "ROLLBACK_REPORT $report"
    exit "$3"
}

if [[ ! -r "$LEDGER" ]]; then
    echo "::error::no readable deploy ledger at $LEDGER; cannot tell what was deployed, nothing rolled back"
    fail_event ledger e2e-no-ledger ""
    finish rollback_failed e2e-no-ledger 4
fi

# ts kind service oldest-from newest-to newest-commit count. Image before
# compose, newest deploy first. The ledger is appended in time order, so the
# first line seen is the oldest.
PLAN="$(awk -F'\t' -v after="$AFTER" -v until="$UNTIL" '
    $1 > after && $1 <= until && $7 == "deployed" && ($2 == "image" || $2 == "compose") {
        k = $2 "\t" $3
        if (!(k in from)) from[k] = $4
        n[k]++; to[k] = $5; commit[k] = $6; ts[k] = $1
    }
    END { for (k in from) print ts[k] "\t" k "\t" from[k] "\t" to[k] "\t" commit[k] "\t" n[k] }
' "$LEDGER" | sort -t "$(printf '\t')" -k2,2r -k1,1r)"

# skip_newer <service> <why>: a later deploy is live; leave it, say so.
skip_newer() {
    echo "::warning::$1: $2; skipped (gate e2e-newer-deploy-running)"
    SKIPPED+="$1=e2e-newer-deploy-running "
}

echo "==> rolling back what was deployed in ($AFTER, $UNTIL] (last green E2E: $SINCE)"
while IFS=$'\t' read -r -u 3 _ts kind svc from to commit n; do
    [[ -n "$kind" ]] || continue
    if (( REHEARSE )); then
        [[ "$kind/$svc" == "image/$CANARY" ]] || continue
    elif [[ "$svc" == "$CANARY" ]]; then
        echo "==> $CANARY: skipped, it is the rehearsal service"
        continue
    fi
    if [[ "$kind" == compose ]]; then
        if [[ "$(cat "$COMPOSE_MARKER" 2>/dev/null)" != "$to" ]]; then
            skip_newer compose "the host is no longer on the sync of $to"
            continue
        fi
    else
        cid="$("$DOCKER" compose ps -q "$svc" 2>/dev/null | head -n1)"
        running="$([[ -z "$cid" ]] || "$DOCKER" inspect --format '{{.Image}}' "$cid" 2>/dev/null)"
        newest="$("$DOCKER" image inspect --format '{{.Id}}' "$to" 2>/dev/null)"
        if [[ -n "$running" && "$running" != "$newest" ]]; then
            skip_newer "$svc" "it runs $running, not $to from the window"
            continue
        fi
    fi
    DONE=$((DONE + 1))
    SERVICES+="$svc "
    FROMS+="$svc=$from "
    TOS+="$svc=$to "
    COMMITS+="$commit "
    if [[ "$kind" == compose ]]; then
        if (( n > 1 )); then
            echo "::error::compose: $n syncs in the window and the *.prev copies reach back one; nothing restored"
            fail_event compose e2e-several-syncs "$commit"
            FAILED=$((FAILED + 1))
            continue
        fi
        if [[ "$(sed -n 's/^to=//p' "$PREV_STATE" 2>/dev/null)" != "$to" ]]; then
            echo "::error::compose: $PREV_STATE does not belong to the sync of $to; nothing restored"
            fail_event compose e2e-no-prev-state "$commit"
            FAILED=$((FAILED + 1))
            continue
        fi
        echo "==> compose: undoing the sync of $to"
        # A subshell: rb_finish ends with exit.
        # shellcheck source=/dev/null
        ( source "$COMPOSE_ROLLBACK" && rb_replay e2e ) < /dev/null
        rc=$?
    elif [[ "$from" == none ]]; then
        echo "::error::$svc: the first deploy in the window had no previous image; nothing to go back to"
        fail_event "$svc" e2e-no-previous-image "$commit"
        FAILED=$((FAILED + 1))
        continue
    else
        probe=()
        url="$(report_field "$REPORT_DIR/$svc.json" probe)"
        net="$(report_field "$REPORT_DIR/$svc.json" probe_network)"
        [[ -z "$url" ]] || probe=(--probe "$url" --probe-network "${net:-klai-net}")
        echo "==> $svc: back to $from ($n deploy(s) in the window, newest $to)"
        GITHUB_SHA="$commit" "$DEPLOY" "$svc" "$from" --rollback e2e "${probe[@]}" < /dev/null
        rc=$?
    fi
    case "$rc" in
        3) ;;
        4) FAILED=$((FAILED + 1)) ;;
        *)
            echo "::error::$svc: rollback ended with exit $rc; nothing was rolled back"
            fail_event "$svc" "e2e-exit-$rc" "$commit"
            FAILED=$((FAILED + 1)) ;;
    esac
done 3<<< "$PLAN"

if (( DONE == 0 )); then
    echo "==> nothing to roll back in ($AFTER, $UNTIL]${SKIPPED:+; skipped: ${SKIPPED% }}"
    exit 0
fi

(( REHEARSE )) || printf '%s\n' "$STARTED" > "$REPLAYED_AT"

echo "==> $DONE service(s), $FAILED failed"
if (( FAILED > 0 )); then finish rollback_failed e2e 4; fi
finish rolled_back e2e 3
