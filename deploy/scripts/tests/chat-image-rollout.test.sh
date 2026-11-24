#!/usr/bin/env bash
# Tests for chat-image-rollout.sh (the staged rollout's decisions) and
# chat-image-rollout-host.sh (its core-01 ops).
#
# The driver runs for real. ROLLOUT_HOST is a fake core-01 that keeps the state
# file in a directory and records every op; gh is a stub on PATH that answers
# E2E runs from a queue of conclusions and applies the script's own --jq filter
# to them, so the real run selection and polling run too.
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

cat > "$tmp/bin/fakehost" <<'STUB'
#!/usr/bin/env bash
op="$1"; slug="${2:-all}"
case "$op" in
  read-state) [ -f "$FX/state" ] && cat "$FX/state"; exit 0 ;;
  write-state) cat > "$FX/state"; echo "write $(jq -r .phase "$FX/state")" >> "$FX/calls" ;;
  running-image) echo "running-image $slug" >> "$FX/calls"; cat "$FX/running" ;;
  recreate)
    echo "recreate $slug" >> "$FX/calls"
    if [ -e "$FX/recreate-fails-$slug" ]; then rm "$FX/recreate-fails-$slug"; exit 1; fi ;;
esac
STUB

# gh workflow run ... -f reason=<r>     -> appends a completed run whose
#                                          conclusion is the next queue line
# gh run list ... --event workflow_dispatch --jq <e>   -> those runs
# gh run list ... --status completed --jq <e>          -> the baseline runs
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
    jq --arg t "E2E prod-tenant: $reason" --arg c "$conclusion" \
      '[{databaseId: (length + 100), displayTitle: $t, status: "completed", conclusion: $c}] + .' \
      "$FX/runs.json" > "$FX/runs.new" && mv "$FX/runs.new" "$FX/runs.json" ;;
  *"--event workflow_dispatch"*) jq -r "$expr" "$FX/runs.json" ;;
  *"--status completed"*) jq -r "$expr" "$FX/baseline.json" ;;
  *) echo "gh stub: unexpected $args" >&2; exit 90 ;;
esac
STUB
chmod +x "$tmp/bin/fakehost" "$tmp/bin/gh"

# new_case <name> <e2e conclusions...>
new_case() {
  FX="$tmp/$1"; shift
  mkdir -p "$FX"
  : > "$FX/calls"
  printf '%s\n' "$@" > "$FX/e2e"
  echo '[]' > "$FX/runs.json"
  echo '[{"conclusion":"success"}]' > "$FX/baseline.json"
  echo "$OLD" > "$FX/running"
}
state() { jq -n --arg p "$1" --arg s "$2" --arg c "${3:-}" '{phase: $p, stable: $s, candidate: $c, canary: "e2e"}' > "$FX/state"; }

