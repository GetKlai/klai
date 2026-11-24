#!/usr/bin/env bash
# deploy/scripts/chat-image-rollout.sh <target-image> <e2e-base-url>
#
# Staged rollout of the chat image, run on the runner by chat-image-rollout.yml.
# <target-image> is portal-api's LIBRECHAT_IMAGE pin in deploy/docker-compose.yml
# (digest only); <e2e-base-url> is the E2E_BASE_URL secret, whose first DNS label
# is the slug of the tenant the production journeys log in to. That tenant is
# the canary, and it must be marked synthetic on the host (canary-check in
# chat-image-rollout-host.sh).
#
#   1. the canary tenant alone is recreated on the target, and read back;
#   2. E2E prod-tenant runs; a red run gets one confirmation run;
#   3. green: every provisioning-managed tenant is recreated on the target, one
#      at a time with portal-api's health gate (stops at the first failure),
#      and every one is read back;
#   4. E2E runs again, same confirmation rule;
#   5. green: the target becomes the stable image. Red at 2: only the canary
#      goes back. Red at 4: the whole fleet goes back.
#
# Why: on 2026-11-24 the image moved from upstream v0.8.8 to v0.8.9 by
# recreating all 45 tenants first and running the journeys after. They went red
# (a renamed send-button id); had the image itself been broken, every customer
# would have had it before any proof ran.
#
# The state file on core-01 says what every tenant should run, and portal-api
# creates containers from it. Each step writes the state first and moves the
# containers second, and a phase is only made terminal (stable, rejected) after
# the containers were read back and match it. Every run starts by reconciling:
# any provisioning-managed container that does not run what the state implies is
# recreated, and still differing tenants stop the run by name. So a run that
# dies anywhere leaves a state the next run makes true:
#   canary    unproven; goes to reverting, the canary goes back, then a fresh
#             canary runs
#   fleet     the canary was proven for that candidate: the fleet move and its
#             proof resume; for any other candidate it reverts first
#   reverting a put-back in progress; reconciled, then its `next` phase
#             (rejected after a red proof, stable after an aborted canary)
#   rejected  not tried again unless ROLLOUT_RETRY_REJECTED=true
#   stable    nothing in flight
# No state file: ensure-baseline writes one from the image the fleet runs.
#
# An E2E run counts only when it was created after this script recorded the
# newest existing run, carries this run's title (run id, run attempt, step and
# dispatch attempt), and its job started after the move it proves.
#
# E2E runs are started as workflow_dispatch, which Auto-rollback ignores, and
# Auto-rollback also skips red runs that overlap this workflow's rollout job, so
# the two never undo each other's work (see auto-rollback.yml).
#
# Exit: 0 rolled out, or nothing to do; 1 rejected (what moved was put back);
#       3 waiting, nothing moved: E2E is red for another reason, or the target
#         was rejected by an earlier run;
#       4 a person has to look (refused canary, a put-back or read-back failed);
#       2 usage.
#
# Env: ROLLOUT_FORCE=true runs the sequence even when the target is already
#      stable (the no-op exercise). ROLLOUT_PLAN_ONLY=true prints move=true or
#      move=false and moves nothing (it may write the baseline). ROLLOUT_HOST
#      replaces the ssh transport (tests); otherwise ROLLOUT_SSH_TARGET
#      (user@host) is required. E2E_WAIT_SECONDS (default 2700),
#      E2E_POLL_SECONDS (30).
#
# Tests: deploy/scripts/tests/chat-image-rollout.test.sh

set -euo pipefail

