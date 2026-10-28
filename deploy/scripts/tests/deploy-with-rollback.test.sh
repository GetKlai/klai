#!/usr/bin/env bash
# Tests for deploy-with-rollback.sh (SPEC-INFRA-AUTO-ROLLBACK-001 AT-a1, a2, a5).
#
# No Docker daemon and no host paths: the script takes its docker binary,
# compose-up.sh, ledger, report directory and event log from KLAI_* seams, so
# each case drives a stub docker and a stub compose-up.sh.
#
# Run: bash deploy/scripts/tests/deploy-with-rollback.test.sh

set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/deploy-with-rollback.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0
fx=""

cat > "$work/docker" <<'STUB'
#!/usr/bin/env bash
# Answers only what deploy-with-rollback.sh asks; anything else is a hard error.
fx="$FIXTURES"
printf '%s\n' "$*" >> "$fx/docker_argv"
# Pop the next line of a sequence file; the last line repeats.
pop() {
    local f="$fx/$1" n line
    n=$(cat "$f.n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$f.n"
    line=$(sed -n "${n}p" "$f"); [[ -n "$line" ]] || line=$(tail -n1 "$f")
    printf '%s' "$line"
}
case "$1" in
  compose)
    [[ "$2" == "ps" ]] || { echo "stub: unexpected compose args: $*" >&2; exit 90; }
    # Real compose finds the stack from the working directory; the ssh step
    # starts in the deploy user's home, so the script has to cd on its own.
    [[ "$(pwd -P)" == "$(cd "$KLAI_COMPOSE_DIR" && pwd -P)" ]] \
        || { echo "stub: compose ps outside $KLAI_COMPOSE_DIR (cwd $PWD)" >&2; exit 1; }
    [[ -f "$fx/running_cid" ]] && cat "$fx/running_cid"; exit 0 ;;
  inspect) echo "sha256:0123456789abcdef0123456789abcdef"; exit 0 ;;
  tag|pull) exit 0 ;;
  image) exit 0 ;;
  ps) echo "failedcontainer1"; exit 0 ;;
  logs) echo "container log line: boom"; exit 0 ;;
  run) c=$(pop probe_codes); printf '%s' "$c"; [[ "$c" =~ ^2 ]] || exit 22; exit 0 ;;
esac
echo "stub: unexpected docker args: $*" >&2; exit 90
STUB
cat > "$work/compose-up.sh" <<'STUB'
#!/usr/bin/env bash
fx="$FIXTURES"
printf '%s\n' "$*" >> "$fx/compose_up_argv"
n=$(wc -l < "$fx/compose_up_argv")
rc=$(sed -n "${n}p" "$fx/compose_up_rcs"); [[ -n "$rc" ]] || rc=$(tail -n1 "$fx/compose_up_rcs")
echo "compose-up stub call $n rc=$rc"
exit "$rc"
STUB
chmod +x "$work/docker" "$work/compose-up.sh"

# The script needs flock(1). Linux has it; elsewhere emulate `flock -w N 9` on
# the inherited descriptor so the lock cases still run.
if ! command -v flock >/dev/null 2>&1; then
    mkdir -p "$work/bin"
    cat > "$work/bin/flock" <<'STUB'
#!/usr/bin/env python3
import fcntl, sys, time
wait = float(sys.argv[2]) if sys.argv[1] == "-w" else 0
fd = int(sys.argv[-1])
end = time.time() + wait
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.exit(0)
    except OSError:
        if time.time() >= end:
            sys.exit(1)
        time.sleep(0.05)
STUB
    chmod +x "$work/bin/flock"
    export PATH="$work/bin:$PATH"
fi

# new_case <name> <compose-up rcs, space separated> <probe codes, space separated>
new_case() {
    fx="$work/$1"; mkdir -p "$fx"
    export FIXTURES="$fx"
    printf '%s\n' $2 > "$fx/compose_up_rcs"
    printf '%s\n' ${3:-200} > "$fx/probe_codes"
    mkdir -p "$fx/opt"
    echo "oldcontainer1" > "$fx/running_cid"
    : > "$fx/docker_argv"; : > "$fx/compose_up_argv"
}

# run_script [extra args]; sets rc, out, and the path variables below.
run_script() {
    out=$(cd "$work" && KLAI_COMPOSE_DIR="$fx/opt" KLAI_DEPLOY_LOCK_DIR="$fx" KLAI_DEPLOY_LOCK_WAIT="${LOCK_WAIT:-5}" KLAI_DOCKER_BIN="$work/docker" KLAI_COMPOSE_UP="$work/compose-up.sh" \
          KLAI_PROBE_TIMEOUT=0 KLAI_PROBE_INTERVAL=0 GITHUB_SHA=abc123 \
          bash "$target" retrieval-api ghcr.io/getklai/retrieval-api:abc123 "$@" 2>"$fx/stderr")
    rc=$?
    ledger="$fx/opt/deploy-ledger.log"
    report="$fx/opt/deploy-reports/retrieval-api.json"
    event="$fx/opt/logs/auto-rollback.log"
}

check() {
    local desc="$1"; shift
    if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc"; failures=$((failures + 1)); fi
}
ledger_outcome() { awk -F'\t' 'END { print $7 }' "$ledger"; }
count_lines() { wc -l < "$1" | tr -d ' '; }
has_line() { grep -qxF -- "$2" "$1"; }

