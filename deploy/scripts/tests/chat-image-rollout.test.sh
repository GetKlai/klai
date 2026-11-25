#!/usr/bin/env bash
# Tests for chat-image-rollout.sh (the staged rollout's decisions) and
# chat-image-rollout-host.sh (its core-01 ops).
#
# The driver runs for real. ROLLOUT_HOST is a fake core-01: it keeps the state
# file and one running image per tenant in a directory, and a recreate gives a
# tenant the image the state names, the way portal-api resolves it
# (ImageRolloutState.image_for). gh is a stub on PATH that answers E2E runs
# from a queue of conclusions and applies the script's own --jq filters, so the
# real run selection and polling run too.
#
# Run: bash deploy/scripts/tests/chat-image-rollout.test.sh

set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
driver="$script_dir/chat-image-rollout.sh"
hostops="$script_dir/chat-image-rollout-host.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
failures=0

OLD="ghcr.io/getklai/librechat@sha256:$(printf '1%.0s' {1..64})"
NEW="ghcr.io/getklai/librechat@sha256:$(printf '2%.0s' {1..64})"
URL="https://e2e.example.com"

# $FX/images: "<slug> <image>" per tenant. recreate-fails-<slug|all>: that
# recreate fails once (for all: after the tenants before acme moved).
# recreate-ignores-<slug>: the recreate "succeeds" but the image stays, which
# is what an env override or a compose-managed container looks like.
# recreate-sticks-<slug>: the first recreate moves it, later ones do not.
cat > "$tmp/bin/fakehost" <<'STUB'
#!/usr/bin/env bash
op="$1"; arg="${2:-}"
want() {
  jq -r --arg s "$1" 'if .phase == "fleet" or (.phase == "canary" and .canary == $s) then .candidate else .stable end' "$FX/state"
}
move() {
  [ -e "$FX/recreate-ignores-$1" ] && return
  if [ -e "$FX/recreate-sticks-$1" ]; then [ -e "$FX/moved-$1" ] && return; touch "$FX/moved-$1"; fi
  awk -v s="$1" -v i="$(want "$1")" '$1 == s { $2 = i } { print }' "$FX/images" > "$FX/images.new" && mv "$FX/images.new" "$FX/images"
}
case "$op" in
  read-state) [ -f "$FX/state" ] && cat "$FX/state"; exit 0 ;;
  write-state) cat > "$FX/state"; echo "write $(jq -r .phase "$FX/state")" >> "$FX/calls" ;;
  ensure-baseline)
    [ -f "$FX/state" ] && exit 0
    n="$(awk '{ print $2 }' "$FX/images" | sort -u | wc -l | tr -d ' ')"
    [ "$n" = 1 ] || { echo "baseline refused" >> "$FX/calls"; exit 1; }
    jq -n --arg s "$(awk '{ print $2; exit }' "$FX/images")" '{phase: "stable", stable: $s, candidate: ""}' > "$FX/state"
    echo "baseline" >> "$FX/calls" ;;
  images) cat "$FX/images" ;;
  canary-check) [ -e "$FX/canary-refused" ] && exit 1; exit 0 ;;
  recreate)
    echo "recreate ${arg:-all}" >> "$FX/calls"
    if [ -n "$arg" ]; then
      if [ -e "$FX/recreate-fails-$arg" ]; then rm "$FX/recreate-fails-$arg"; exit 1; fi
      move "$arg"
    else
      for s in $(awk '{ print $1 }' "$FX/images"); do
        if [ "$s" = acme ] && [ -e "$FX/recreate-fails-all" ]; then rm "$FX/recreate-fails-all"; exit 1; fi
        move "$s"
      done
    fi ;;
esac
STUB

# gh workflow run ... -f reason=<r>   -> a completed run, conclusion from the queue
# gh run list ... --limit 1           -> the newest run id (the floor)
# gh run list ... --event workflow_dispatch / --status completed -> filtered runs
# gh api .../runs/<id>/jobs           -> the job start of that run
cat > "$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
args="$*" expr="" reason=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jq) expr="$2"; shift 2 ;;
    -f) reason="${2#reason=}"; shift 2 ;;
    *) shift ;;
  esac