rollout() {
  out=$(env PATH="$tmp/bin:$PATH" FX="$FX" ROLLOUT_HOST="$tmp/bin/fakehost" GITHUB_REPOSITORY=example/repo \
    GITHUB_RUN_ID=7 E2E_POLL_SECONDS=0 E2E_WAIT_SECONDS=5 "$@" bash "$driver" "${TARGET:-$NEW}" "$URL" 2>&1)
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

echo "# green canary and green fleet: the target becomes stable"
new_case green success success
rollout
check "exit 0" test "$rc" = 0
check "canary first, fleet second, state follows each step" \
  test "$(calls)" = "running-image e2e;write canary;recreate e2e;write fleet;recreate all;write stable;"
check "the canary tenant is the first DNS label of the E2E base URL" has '^recreate e2e$'
check "the target is now the stable image" test "$(st .stable)" = "$NEW"

echo "# red canary, confirmed: the fleet is never touched"
new_case red_canary failure failure
rollout
check "exit 1" test "$rc" = 1
check "no fleet step at all" lacks '^(write fleet|recreate all)$'
check "the canary went back after the rejection was written" \
  test "$(calls)" = "running-image e2e;write canary;recreate e2e;write rejected;recreate e2e;"
check "the previous image stays stable, the candidate is remembered as rejected" \
  test "$(st .phase) $(st .stable) $(st .candidate)" = "rejected $OLD $NEW"
check "it ran the canary journeys twice (one confirmation)" test "$(grep -c '^e2e ' "$FX/calls")" = 2

echo "# one red canary run that the confirmation does not repeat is not a rejection"
new_case flaky_canary failure success success
rollout
check "exit 0" test "$rc" = 0
check "the fleet moved" has '^recreate all$'

echo "# the canary fails portal-api's health gate: put back, no journeys run"
new_case canary_unhealthy
touch "$FX/recreate-fails-e2e"
rollout
check "exit 1" test "$rc" = 1
check "no E2E run, no fleet step" lacks '^(e2e |recreate all)'
check "the canary was recreated back" test "$(calls)" = "running-image e2e;write canary;recreate e2e;write rejected;recreate e2e;"

echo "# red fleet, confirmed: every tenant goes back, then E2E proves the put-back"
new_case red_fleet success failure failure success
rollout
check "exit 1" test "$rc" = 1
check "rejected, then the whole fleet recreated back" \
  test "$(calls)" = "running-image e2e;write canary;recreate e2e;write fleet;recreate all;write rejected;recreate all;"
check "stable is still the previous image" test "$(st .stable)" = "$OLD"
check "an E2E run after the put-back" has 'after fleet rollback'

echo "# the put-back itself is red: a person has to look"
new_case red_after_put_back success failure failure failure failure
rollout
check "exit 4" test "$rc" = 4

echo "# E2E is already red for another reason: nothing moves"
new_case baseline_red
echo '[{"conclusion":"cancelled"},{"conclusion":"failure"},{"conclusion":"success"}]' > "$FX/baseline.json"
rollout
check "exit 3" test "$rc" = 3
check "no write, no recreate" lacks '^(write|recreate)'

echo "# the target already is stable: nothing to do, unless forced (the no-op exercise)"
new_case noop
state stable "$NEW"
rollout
check "exit 0 without touching anything" test "$rc $(calls)" = "0 "
new_case forced success success
state stable "$NEW"
rollout ROLLOUT_FORCE=true
check "forced: the full sequence on the current image" \
  test "$rc $(calls)" = "0 write canary;recreate e2e;write fleet;recreate all;write stable;"

echo "# a rejected target is not tried again by itself"
new_case rejected
state rejected "$OLD" "$NEW"
rollout
check "exit 3 (held), nothing moved" test "$rc $(calls)" = "3 "

echo "# plan only: says whether a run would move anything, and moves nothing"
new_case plan_move
rollout ROLLOUT_PLAN_ONLY=true
check "a new target: move=true, nothing written" test "$rc $(calls)" = "0 running-image e2e;"
check "  prints move=true" grep -qx 'move=true' <<< "$out"
new_case plan_none
state stable "$NEW"
rollout ROLLOUT_PLAN_ONLY=true
check "the target is stable: move=false" grep -qx 'move=false' <<< "$out"

echo "# a run that died in the canary step: the canary goes back before a fresh canary"
new_case died_in_canary success success
state canary "$OLD" "$NEW"
rollout
check "back to stable first, then the sequence" \
  test "$rc $(calls)" = "0 write stable;recreate e2e;write canary;recreate e2e;write fleet;recreate all;write stable;"

echo "# a run that died in the fleet step of this target: resume the fleet, no new canary"
new_case died_in_fleet success
state fleet "$OLD" "$NEW"
rollout
check "fleet resumed" test "$rc $(calls)" = "0 recreate all;write stable;"

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
mkdir -p "$H/scripts" "$H/librechat"
cp "$hostops" "$H/scripts/"
cat > "$H/scripts/tenant-config-regenerate.sh" <<'STUB'
echo "regenerate RECREATE_CONTAINERS=$RECREATE_CONTAINERS TENANT=$TENANT"
STUB
cat > "$tmp/bin/docker" <<'STUB'
#!/usr/bin/env bash
cat "$FX/inspect-${*: -1}" 2>/dev/null || exit 1
STUB
chmod +x "$tmp/bin/docker"
FX="$tmp/hostfx"; mkdir -p "$FX"; : > "$FX/calls"
hostop() { out=$(env PATH="$tmp/bin:$PATH" FX="$FX" KLAI_LIBRECHAT_DIR="$H/librechat" bash "$H/scripts/chat-image-rollout-host.sh" "$@" 2>&1); rc=$?; }

hostop write-state <<< '{"phase":"canary","stable":"x"}'
check "write-state writes the file" test "$rc $(jq -r .phase "$H/librechat/image-rollout.json")" = "0 canary"
check "readable by portal-api" test "$(stat -c %a "$H/librechat/image-rollout.json" 2>/dev/null || stat -f %Lp "$H/librechat/image-rollout.json")" = 644
hostop write-state <<< '{"phase":"halfway"}'
check "an unknown phase is refused and the old state kept" test "$rc $(jq -r .phase "$H/librechat/image-rollout.json")" = "1 canary"
hostop read-state
check "read-state prints it" test "$(jq -r .phase <<< "$out")" = canary

echo "portal-api-provisioning e2e $OLD" > "$FX/inspect-librechat-e2e"
hostop running-image e2e
check "running-image prints the container's image" test "$rc $out" = "0 $OLD"
echo " getklai $OLD" > "$FX/inspect-librechat-getklai"
hostop running-image getklai
check "a compose-managed container cannot be the canary" test "$rc" = 1
hostop running-image 'e2e;rm'
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
