#!/usr/bin/env bash
# deploy/scripts/chat-image-rollout.sh <target-image> <e2e-base-url>
#
# Staged rollout of the chat image, run on the runner by chat-image-rollout.yml.
# <target-image> is portal-api's LIBRECHAT_IMAGE pin in deploy/docker-compose.yml
# (digest only); <e2e-base-url> is the E2E_BASE_URL secret, whose first DNS label
# is the slug of the tenant the production journeys log in to. That tenant is
# the canary.
#
#   1. the canary tenant alone is recreated on the target;
#   2. E2E prod-tenant runs; a red run gets one confirmation run;
#   3. green: every provisioning-managed tenant is recreated on the target, one
#      at a time with portal-api's health gate (stops at the first failure);
#   4. E2E runs again, same confirmation rule;
#   5. green: the target becomes the stable image. Red at 2: only the canary
#      goes back. Red at 4: the whole fleet goes back.
#
# Why: on 2026-11-24 the image moved from upstream v0.8.8 to v0.8.9 by
# recreating all 45 tenants first and running the journeys after. They went red
# (a renamed send-button id); had the image itself been broken, every customer
# would have had it before any proof ran.
#
# Every step is written to the state file on core-01 BEFORE the containers move
# (chat-image-rollout-host.sh), and portal-api creates containers from that
# state, so a run that dies halfway leaves a state the next run reads:
#   canary   unproven; the canary goes back to stable, then a fresh canary runs
#   fleet    the canary was proven for that candidate: the fleet step resumes;
#            for any other candidate the fleet goes back to stable first
#   rejected the candidate failed; it is not tried again unless
#            ROLLOUT_RETRY_REJECTED=true (a new pin is tried normally)
#   stable   nothing in flight
# No state file yet: stable is what the canary tenant's container runs now.
#
# E2E runs are started as workflow_dispatch, which Auto-rollback ignores, and
# Auto-rollback also skips red runs that overlap a run of this workflow, so
# the two never undo each other's work (see auto-rollback.yml).
#
# Exit: 0 rolled out, or nothing to do; 1 rejected (what moved was put back);
#       3 waiting, nothing moved: E2E is red for another reason, or the target
#         was rejected by an earlier run;
#       4 a put-back failed, a person has to look; 2 usage.
#
# Env: ROLLOUT_FORCE=true runs the sequence even when the target is already
#      stable (the no-op exercise). ROLLOUT_HOST replaces the ssh transport
#      (tests); otherwise ROLLOUT_SSH_TARGET (user@host) is required.
#      E2E_WAIT_SECONDS (default 2700), E2E_POLL_SECONDS (30).
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
WAIT="${E2E_WAIT_SECONDS:-2700}"
POLL="${E2E_POLL_SECONDS:-30}"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="/tmp/chat-image-rollout-$RUN_ID"

say() { echo "chat-image-rollout: $*"; }

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

STABLE="" CANDIDATE=""

# write <phase>: the state the next container create will follow.
write() {
  jq -n --arg phase "$1" --arg stable "$STABLE" --arg candidate "$CANDIDATE" --arg canary "$SLUG" \
    --arg commit "${GITHUB_SHA:-}" --arg run "$RUN_ID" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{phase: $phase, stable: $stable, candidate: $candidate, canary: $canary, commit: $commit, run: $run, updated_at: $at}' \
    | host write-state
  say "state: $1 (stable $STABLE, candidate ${CANDIDATE:-none})"
}