done
case "$args" in
  "workflow run"*)
    conclusion="$(head -n1 "$FX/e2e")"; sed -i.bak 1d "$FX/e2e"
    echo "e2e $reason -> $conclusion" >> "$FX/calls"
    started="$(date -u -v+5S +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+5 seconds' +%Y-%m-%dT%H:%M:%SZ)"
    [ -e "$FX/job-started-early" ] && started=2000-01-01T00:00:00Z
    # Listed only after the next poll, as a fresh dispatch is on GitHub.
    jq --arg t "E2E prod-tenant: $reason" --arg c "$conclusion" --arg st "$started" \
      '[{databaseId: ((map(.databaseId) | max // 100) + 1), displayTitle: $t, status: "completed", conclusion: $c, started: $st}] + .' \
      "$FX/runs.json" > "$FX/runs.pending" ;;
  *"--limit 1 "*) jq -r "$expr" "$FX/runs.json" ;;
  *"--event workflow_dispatch"*)
    jq -r "$expr" "$FX/runs.json"
    if [ -f "$FX/runs.pending" ]; then mv "$FX/runs.pending" "$FX/runs.json"; fi ;;
  *"--status completed"*) jq -r "$expr" "$FX/baseline.json" ;;
  "api "*"/jobs"*)
    id="${args#*runs/}"; id="${id%%/*}"
    jq --argjson id "$id" '{jobs: [{started_at: (.[] | select(.databaseId == $id) | .started)}]}' "$FX/runs.json" | jq -r "$expr" ;;
  *) echo "gh stub: unexpected $args" >&2; exit 90 ;;
esac
STUB
chmod +x "$tmp/bin/fakehost" "$tmp/bin/gh"

# new_case <name> <e2e conclusions...>: tenants e2e, acme, beta all on OLD.
new_case() {
  FX="$tmp/$1"; shift
  mkdir -p "$FX"
  : > "$FX/calls"
  printf '%s\n' "$@" > "$FX/e2e"
  echo '[]' > "$FX/runs.json"
  echo '[{"conclusion":"success"}]' > "$FX/baseline.json"
  printf 'e2e %s\nacme %s\nbeta %s\n' "$OLD" "$OLD" "$OLD" > "$FX/images"
}
# state <phase> <stable> [candidate] [next]
state() { jq -n --arg p "$1" --arg s "$2" --arg c "${3:-}" --arg n "${4:-}" '{phase: $p, stable: $s, candidate: $c, canary: "e2e", next: $n}' > "$FX/state"; }
on() { awk -v s="$1" '$1 == s { print $2 }' "$FX/images"; }
set_image() { awk -v s="$1" -v i="$2" '$1 == s { $2 = i } { print }' "$FX/images" > "$FX/images.new" && mv "$FX/images.new" "$FX/images"; }

rollout() {
  out=$(env PATH="$tmp/bin:$PATH" FX="$FX" ROLLOUT_HOST="$tmp/bin/fakehost" GITHUB_REPOSITORY=example/repo \
    GITHUB_RUN_ID=7 GITHUB_RUN_ATTEMPT=1 E2E_POLL_SECONDS=0 E2E_WAIT_SECONDS=5 "$@" bash "$driver" "${TARGET:-$NEW}" "$URL" 2>&1)
  rc=$?
}
calls() { grep -v '^e2e ' "$FX/calls" | tr '\n' ';'; }
# shellcheck disable=SC2001  # indenting a multi-line value
check() {
  local desc="$1"; shift
  if "$@"; then echo "ok   - $desc"; else
    echo "FAIL - $desc"; sed 's/^/       out: /' <<< "$out"; sed 's/^/       call: /' "$FX/calls"; failures=$((failures + 1)); fi
}
has() { grep -qE -- "$1" "$FX/calls"; }
lacks() { ! grep -qE -- "$1" "$FX/calls"; }
st() { jq -r "$1" "$FX/state"; }
all_on() { test "$(awk '{ print $2 }' "$FX/images" | sort -u)" = "$1"; }

