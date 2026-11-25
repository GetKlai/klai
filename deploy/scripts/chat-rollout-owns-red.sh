#!/usr/bin/env bash
# deploy/scripts/chat-rollout-owns-red.sh <repo> <red-created> <red-updated>
#
# auto-rollback.yml runs this on the runner: does a red E2E prod-tenant run
# (created .. updated) overlap the `rollout` job of a Chat image rollout run?
# That job moves tenants to a new chat image and answers a red E2E run itself
# by putting back what it moved, so Auto-rollback must not also undo unrelated
# deploys for it. Prints the overlapping run id, or nothing.
#
# Only the `rollout` job counts: the `plan` job moves no container, and the
# hourly run of the workflow is mostly a plan that finds nothing to do. Job
# timestamps from the API, not a run name.
#
# Fails open: a failed lookup prints nothing and exits 0, so the round goes
# ahead exactly as it did before the rollout existed.
#
# Tests: deploy/scripts/tests/chat-rollout-owns-red.test.sh

set -uo pipefail

(( $# == 3 )) || { echo "usage: ${0##*/} <repo> <red-created> <red-updated>" >&2; exit 1; }
REPO="$1" CREATED="$2" UPDATED="$3"

ids="$(gh api "repos/$REPO/actions/workflows/chat-image-rollout.yml/runs?branch=main&per_page=30" \
  --jq '.workflow_runs[] | select(.run_started_at <= "'"$UPDATED"'" and .updated_at >= "'"$CREATED"'") | .id')" \
  || { echo "::warning::could not list Chat image rollout runs; treating the red run as Auto-rollback's" >&2; exit 0; }
for id in $ids; do
  overlap="$(gh api "repos/$REPO/actions/runs/$id/jobs" --jq '[.jobs[]
    | select(.name == "rollout" and .started_at != null and .started_at <= "'"$UPDATED"'"
             and (.completed_at == null or .completed_at >= "'"$CREATED"'"))] | length')" || overlap=0
  if [[ "$overlap" != 0 ]]; then
    echo "$id"
    exit 0
  fi
done
