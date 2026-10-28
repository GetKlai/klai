#!/usr/bin/env bash
# /opt/klai/scripts/rollback-since.sh
#
# Put core-01 back to what ran when E2E prod-tenant was last green
# (SPEC-INFRA-AUTO-ROLLBACK-001 section 6, slice S6). auto-rollback.yml runs it
# over ssh after a red E2E run on main, with <since-ts> = the time the last green
# run's job started, read from the GitHub API.
#
# Usage:
#   rollback-since.sh <since-ts> [--rehearse]
#
#   <since-ts>   UTC in the ledger's own format (2026-10-28T04:00:00Z); ledger
#                lines are compared to it as text.
#   --rehearse   handle the rollback-canary service only, so the whole chain can
#                be exercised on production without touching a real service.
#
# One pass over the deploy ledger, every service with a `deployed` line after
# <since-ts>, newest deploy first:
#   image    back to the `from` of its OLDEST deployed line since then, which is
#            the image that ran when E2E was green, through
#            deploy-with-rollback.sh --rollback e2e (its gates, ledger line,
#            report and auto_rollback event).
#   compose  the sync is undone from its *.prev copies (compose-rollback.sh
#            rb_replay, which keeps the .compose-rollback-failed and
#            self-migrating-image rules) only when it is the only sync since
#            <since-ts> and its .compose-prev-state is still there. The copies
#            reach back one sync, so anything else is reported and counts as
#            failed.
#   rollback-canary is skipped, except with --rehearse, which skips the rest.
#
# Never loops (R6): a real run records its start time in .e2e-rollback-at, and
# later runs only read ledger lines after the later of <since-ts> and that time,
# so a deploy is undone once even when E2E stays red.
#
# Exit codes
#   0  nothing deployed since <since-ts>; nothing changed
#   3  every service found was rolled back                     (rolled_back)
#   4  at least one rollback failed or could not be attempted  (rollback_failed)
#   1  bad arguments
#
# Report: <reports>/e2e-rollback.json, one object in the shape
# ci-failure-notice.yml reads, also printed as "ROLLBACK_REPORT <json>"; the
# full output goes to <reports>/e2e-rollback.log.
#
# Tests: deploy/scripts/tests/rollback-since.test.sh

set -uo pipefail

# Seams for the test-suite; production keeps every default.
KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
DEPLOY="${KLAI_DEPLOY_WITH_ROLLBACK:-$KLAI_DIR/scripts/deploy-with-rollback.sh}"
COMPOSE_ROLLBACK="${KLAI_COMPOSE_ROLLBACK:-$KLAI_DIR/scripts/compose-rollback.sh}"
LEDGER="${KLAI_DEPLOY_LEDGER:-$KLAI_DIR/deploy-ledger.log}"
REPORT_DIR="${KLAI_DEPLOY_REPORTS:-$KLAI_DIR/deploy-reports}"
EVENT_LOG="${KLAI_AUTO_ROLLBACK_LOG:-$KLAI_DIR/logs/auto-rollback.log}"
REPLAYED_AT="$KLAI_DIR/.e2e-rollback-at"
# Written by compose-rollback.sh (RB_STATE).
PREV_STATE="$KLAI_DIR/.compose-prev-state"
CANARY="rollback-canary"

usage() { echo "usage: ${0##*/} <since-ts YYYY-MM-DDTHH:MM:SSZ> [--rehearse]" >&2; exit 1; }

(( $# == 1 || $# == 2 )) || usage
SINCE="$1"
[[ "$SINCE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || usage
REHEARSE=0
if (( $# == 2 )); then
    [[ "$2" == --rehearse ]] || usage
    REHEARSE=1
    # The canary sits in a compose profile; without it compose does not see it.
    export COMPOSE_PROFILES="rollback-rehearsal"
fi

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
json_str() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

cd "$KLAI_DIR" || exit 1
mkdir -p "$REPORT_DIR"
LOG="$REPORT_DIR/e2e-rollback.log"
exec > >(tee "$LOG") 2>&1

STARTED="$(now)"
AFTER="$SINCE"
if (( ! REHEARSE )) && [[ -s "$REPLAYED_AT" ]]; then
    last="$(cat "$REPLAYED_AT")"
    if [[ "$last" > "$AFTER" ]]; then
        echo "==> an earlier replay ran at $last; only ledger lines after it count"
        AFTER="$last"
    fi
fi

# ts kind service oldest-from newest-to newest-commit count, newest deploy first.
# The ledger is appended in time order, so the first line seen is the oldest.
PLAN="$(awk -F'\t' -v after="$AFTER" '
    $1 > after && $7 == "deployed" && ($2 == "image" || $2 == "compose") {
        k = $2 "\t" $3
        if (!(k in from)) from[k] = $4
        n[k]++; to[k] = $5; commit[k] = $6; ts[k] = $1
    }
    END { for (k in from) print ts[k] "\t" k "\t" from[k] "\t" to[k] "\t" commit[k] "\t" n[k] }
' "$LEDGER" 2>/dev/null | sort -r)"

SERVICES=""
FROMS=""
TOS=""
COMMITS=""
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

echo "==> rolling back what was deployed after $AFTER (last green E2E: $SINCE)"
while IFS=$'\t' read -r -u 3 _ts kind svc from to commit n; do
    [[ -n "$kind" ]] || continue
    if (( REHEARSE )); then
        [[ "$kind/$svc" == "image/$CANARY" ]] || continue
    elif [[ "$svc" == "$CANARY" ]]; then
        echo "==> $CANARY: skipped, it is the rehearsal service"
        continue
    fi
    DONE=$((DONE + 1))
    SERVICES+="$svc "
    FROMS+="$svc=$from "
    TOS+="$svc=$to "
    COMMITS+="$commit "
    if [[ "$kind" == compose ]]; then
        if (( n > 1 )); then
            echo "::error::compose: $n syncs since $AFTER and the *.prev copies reach back one; nothing restored"
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
        echo "::error::$svc: the first deploy since $AFTER had no previous image; nothing to go back to"
        fail_event "$svc" e2e-no-previous-image "$commit"
        FAILED=$((FAILED + 1))
        continue
    else
        echo "==> $svc: back to $from ($n deploy(s) since $AFTER, newest $to)"
        GITHUB_SHA="$commit" "$DEPLOY" "$svc" "$from" --rollback e2e < /dev/null
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
    echo "==> nothing deployed after $AFTER; nothing rolled back"
    exit 0
fi

(( REHEARSE )) || printf '%s\n' "$STARTED" > "$REPLAYED_AT"

OUTCOME="rolled_back"
CODE=3
if (( FAILED > 0 )); then OUTCOME="rollback_failed"; CODE=4; fi
REPORT="$(printf '{"service":"%s","kind":"e2e","from":"%s","to":"%s","commit":"%s","gate":"e2e","outcome":"%s","ts":"%s","since":"%s","log":"%s"}' \
    "$(json_str "${SERVICES% }")" "$(json_str "${FROMS% }")" "$(json_str "${TOS% }")" "$(json_str "${COMMITS% }")" \
    "$OUTCOME" "$(now)" "$AFTER" "$(json_str "$LOG")")"
printf '%s\n' "$REPORT" > "$REPORT_DIR/e2e-rollback.json"
echo "==> $DONE service(s), $FAILED failed: $OUTCOME"
echo "ROLLBACK_REPORT $REPORT"
exit "$CODE"