echo "# green canary and green fleet: the target becomes stable"
new_case green success success
rollout
check "exit 0" test "$rc" = 0
check "baseline first, canary, fleet; each state before its move" \
  test "$(calls)" = "baseline;write canary;recreate e2e;write fleet;recreate all;write stable;"
check "every tenant runs the target" all_on "$NEW"
check "the target is now the stable image" test "$(st .stable)" = "$NEW"

echo "# no state file: the baseline is written before anything else, also in plan mode"
new_case plan_baseline
rollout ROLLOUT_PLAN_ONLY=true
check "plan: move=true, baseline written, nothing recreated" test "$rc $(calls)" = "0 baseline;"
check "  the baseline is the image the fleet runs, not the new pin" test "$(st .phase) $(st .stable)" = "stable $OLD"
new_case mixed_fleet
set_image beta "$NEW"
rollout
check "a fleet on two images and no state: exit 4, nothing moves" test "$rc $(calls)" = "4 baseline refused;"

echo "# red canary, confirmed: the fleet is never touched"
new_case red_canary failure failure
rollout
check "exit 1" test "$rc" = 1
check "no fleet step at all" lacks '^(write fleet|recreate all)$'
check "reverting, put back, read back, then rejected" \
  test "$(calls)" = "baseline;write canary;recreate e2e;write reverting;recreate e2e;write rejected;"
check "every tenant back on the previous image" all_on "$OLD"
check "the candidate is remembered as rejected" test "$(st .phase) $(st .stable) $(st .candidate)" = "rejected $OLD $NEW"

echo "# one red canary run that the confirmation does not repeat is not a rejection"
new_case flaky_canary failure success success
rollout
check "exit 0, fleet moved" test "$rc $(on beta)" = "0 $NEW"

echo "# the canary recreate reports success but the image did not move: no journeys"
new_case canary_did_not_move success success
touch "$FX/recreate-ignores-e2e"
rollout
check "put back before any E2E run" test "$rc" = 1
check "  no E2E dispatched" lacks '^e2e '
check "  no fleet step" lacks '^recreate all$'

echo "# a fleet recreate that reports success while a tenant did not move: no fleet journeys"
new_case fleet_did_not_move success success
touch "$FX/recreate-ignores-beta"
rollout
check "put back before the fleet E2E run, exit 1" test "$rc $(st .phase)" = "1 rejected"
check "  no fleet journeys, only the canary's and the put-back's" lacks ', fleet, attempt'
check "  every tenant back on the previous image" all_on "$OLD"

echo "# the canary is refused up front (override, not synthetic, compose-managed)"
new_case refused
touch "$FX/canary-refused"
rollout
check "exit 4 before any state change or move" test "$rc $(calls)" = "4 baseline;"

echo "# red fleet, confirmed: every tenant goes back, then E2E proves the put-back"
new_case red_fleet success failure failure success
rollout
check "exit 1" test "$rc" = 1
check "reverting, each tenant put back and read back, then rejected" \
  test "$(calls)" = "baseline;write canary;recreate e2e;write fleet;recreate all;write reverting;recreate e2e;recreate acme;recreate beta;write rejected;"
check "every tenant back on the previous image" all_on "$OLD"
check "an E2E run after the put-back" has 'after fleet rollback'

echo "# a put-back that does not take: state stays reverting, a person has to look"
new_case put_back_fails failure failure
touch "$FX/recreate-sticks-e2e"
rollout
check "exit 4" test "$rc" = 4
check "  never written as rejected" test "$(st .phase)" = reverting

echo "# runner died during the canary put-back: the next run repairs, then rejects"
new_case died_in_canary_put_back
state reverting "$OLD" "$NEW" rejected
set_image e2e "$NEW"
rollout
check "canary recreated back, then rejected; exit 1" test "$rc $(calls)" = "1 recreate e2e;write rejected;"
check "  canary on the previous image" test "$(on e2e)" = "$OLD"

