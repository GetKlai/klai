#!/usr/bin/env bash
# deploy/scripts/e2e-rollback-evidence.sh <repo> <green-created>
#
# auto-rollback.yml runs this on the runner (not on core-01) to find the
# evidence that E2E prod-tenant is red for a reason outside the deploys: the
# newest completed E2E run that an earlier Auto-rollback round started after
# its rollback, created after the last green run (<green-created>), when it
# failed. Prints "<completed-at> <run-id>" for rollback-since.sh --red-after,
# or nothing.
#
# Such a run is a workflow_dispatch run on main, triggered by
# github-actions[bot] (Auto-rollback dispatches with its own token), named
# "E2E prod-tenant: after a clean rollback by auto-rollback run <id>" (the
# run-name of e2e-prod-tenant.yml built from the reason input; the API does not
# return inputs), and <id> is a run of auto-rollback.yml. A person who
# dispatches E2E, even with the same reason, is not the bot, so a manual red
# run is never evidence; neither is a cancelled or skipped run.
#
# Only a clean experiment counts. Auto-rollback names the run "after a clean
# rollback" when its report said rolled_back (every planned rollback
# succeeded) and "after a failed rollback" otherwise; a run named before that
# distinction existed ("after rollback by ...") cannot tell the two apart. When
# the newest post-rollback run of any of the three names is not a clean one,
# there is no evidence: after a failed rollback the deploy that broke E2E can
# still be live, and blocking on that red run would leave it there and block
# every rollback after it. The name is the handle because the round fixes it
# from its own report at the moment it starts the run, it cannot change
# afterwards, and it needs no second lookup; the round's conclusion is red
# for both outcomes, and its report artifact expires after 30 days and is
# uploaded best-effort.
#
# Fails open: when a lookup fails or answers something unexpected it prints
# nothing and exits 0, so the round rolls back exactly as it did before this
# evidence existed. That is the safer side: wrongly blocking leaves a deploy
# that breaks a customer path live with nothing else undoing it, while wrongly
# rolling back undoes a deploy that was fine (for a data store, the writes since
# its snapshot), which is the behaviour this repo already ran with, is reported
# in the issue, and stops at the next green run.
#
# Tests: deploy/scripts/tests/e2e-rollback-evidence.test.sh

set -uo pipefail

(( $# == 2 )) || { echo "usage: ${0##*/} <repo> <green-created>" >&2; exit 1; }
REPO="$1"
GREEN="$2"
TS_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'

none() { echo "::warning::$*; no evidence that E2E was red before these deploys, so the rollback goes ahead" >&2; exit 0; }

[[ "$GREEN" =~ $TS_RE ]] || none "green run creation '$GREEN' is not YYYY-MM-DDTHH:MM:SSZ"

# The query filters are repeated in --jq so the selection does not rest on
# which query parameters the API honours.
runs="$(gh api -X GET --paginate "repos/$REPO/actions/workflows/e2e-prod-tenant.yml/runs" \
    -f branch=main -f event=workflow_dispatch -f created=">=$GREEN" -f per_page=100 \
    --jq '.workflow_runs[] | select(.status == "completed" and .event == "workflow_dispatch"
        and .head_branch == "main" and .created_at >= "'"$GREEN"'"
        and .triggering_actor.login == "github-actions[bot]"
        and (.display_title | test("^E2E prod-tenant: after (a clean |a failed )?rollback by auto-rollback run [0-9]+$")))
      | (.display_title | capture("after (?<kind>a clean |a failed )?rollback by auto-rollback run (?<round>[0-9]+)$")) as $t
      | "\(.created_at) \(.id) \(.conclusion) \($t.round) \(($t.kind // "") | ltrimstr("a ") | rtrimstr(" ") | if . == "" then "unknown" else . end)"')" \
    || none "could not list the E2E prod-tenant runs"
[[ -n "$runs" ]] || exit 0
read -r _created id conclusion round kind <<< "$(sort <<< "$runs" | tail -n1)"
if [[ "$conclusion" != failure ]]; then
    echo "newest post-rollback E2E run $id ended $conclusion: no evidence" >&2
    exit 0
fi
if [[ "$kind" != clean ]]; then
    echo "newest post-rollback E2E run $id followed a rollback that was not clean ($kind): no evidence" >&2
    exit 0
fi

path="$(gh api "repos/$REPO/actions/runs/$round" --jq .path)" || none "could not read run $round named by E2E run $id"
[[ "$path" == .github/workflows/auto-rollback.yml ]] || none "E2E run $id names run $round, which is not an Auto-rollback run"

done_at="$(gh api "repos/$REPO/actions/runs/$id/jobs" --jq '[.jobs[].completed_at | select(.)] | max // empty')" \
    || none "could not read the jobs of E2E run $id"
[[ "$done_at" =~ $TS_RE ]] || none "completion '$done_at' of E2E run $id is not YYYY-MM-DDTHH:MM:SSZ"
echo "$done_at $id"
