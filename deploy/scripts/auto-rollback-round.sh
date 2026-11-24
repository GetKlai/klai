#!/usr/bin/env bash
# deploy/scripts/auto-rollback-round.sh, run by auto-rollback.yml on the runner.
#
# Two decisions of a round, kept out of the workflow so they have tests:
#
#   until <red-run-created>
#       The end of the replay window: the creation time of the red run, or now
#       for a rehearsal (no red run). A deploy that landed after that is not
#       what the red run saw; rollback-since.sh leaves it for the next round's
#       window, and a later red run judges it. Ending the window at the replay
#       instead would roll back deploys that neither red run had to test (13 of
#       41 confirmation runs between 10 and 24 November 2026 had one landing
#       during them), and a second compose sync in the window turns a clean
#       rollback into rollback_failed.
#   reason <outcome> <round-run-id>
#       The reason input, and so the run name, of the E2E run started after the
#       rollback: "after a clean rollback by ..." only for outcome rolled_back
#       (every planned rollback succeeded, none skipped), "after a partial
#       rollback" for rolled_back_partial, "after a failed rollback" otherwise.
#       e2e-rollback-evidence.sh takes only the clean form as evidence.
#
# Tests: deploy/scripts/tests/auto-rollback-round.test.sh

set -uo pipefail

TS_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'

case "${1:-}" in
    until)
        (( $# == 2 )) || { echo "usage: ${0##*/} until <red-run-created>" >&2; exit 1; }
        until="${2:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
        [[ "$until" =~ $TS_RE ]] || { echo "::error::window end '$until' is not YYYY-MM-DDTHH:MM:SSZ" >&2; exit 1; }
        echo "$until" ;;
    reason)
        (( $# == 3 )) && [[ "$3" =~ ^[0-9]+$ ]] || { echo "usage: ${0##*/} reason <outcome> <round-run-id>" >&2; exit 1; }
        case "$2" in
            rolled_back) kind=clean ;;
            rolled_back_partial) kind=partial ;;
            *) kind=failed ;;
        esac
        echo "after a $kind rollback by auto-rollback run $3" ;;
    *)
        echo "usage: ${0##*/} until <red-run-created> | reason <outcome> <round-run-id>" >&2
        exit 1 ;;
esac