echo "# runner died during the fleet put-back: only the tenants still on the candidate move"
new_case died_in_fleet_put_back
state reverting "$OLD" "$NEW" rejected
set_image acme "$NEW"; set_image beta "$NEW"
rollout
check "acme and beta recreated, then rejected" test "$rc $(calls)" = "1 recreate acme;recreate beta;write rejected;"
check "  every tenant on the previous image" all_on "$OLD"

echo "# runner died during the fleet move: the rest moves, then the fleet proof"
new_case died_in_fleet_move success
state fleet "$OLD" "$NEW"
set_image e2e "$NEW"; set_image acme "$NEW"
rollout
check "beta moved, proof, stable" test "$rc $(calls)" = "0 recreate beta;write stable;"
check "  every tenant on the target" all_on "$NEW"

echo "# rejected, the pin still equals the rejected candidate, one tenant left on it"
new_case rejected_stranded
state rejected "$OLD" "$NEW"
set_image beta "$NEW"
rollout
check "beta put back, nothing else; exit 3" test "$rc $(calls)" = "3 recreate beta;"
new_case rejected_clean
state rejected "$OLD" "$NEW"
rollout
check "nothing stranded: exit 3, nothing moved" test "$rc $(calls)" = "3 "

echo "# a run that died in the canary step: the canary goes back before a fresh canary"
new_case died_in_canary success success
state canary "$OLD" "$NEW"
set_image e2e "$NEW"
rollout
check "reverting to stable, then the sequence" \
  test "$rc $(calls)" = "0 write reverting;recreate e2e;write stable;write canary;recreate e2e;write fleet;recreate all;write stable;"

echo "# an older completed run with the same title is not this dispatch's proof"
new_case stale_title failure failure
jq -n --arg t "E2E prod-tenant: chat image rollout run 7.1, canary, attempt 1" \
  '[{databaseId: 50, displayTitle: $t, status: "completed", conclusion: "success", started: "2999-01-01T00:00:00Z"}]' > "$FX/runs.json"
rollout
check "the red runs dispatched now count, the old green one does not: exit 1" test "$rc" = 1
check "  no fleet step" lacks '^recreate all$'

echo "# a run whose job started before the move proves nothing"
new_case early_job success success
touch "$FX/job-started-early"
rollout
check "taken as no proof: put back, exit 1" test "$rc $(st .phase)" = "1 rejected"
check "  no fleet step" lacks '^recreate all$'

echo "# E2E is already red for another reason: nothing moves"
new_case baseline_red
state stable "$OLD"
echo '[{"conclusion":"cancelled"},{"conclusion":"failure"},{"conclusion":"success"}]' > "$FX/baseline.json"
rollout
check "exit 3, no write, no recreate" test "$rc $(calls)" = "3 "

echo "# the target already is stable: nothing to do, unless forced (the no-op exercise)"
new_case noop
state stable "$NEW"
printf 'e2e %s\nacme %s\nbeta %s\n' "$NEW" "$NEW" "$NEW" > "$FX/images"
rollout
check "exit 0 without touching anything" test "$rc $(calls)" = "0 "
new_case forced success success
state stable "$OLD"
rollout ROLLOUT_FORCE=true TARGET="$OLD"
check "forced: the full sequence on the current image" \
  test "$rc $(calls)" = "0 write canary;recreate e2e;write fleet;recreate all;write stable;"

echo "# a pending E2E run cancelled by the concurrency group is started again"
new_case cancelled cancelled success success
rollout
check "exit 0" test "$rc" = 0
check "the retry is a separate run" has 'canary, attempt 2 -> success'

echo "# a tag instead of a digest is refused before anything happens"
new_case tag
TARGET="ghcr.io/getklai/librechat:v0.8.9" rollout
check "exit 2, no calls" test "$rc $(calls)" = "2 "

