#!/usr/bin/env bash
# Tests for rollback-rehearsal-assert.sh (SPEC-INFRA-AUTO-ROLLBACK-001 AT-f2).
#
# No Docker daemon and no host paths: the script takes its docker binary, the
# deploy script and the event log from KLAI_* seams, so each case drives a stub
# deploy-with-rollback.sh (one exit code and report line per call) and a stub
# docker that says which image the canary container runs.
#
# Run: bash deploy/scripts/tests/rollback-rehearsal-assert.test.sh

set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/rollback-rehearsal-assert.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0
fx=""

cat > "$work/docker" <<'STUB'
#!/usr/bin/env bash
fx="$FIXTURES"
case "$1" in
  image) echo "sha256:goodimage"; exit 0 ;;
  compose) [[ "$2" == "ps" ]] || exit 90; echo "canarycid"; exit 0 ;;
  inspect) cat "$fx/running_image"; exit 0 ;;
  run) c=$(cat "$fx/probe_code"); printf '%s' "$c"; [[ "$c" =~ ^2 ]] || exit 22; exit 0 ;;
esac
echo "stub: unexpected docker args: $*" >&2; exit 90
STUB
cat > "$work/deploy.sh" <<'STUB'
#!/usr/bin/env bash
# One line of $FIXTURES/deploy_calls per call: "<exit code> <outcome or ->".
fx="$FIXTURES"
printf '%s COMPOSE_PROFILES=%s\n' "$*" "${COMPOSE_PROFILES:-}" >> "$fx/deploy_argv"
n=$(wc -l < "$fx/deploy_argv")
read -r rc outcome < <(sed -n "${n}p" "$fx/deploy_calls")
echo "deploy stub call $n rc=$rc"
[[ "$outcome" == "-" ]] || echo "ROLLBACK_REPORT {\"service\":\"rollback-canary\",\"outcome\":\"$outcome\"}"
exit "$rc"
STUB
chmod +x "$work/docker" "$work/deploy.sh"

# new_case <name> <running image id> <probe code>; deploy_calls is filled by the caller.
new_case() {
    fx="$work/$1"; mkdir -p "$fx/opt"
    export FIXTURES="$fx"
    echo "$2" > "$fx/running_image"
    echo "$3" > "$fx/probe_code"
    : > "$fx/deploy_argv"
}

run_script() {
    out=$(KLAI_COMPOSE_DIR="$fx/opt" KLAI_DOCKER_BIN="$work/docker" KLAI_DEPLOY_WITH_ROLLBACK="$work/deploy.sh" \
          KLAI_AUTO_ROLLBACK_LOG="$fx/opt/logs/auto-rollback.log" KLAI_REHEARSAL_WAIT=0 KLAI_REHEARSAL_INTERVAL=0 \
          bash "$target" busybox:1.37.0 debian:12.15-slim 2>&1)
    rc=$?
    event="$fx/opt/logs/auto-rollback.log"
}

check() {
    local desc="$1"; shift
    if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc"; failures=$((failures + 1)); fi
}
calls() { wc -l < "$fx/deploy_argv" | tr -d ' '; }

echo "# good deployed, bad rolled back, canary back on the good image"
new_case ok "sha256:goodimage" 200
printf '%s\n' "0 -" "3 rolled_back" > "$fx/deploy_calls"
run_script
check "exit 0" test "$rc" = 0
check "event line appended" grep -q '"service":"auto-rollback","level":"info","event":"rollback_rehearsal","outcome":"ok"' "$event"
check "event names the canary service, so the auto_rollback alert can exclude it" grep -q '"deploy_service":"rollback-canary"' "$event"
check "one event line" test "$(wc -l < "$event" | tr -d ' ')" = 1
check "good image deployed first, bad second" bash -c "sed -n 1p '$fx/deploy_argv' | grep -q '^rollback-canary busybox:1.37.0 ' && sed -n 2p '$fx/deploy_argv' | grep -q '^rollback-canary debian:12.15-slim '"
check "same compose tag, probe and probe network on both deploys" test "$(grep -c -- '--compose-tag klai/rollback-canary:1.0-local-261028-0000 --probe http://rollback-canary:8080/ --probe-network klai-rehearsal' "$fx/deploy_argv")" = 2
check "compose profile is on for the deploy script" test "$(grep -c 'COMPOSE_PROFILES=rollback-rehearsal$' "$fx/deploy_argv")" = 2

echo "# bad image deploys as healthy: the rollback path did not roll back"
new_case badok "sha256:badimage" 200
printf '%s\n' "0 -" "0 deployed" > "$fx/deploy_calls"
run_script
check "exit 1" test "$rc" = 1
check "says the rollback path did not roll back" grep -q 'the rollback path did not roll back' <<< "$out"
check "no event line" test ! -e "$event"

echo "# rollback itself failed (exit 4)"
new_case rb4 "sha256:badimage" 000
printf '%s\n' "0 -" "4 rollback_failed" > "$fx/deploy_calls"
run_script
check "exit 1" test "$rc" = 1
check "says rollback_failed" grep -q 'rollback_failed' <<< "$out"
check "no event line" test ! -e "$event"

echo "# good deploy red: the bad image is never tried"
new_case goodred "sha256:goodimage" 200
printf '%s\n' "3 rolled_back" > "$fx/deploy_calls"
run_script
check "exit 1" test "$rc" = 1
check "only one deploy call" test "$(calls)" = 1
check "no event line" test ! -e "$event"

echo "# exit 3 without a rolled_back report line"
new_case noreport "sha256:goodimage" 200
printf '%s\n' "0 -" "3 -" > "$fx/deploy_calls"
run_script
check "exit 1" test "$rc" = 1
check "no event line" test ! -e "$event"

echo "# canary still on another image after the rollback"
new_case stuck "sha256:badimage" 200
printf '%s\n' "0 -" "3 rolled_back" > "$fx/deploy_calls"
run_script
check "exit 1" test "$rc" = 1
check "says the canary is not on the good image" grep -q 'does not run the good image' <<< "$out"
check "no event line" test ! -e "$event"

echo "# canary on the good image but the probe answers 500"
new_case probe500 "sha256:goodimage" 500
printf '%s\n' "0 -" "3 rolled_back" > "$fx/deploy_calls"
run_script
check "exit 1" test "$rc" = 1
check "no event line" test ! -e "$event"

echo "# arguments"
out=$(bash "$target" onlyone 2>&1); rc=$?
check "one argument is a usage error" test "$rc" = 1

echo
if (( failures > 0 )); then echo "$failures check(s) failed"; exit 1; fi
echo "all checks passed"