# e2e <step>: start one E2E prod-tenant run and print its conclusion. A run the
# E2E concurrency group cancelled while it was pending proves nothing either
# way, so it is started again, twice at most; still no verdict is a failure.
e2e() {
  local title id status conclusion deadline attempt
  for attempt in 1 2 3; do
    title="E2E prod-tenant: chat image rollout run $RUN_ID, $1, attempt $attempt"
    gh workflow run e2e-prod-tenant.yml --repo "$REPO" --ref main -f reason="${title#E2E prod-tenant: }" >&2
    deadline=$(( SECONDS + WAIT ))
    status="" conclusion=""
    while (( SECONDS < deadline )); do
      sleep "$POLL"
      line="$(gh run list --repo "$REPO" --workflow e2e-prod-tenant.yml --event workflow_dispatch --limit 30 \
        --json databaseId,displayTitle,status,conclusion \
        --jq "[.[] | select(.displayTitle == \"$title\")][0] // empty | \"\(.databaseId) \(.status) \(.conclusion)\"")" || true
      [[ -n "$line" ]] || continue
      read -r id status conclusion <<< "$line"
      [[ "$status" == completed ]] && break
    done
    if [[ "$status" != completed ]]; then
      say "E2E run for $1 did not finish within $WAIT s" >&2
      echo timeout
      return
    fi
    say "E2E run $id ($1): $conclusion" >&2
    [[ "$conclusion" == cancelled ]] || { echo "$conclusion"; return; }
  done
  echo cancelled
}

# proven <step>: green, or red twice in a row. The suite fails now and then for
# reasons outside the code (7 of 60 runs on 2026-10-22/23), so one red run does
# not reject an image; that is the same rule Auto-rollback uses.
proven() {
  [[ "$(e2e "$1")" == success ]] && return 0
  [[ "$(e2e "$1 confirmation")" == success ]]
}

# The last finished E2E run must be green before anything moves: on a suite
# that is red for another reason the canary can prove nothing.
baseline_green() {
  local last
  last="$(gh run list --repo "$REPO" --workflow e2e-prod-tenant.yml --branch main --status completed --limit 20 \
    --json conclusion --jq '[.[] | select(.conclusion != "cancelled" and .conclusion != "skipped")][0].conclusion // empty')" || return 1
  [[ "$last" == success ]]
}

put_back_canary() {
  write rejected
  host recreate "$SLUG" || { say "ERROR: the canary did not go back to $STABLE; a person has to look" >&2; exit 4; }
  say "rejected $CANDIDATE; only the canary had moved and it is back on $STABLE"
  exit 1
}

put_back_fleet() {
  write rejected
  host recreate || { say "ERROR: the fleet did not go back to $STABLE; a person has to look" >&2; exit 4; }
  if ! proven "after fleet rollback"; then
    say "ERROR: the fleet is back on $STABLE but E2E is still red; a person has to look" >&2
    exit 4
  fi
  say "rejected $CANDIDATE after the fleet step; every tenant is back on $STABLE"
  exit 1
}

# ROLLOUT_PLAN_ONLY=true stops before the first change and prints move=true;
# every path that changes nothing prints move=false. The workflow runs this as
# its own job first, so a run that moved containers can be told apart from one
# that did not by whether its rollout job ran (Auto-rollback reads that).
about_to_move() { if [[ "${ROLLOUT_PLAN_ONLY:-}" == true ]]; then echo "move=true"; exit 0; fi; }

state="$(host read-state)" || { say "ERROR: cannot read the rollout state on core-01" >&2; exit 4; }
if [[ -z "$state" ]]; then
  phase=none
  STABLE="$(host running-image "$SLUG")" || { say "ERROR: cannot read the canary's current image" >&2; exit 4; }
  say "no rollout state yet; the canary runs $STABLE, taken as stable"
else
  phase="$(jq -r .phase <<< "$state")"
  STABLE="$(jq -r '.stable // ""' <<< "$state")"
  CANDIDATE="$(jq -r '.candidate // ""' <<< "$state")"
fi
[[ "$STABLE" =~ $DIGEST_RE ]] || { say "ERROR: stable image '$STABLE' is not pinned by digest" >&2; exit 4; }

case "$phase" in
  fleet)
    if [[ "$CANDIDATE" == "$TARGET" ]]; then
      about_to_move
      say "resuming the fleet step for $TARGET, whose canary was proven"
      host recreate || put_back_fleet
      proven fleet || put_back_fleet
      CANDIDATE="" STABLE="$TARGET"
      write stable
      say "every tenant runs $TARGET"
      exit 0
    fi
    about_to_move
    say "an interrupted fleet step for $CANDIDATE: back to $STABLE first"
    CANDIDATE=""
    write stable
    host recreate || { say "ERROR: the fleet did not go back to $STABLE" >&2; exit 4; }
    ;;
  canary)
    about_to_move
    say "an interrupted canary of $CANDIDATE: the canary goes back to $STABLE first"
    CANDIDATE=""
    write stable
    host recreate "$SLUG" || { say "ERROR: the canary did not go back to $STABLE" >&2; exit 4; }
    ;;
  rejected)
    if [[ "$CANDIDATE" == "$TARGET" && "${ROLLOUT_RETRY_REJECTED:-}" != true ]]; then
      say "$TARGET was rejected earlier; pin another image, or dispatch with retry_rejected"
      echo "move=false"
      exit 3
    fi
    ;;
esac

if [[ "$STABLE" == "$TARGET" && "${ROLLOUT_FORCE:-}" != true ]]; then
  say "every tenant already runs $TARGET; nothing to do"
  echo "move=false"
  exit 0
fi

if ! baseline_green; then
  say "the last E2E prod-tenant run on main is not green; nothing moved, the next run tries again"
  echo "move=false"
  exit 3
fi
about_to_move

CANDIDATE="$TARGET"
write canary
host recreate "$SLUG" || put_back_canary
proven canary || put_back_canary

say "canary green on $TARGET; moving every tenant"
write fleet
host recreate || put_back_fleet
proven fleet || put_back_fleet

CANDIDATE="" STABLE="$TARGET"
write stable
say "every tenant runs $TARGET"