echo "# host ops"
H="$tmp/host"
mkdir -p "$H/scripts" "$H/klai/librechat/e2e" "$H/klai/librechat/getklai"
cp "$hostops" "$H/scripts/"
cat > "$H/scripts/tenant-config-regenerate.sh" <<'STUB'
echo "regenerate RECREATE_CONTAINERS=$RECREATE_CONTAINERS TENANT=$TENANT"
STUB
cat > "$tmp/bin/docker" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  ps) cat "$FX/ps" ;;
  compose) cat "$FX/overrides" 2>/dev/null || exit 1 ;;
  inspect) cat "$FX/inspect-${*: -1}" 2>/dev/null || exit 1 ;;
esac
STUB
chmod +x "$tmp/bin/docker"
FX="$tmp/hostfx"; mkdir -p "$FX"; : > "$FX/calls"
STATE_FILE="$H/klai/librechat/image-rollout.json"
hostop() { out=$(env PATH="$tmp/bin:$PATH" FX="$FX" KLAI_COMPOSE_DIR="$H/klai" bash "$H/scripts/chat-image-rollout-host.sh" "$@" 2>&1); rc=$?; }

hostop write-state <<< "{\"phase\":\"canary\",\"stable\":\"$OLD\",\"candidate\":\"$NEW\"}"
check "write-state writes a valid state" test "$rc $(jq -r .phase "$STATE_FILE")" = "0 canary"
check "  readable by portal-api" test "$(stat -c %a "$STATE_FILE" 2>/dev/null || stat -f %Lp "$STATE_FILE")" = 644
hostop write-state <<< '{"phase":"halfway"}'
check "an unknown phase is refused and the old state kept" test "$rc $(jq -r .phase "$STATE_FILE")" = "1 canary"
hostop write-state <<< "{\"phase\":\"canary\",\"stable\":\"$OLD\",\"candidate\":\"ghcr.io/getklai/librechat:v0.8.9\"}"
check "a tag as candidate is refused" test "$rc $(jq -r .candidate "$STATE_FILE")" = "1 $NEW"

rm -f "$STATE_FILE"
printf 'e2e %s\nacme %s\n' "$OLD" "$NEW" > "$FX/ps"
hostop ensure-baseline
check "ensure-baseline refuses a fleet on two images" test "$rc $(test -f "$STATE_FILE" && echo written)" = "1 "
printf 'e2e %s\nacme %s\n' "$OLD" "$OLD" > "$FX/ps"
hostop ensure-baseline
check "ensure-baseline writes the one image the fleet runs" test "$rc $(jq -r '.phase + " " + .stable' "$STATE_FILE")" = "0 stable $OLD"
hostop write-state <<< "{\"phase\":\"fleet\",\"stable\":\"$OLD\",\"candidate\":\"$NEW\"}"
hostop ensure-baseline
check "ensure-baseline leaves an existing state alone" test "$rc $(jq -r .phase "$STATE_FILE")" = "0 fleet"

echo "portal-api-provisioning e2e" > "$FX/inspect-librechat-e2e"
hostop canary-check e2e
check "a tenant not marked synthetic is refused" test "$rc" = 1
echo "KLAI_TENANT_PURPOSE=synthetic" > "$H/klai/librechat/e2e/.env"
hostop canary-check e2e
check "a synthetic provisioning-managed tenant without override passes" test "$rc" = 0
echo "acme=$NEW, e2e=$NEW" > "$FX/overrides"
hostop canary-check e2e
check "an env override for the canary is refused" test "$rc" = 1
rm "$FX/overrides"
echo " getklai" > "$FX/inspect-librechat-getklai"
echo "KLAI_TENANT_PURPOSE=synthetic" > "$H/klai/librechat/getklai/.env"
hostop canary-check getklai
check "a compose-managed container cannot be the canary" test "$rc" = 1
hostop canary-check 'e2e;rm'
check "a slug with shell characters is refused" test "$rc" = 1

hostop recreate e2e
check "recreate <slug> narrows the regenerate to that tenant" test "$out" = "regenerate RECREATE_CONTAINERS=true TENANT=e2e"
hostop recreate
check "recreate without a slug recreates every tenant" test "$out" = "regenerate RECREATE_CONTAINERS=true TENANT="

if (( failures > 0 )); then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all checks passed"