echo "# green path"
new_case green "0" "200"
run_script --probe http://retrieval-api:8040/health --compose-tag klai/retrieval-api:local
check "exit 0" test "$rc" = 0
check "ledger outcome deployed" test "$(ledger_outcome)" = deployed
check "ledger carries commit and kind" grep -q $'\timage\tretrieval-api\t0123456789ab\tghcr.io/getklai/retrieval-api:abc123\tabc123\tdeployed$' "$ledger"
check "report outcome deployed" grep -q '"outcome":"deployed"' "$report"
check "report line printed on stdout" grep -q '^ROLLBACK_REPORT {' <<< "$out"
check "no event for a green deploy" test ! -e "$event"
check "previous kept" has_line "$fx/docker_argv" "tag sha256:0123456789abcdef0123456789abcdef klai/retrieval-api:previous"
check "exact sha pulled, tagged as compose tag" has_line "$fx/docker_argv" "tag ghcr.io/getklai/retrieval-api:abc123 klai/retrieval-api:local"
check "compose-up runs without a registry pull" has_line "$fx/compose_up_argv" "--no-pull retrieval-api"
check "one compose-up call" test "$(count_lines "$fx/compose_up_argv")" = 1
check "ledger file is 0644" test "$(stat -c %a "$ledger" 2>/dev/null || stat -f %Lp "$ledger")" = 644

check "probe runs on the default network" grep -q '^run --rm --network klai-net ' "$fx/docker_argv"

echo "# AT-a1: compose-up red -> previous retagged, exit 3"
new_case a1 "1 0" "200"
run_script --probe http://retrieval-api:8040/health --compose-tag klai/retrieval-api:local
check "exit 3" test "$rc" = 3
check "ledger outcome rolled_back" test "$(ledger_outcome)" = rolled_back
check "previous retagged to the compose tag" has_line "$fx/docker_argv" "tag klai/retrieval-api:previous klai/retrieval-api:local"
check "second compose-up is a forced recreate" has_line "$fx/compose_up_argv" "--no-pull --force-recreate retrieval-api"
check "report gate compose-up, outcome rolled_back" grep -q '"gate":"compose-up".*"outcome":"rolled_back"' "$report"
check "report names the log excerpt and it holds the container log" grep -q 'container log line: boom' "$fx/opt/deploy-reports/retrieval-api.log"
check "auto_rollback event written" grep -q '"event":"auto_rollback","outcome":"rolled_back","deploy_service":"retrieval-api"' "$event"
check "report line printed on stdout" grep -q '^ROLLBACK_REPORT {.*"outcome":"rolled_back"' <<< "$out"

echo "# AT-a2: probe answers 500 -> same"
new_case a2 "0 0" "500 200"
run_script --probe http://retrieval-api:8040/health
check "exit 3" test "$rc" = 3
check "ledger outcome rolled_back" test "$(ledger_outcome)" = rolled_back
check "previous retagged to the default compose tag" has_line "$fx/docker_argv" "tag klai/retrieval-api:previous ghcr.io/getklai/retrieval-api:latest"
check "report gate probe" grep -q '"gate":"probe".*"outcome":"rolled_back"' "$report"
check "probe re-run after the rollback" test "$(grep -c '^run ' "$fx/docker_argv")" = 2

echo "# AT-a5: previous also red -> exit 4, one rollback attempt"
new_case a5 "1 1 1" "200"
run_script
check "exit 4" test "$rc" = 4
check "ledger outcome rollback_failed" test "$(ledger_outcome)" = rollback_failed
check "exactly one rollback attempt (two compose-up calls)" test "$(count_lines "$fx/compose_up_argv")" = 2
check "report outcome rollback_failed" grep -q '"outcome":"rollback_failed"' "$report"
check "event outcome rollback_failed" grep -q '"outcome":"rollback_failed"' "$event"

echo "# AT-a5 variant: rollback comes up but its probe is red"
new_case a5p "0 0 0" "500"
run_script --probe http://retrieval-api:8040/health
check "exit 4" test "$rc" = 4
check "gate names the rollback probe" grep -q '"gate":"rollback-probe"' "$report"
check "two compose-up calls only" test "$(count_lines "$fx/compose_up_argv")" = 2

echo "# no previous container"
new_case first "1 0" "200"
rm "$fx/running_cid"
run_script
check "exit 4" test "$rc" = 4
check "gate no-previous-image" grep -q '"gate":"no-previous-image"' "$report"
check "ledger from is none" grep -q $'\tretrieval-api\tnone\t' "$ledger"
check "no rollback attempted" test "$(count_lines "$fx/compose_up_argv")" = 1

echo "# probe network option"
new_case net "0" "200"
run_script --probe http://klai-docker-authz:2375/health --probe-network klai-socket-proxy
check "exit 0" test "$rc" = 0
check "probe runs on the named network" grep -q '^run --rm --network klai-socket-proxy ' "$fx/docker_argv"

echo "# per-service lock"
new_case lockwait "0" "200"
( exec 9>"$fx/klai-deploy-retrieval-api.lock"; flock -w 5 9; sleep 1 ) &
holder=$!
sleep 0.3
run_script
wait "$holder"
check "a second deploy waits for the first, then runs" test "$rc" = 0
new_case locktimeout "0" "200"
( exec 9>"$fx/klai-deploy-retrieval-api.lock"; flock -w 5 9; sleep 3 ) &
holder=$!
sleep 0.3
LOCK_WAIT=1 run_script
wait "$holder"
check "exit 1 when the lock stays held" test "$rc" = 1
check "says why" grep -q 'another deploy of retrieval-api' "$fx/stderr"
check "nothing was touched" test ! -s "$fx/docker_argv"

if (( failures > 0 )); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