(( $# == 2 )) || { echo "usage: ${0##*/} <target-image> <e2e-base-url>" >&2; exit 2; }
TARGET="$1"
DIGEST_RE='^[^[:space:]@]+@sha256:[0-9a-f]{64}$'
[[ "$TARGET" =~ $DIGEST_RE ]] || { echo "target '$TARGET' is not pinned by digest" >&2; exit 2; }
host_part="${2#*://}"
host_part="${host_part%%/*}"
SLUG="${host_part%%.*}"
[[ "$SLUG" =~ ^[a-z0-9][a-z0-9-]{0,57}$ && "$host_part" == *.* ]] \
  || { echo "cannot derive the canary tenant from the E2E base URL" >&2; exit 2; }

REPO="${GITHUB_REPOSITORY:-}"
RUN_ID="${GITHUB_RUN_ID:-local}"
RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"
WAIT="${E2E_WAIT_SECONDS:-2700}"
POLL="${E2E_POLL_SECONDS:-30}"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="/tmp/chat-image-rollout-$RUN_ID-$RUN_ATTEMPT"

say() { echo "chat-image-rollout: $*"; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# One op on core-01, from the scripts of this commit.
if [[ -n "${ROLLOUT_HOST:-}" ]]; then
  host() { "$ROLLOUT_HOST" "$@"; }
else
  : "${ROLLOUT_SSH_TARGET:?ROLLOUT_SSH_TARGET (user@host) is required}"
  SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 "$ROLLOUT_SSH_TARGET")
  tar -cf - -C "$SCRIPTS" chat-image-rollout-host.sh tenant-config-regenerate.sh \
    | "${SSH[@]}" "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR && tar -xf - -C $REMOTE_DIR"
  trap '"${SSH[@]}" "rm -rf $REMOTE_DIR" || true' EXIT
  # Every argument is a slug or nothing, both validated above, so plain word
  # joining is safe here.
  host() { "${SSH[@]}" "bash $REMOTE_DIR/chat-image-rollout-host.sh $*"; }
fi

# Stop before the first container move in plan mode. The workflow runs the plan
# as its own job, so a run that moved containers can be told apart from one
# that did not by whether its rollout job ran (Auto-rollback reads that).
about_to_move() { if [[ "${ROLLOUT_PLAN_ONLY:-}" == true ]]; then echo "move=true"; exit 0; fi; }
nothing_moves() { echo "move=false"; exit "$1"; }

PHASE="" STABLE="" CANDIDATE="" NEXT=""

# write <phase>: the state the next container create will follow.
write() {
  PHASE="$1"
  jq -n --arg phase "$PHASE" --arg stable "$STABLE" --arg candidate "$CANDIDATE" --arg canary "$SLUG" \
    --arg next "$NEXT" --arg commit "${GITHUB_SHA:-}" --arg run "$RUN_ID" --arg at "$(now)" \
    '{phase: $phase, stable: $stable, candidate: $candidate, canary: $canary, next: $next, commit: $commit, run: $run, updated_at: $at}' \
    | host write-state || { say "ERROR: could not write the $PHASE state" >&2; exit 4; }
  say "state: $PHASE (stable $STABLE, candidate ${CANDIDATE:-none})"
}

# What a tenant should run under the current state: portal-api's own rule
# (ImageRolloutState.image_for).
expected() {
  if [[ "$PHASE" == fleet || ( "$PHASE" == canary && "$1" == "$SLUG" ) ]]; then echo "$CANDIDATE"; else echo "$STABLE"; fi
}

# Tenants whose running image differs from the state, one slug per line.
mismatches() {
  local listing slug image
  listing="$(host images)" || { say "ERROR: cannot read the running images" >&2; exit 4; }
  while read -r slug image; do
    [[ -n "$slug" ]] || continue
    [[ "$image" == "$(expected "$slug")" ]] || echo "$slug"
  done <<< "$listing"
}

# verify: every provisioning-managed container runs what the state says.
verify() {
  local off
  off="$(mismatches)"
  [[ -z "$off" ]] && return 0
  say "not on the image the $PHASE state names: ${off//$'\n'/, }" >&2
  return 1
}

# reconcile: make the containers match the state, one tenant at a time, and
# stop by name when that does not work.
reconcile() {
  local off slug
  off="$(mismatches)"
  [[ -n "$off" ]] || return 0
  about_to_move
  say "reconciling ${off//$'\n'/, } to the $PHASE state"
  for slug in $off; do host recreate "$slug" || true; done
  verify || { say "ERROR: these tenants do not run what the state says; a person has to look" >&2; exit 4; }
}

# e2e <step> <moved-at>: start one E2E prod-tenant run and print its
# conclusion. A pending run the E2E concurrency group cancelled proves nothing,
# so it is started again, twice at most.
e2e() {
  local step="$1" moved="$2" title floor id status conclusion started deadline attempt line
  for attempt in 1 2 3; do
    title="E2E prod-tenant: chat image rollout run $RUN_ID.$RUN_ATTEMPT, $step, attempt $attempt"
    # Only a run created after this point can be the one dispatched below:
    # run ids only grow, and a re-run of this workflow keeps its run id.
    floor="$(gh run list --repo "$REPO" --workflow e2e-prod-tenant.yml --limit 1 --json databaseId \
      --jq '.[0].databaseId // 0')" || { echo error; return; }
    gh workflow run e2e-prod-tenant.yml --repo "$REPO" --ref main -f reason="${title#E2E prod-tenant: }" >&2
    deadline=$(( SECONDS + WAIT ))
    status="" conclusion=""
    while (( SECONDS < deadline )); do
      sleep "$POLL"
      line="$(gh run list --repo "$REPO" --workflow e2e-prod-tenant.yml --event workflow_dispatch --limit 30 \
        --json databaseId,displayTitle,status,conclusion \
        --jq "[.[] | select(.displayTitle == \"$title\" and .databaseId > $floor)][0] // empty | \"\(.databaseId) \(.status) \(.conclusion)\"")" || true
      [[ -n "$line" ]] || continue
      read -r id status conclusion <<< "$line"
      [[ "$status" == completed ]] && break
    done
    if [[ "$status" != completed ]]; then
      say "E2E run for $step did not finish within $WAIT s" >&2
      echo timeout
      return
    fi
    [[ "$conclusion" == cancelled ]] && { say "E2E run $id ($step) was cancelled" >&2; continue; }
    started="$(gh api "repos/$REPO/actions/runs/$id/jobs" --jq '[.jobs[].started_at | select(.)] | min // empty')" || started=""
    if [[ -z "$started" || "$started" < "$moved" ]]; then
      say "E2E run $id ($step) started its job at '${started:-unknown}', before the move at $moved: no proof" >&2
      echo stale
      return
    fi
    say "E2E run $id ($step): $conclusion" >&2
    echo "$conclusion"
    return
  done
  echo cancelled
}

# proven <step> <moved-at>: green, or red twice in a row. The suite fails now
# and then for reasons outside the code (7 of 60 runs on 2026-10-22/23), so one
# red run does not reject an image; that is the same rule Auto-rollback uses.
proven() {
  [[ "$(e2e "$1" "$2")" == success ]] && return 0
  [[ "$(e2e "$1 confirmation" "$2")" == success ]]
}

# The last finished E2E run must be green before anything moves: on a suite
# that is red for another reason the canary can prove nothing.
baseline_green() {
  local last
  last="$(gh run list --repo "$REPO" --workflow e2e-prod-tenant.yml --branch main --status completed --limit 20 \
    --json conclusion --jq '[.[] | select(.conclusion != "cancelled" and .conclusion != "skipped")][0].conclusion // empty')" || return 1
  [[ "$last" == success ]]
}

# revert <next>: everyone back to stable, read back, then the terminal phase.
revert() {
  NEXT="$1"
  write reverting
  reconcile
  CANDIDATE_WAS="$CANDIDATE"
  [[ "$NEXT" == stable ]] && CANDIDATE=""
  write "$NEXT"
  NEXT=""
}

put_back_canary() {
  revert rejected
  say "rejected $CANDIDATE_WAS; only the canary had moved and it is back on $STABLE"
  exit 1
}

put_back_fleet() {
  revert rejected
  if ! proven "after fleet rollback" "$(now)"; then
    say "ERROR: the fleet is back on $STABLE but E2E is still red; a person has to look" >&2
    exit 4
  fi
  say "rejected $CANDIDATE_WAS after the fleet step; every tenant is back on $STABLE"
  exit 1
}

fleet_step() {
  host recreate || put_back_fleet
  fleet_proof
}

# The fleet runs the candidate (read back), then the second proof.
fleet_proof() {
  verify || put_back_fleet
  proven fleet "$(now)" || put_back_fleet
  CANDIDATE="" STABLE="$TARGET"
  write stable
  say "every tenant runs $TARGET"
  exit 0
}

host ensure-baseline || { say "ERROR: no rollout state and no single image the fleet runs" >&2; exit 4; }
state="$(host read-state)" || { say "ERROR: cannot read the rollout state on core-01" >&2; exit 4; }
PHASE="$(jq -r .phase <<< "$state")"
STABLE="$(jq -r '.stable // ""' <<< "$state")"
CANDIDATE="$(jq -r '.candidate // ""' <<< "$state")"
NEXT="$(jq -r '.next // ""' <<< "$state")"
[[ "$STABLE" =~ $DIGEST_RE ]] || { say "ERROR: stable image '$STABLE' is not pinned by digest" >&2; exit 4; }

host canary-check "$SLUG" || { say "ERROR: refusing '$SLUG' as the canary" >&2; exit 4; }

# Interrupted states that must not carry on as they are.
case "$PHASE" in
  canary)
    about_to_move
    say "an interrupted canary of $CANDIDATE: it goes back to $STABLE first"
    revert stable
    ;;
  fleet)
    if [[ "$CANDIDATE" != "$TARGET" ]]; then
      about_to_move
      say "an interrupted fleet step for $CANDIDATE, but the pin is now $TARGET: back to $STABLE first"
      revert stable
    fi
    ;;
esac

reconcile

case "$PHASE" in
  reverting)
    say "finishing an interrupted put-back of $CANDIDATE"
    terminal="${NEXT:-rejected}"
    CANDIDATE_WAS="$CANDIDATE"
    [[ "$terminal" == stable ]] && CANDIDATE=""
    NEXT=""
    write "$terminal"
    if [[ "$terminal" == rejected ]]; then
      say "rejected $CANDIDATE_WAS; every tenant is back on $STABLE"
      exit 1
    fi
    ;;
  fleet)
    about_to_move
    # reconcile above already moved whoever was still on the stable image.
    say "resuming the fleet step for $TARGET, whose canary was proven"
    fleet_proof
    ;;
  rejected)
    if [[ "$CANDIDATE" == "$TARGET" && "${ROLLOUT_RETRY_REJECTED:-}" != true ]]; then
      say "$TARGET was rejected earlier; pin another image, or dispatch with retry_rejected"
      nothing_moves 3
    fi
    ;;
esac

if [[ "$STABLE" == "$TARGET" && "${ROLLOUT_FORCE:-}" != true ]]; then
  say "every tenant already runs $TARGET; nothing to do"
  nothing_moves 0
fi

if ! baseline_green; then
  say "the last E2E prod-tenant run on main is not green; nothing moved, the next run tries again"
  nothing_moves 3
fi
about_to_move

CANDIDATE="$TARGET"
write canary
host recreate "$SLUG" || put_back_canary
verify || put_back_canary
proven canary "$(now)" || put_back_canary

say "canary green on $TARGET; moving every tenant"
write fleet
fleet_step
