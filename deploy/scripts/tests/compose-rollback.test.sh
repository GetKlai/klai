#!/usr/bin/env bash
# Tests for compose-rollback.sh, the helper deploy-compose.yml sources to put the
# previous compose and config files back when a gate goes red.
#
# No Docker needed (rsync is, for the directory restore): compose-up.sh, compose-env-drift-recreate.sh and
# check-twenty-upgrade.sh are stubs in each case's scripts/ directory. The case
# driver replays the shape of the workflow: keep, overwrite, gate.
#
# Run: bash deploy/scripts/tests/compose-rollback.test.sh

set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
helper="$script_dir/compose-rollback.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0
case_dir=""
rc=0

OLD_COMPOSE=$'services:\n  caddy:\n    image: caddy:2.9\n  crm:\n    image: twentycrm/twenty:v2.51.0\n'

# new_case <name> <new compose body>: a host in the old state, a stub per script.
# The sweep is red while the compose file contains the word BAD and, when
# sweep_always_red exists, always. Every call is recorded in fixtures/calls.
new_case() {
    case_dir="$work/$1"
    mkdir -p "$case_dir/scripts" "$case_dir/caddy" "$case_dir/alloy" "$case_dir/fixtures" "$case_dir/grafana/provisioning"
    : > "$case_dir/fixtures/calls"
    printf '%s' "$OLD_COMPOSE" > "$case_dir/docker-compose.yml"
    echo "old caddyfile" > "$case_dir/caddy/Caddyfile"
    echo "old alloy" > "$case_dir/alloy/config.alloy"
    echo "old rule" > "$case_dir/grafana/provisioning/rule.yaml"
    echo "aaa111" > "$case_dir/.compose-commit"
    cat > "$case_dir/scripts/compose-env-drift-recreate.sh" <<'STUB'
#!/usr/bin/env bash
echo "sweep $(tr '\n' ' ' < "$KLAI_COMPOSE_DIR/docker-compose.yml" | cut -c1-60)" >> "$KLAI_COMPOSE_DIR/fixtures/calls"
[[ -e "$KLAI_COMPOSE_DIR/fixtures/sweep_always_red" ]] && exit 1
grep -q BAD "$KLAI_COMPOSE_DIR/docker-compose.yml" && exit 1
exit 0
STUB
    cat > "$case_dir/scripts/compose-up.sh" <<'STUB'
#!/usr/bin/env bash
echo "compose-up $*" >> "$KLAI_COMPOSE_DIR/fixtures/calls"
STUB
    cat > "$case_dir/scripts/check-twenty-upgrade.sh" <<'STUB'
#!/usr/bin/env bash
echo "twenty-check" >> "$KLAI_COMPOSE_DIR/fixtures/calls"
n=$(grep -c '^twenty-check' "$KLAI_COMPOSE_DIR/fixtures/calls")
[[ -e "$KLAI_COMPOSE_DIR/fixtures/twenty_red_first" && "$n" -eq 1 ]] && exit 1
[[ -e "$KLAI_COMPOSE_DIR/fixtures/twenty_always_red" ]] && exit 1
exit 0
STUB
    chmod +x "$case_dir"/scripts/*.sh
}

# run_driver <gate lines...>: the workflow in miniature. Overwrites the kept
# files with the new state, then runs the given gate lines. REACHED_END is
# printed only when no gate ended the run.
run_driver() {
    local new_compose="$1" gates="$2"
    cat > "$case_dir/driver.sh" <<DRIVER
set -e
source "$helper"
rb_init bbb222
rb_keep "\$KLAI_COMPOSE_DIR/docker-compose.yml" "\$KLAI_COMPOSE_DIR/caddy/Caddyfile" "\$KLAI_COMPOSE_DIR/grafana/provisioning"
printf '%s' "$new_compose" > "\$KLAI_COMPOSE_DIR/docker-compose.yml"
echo "new caddyfile" > "\$KLAI_COMPOSE_DIR/caddy/Caddyfile"
echo "new rule" > "\$KLAI_COMPOSE_DIR/grafana/provisioning/rule.yaml"
echo "added rule" > "\$KLAI_COMPOSE_DIR/grafana/provisioning/added.yaml"
rb_touched caddy
red() { echo "red: \$*"; return 1; }
$gates
rb_deployed
echo REACHED_END
DRIVER
    set +e
    KLAI_COMPOSE_DIR="$case_dir" bash "$case_dir/driver.sh" > "$case_dir/out" 2>&1
    rc=$?
    set -e
}

fail() {
    echo "  FAIL $1"
    sed 's/^/       /' "$case_dir/out"
    echo "       --- calls ---"
    sed 's/^/       /' "$case_dir/fixtures/calls"
    failures=$((failures + 1))
}
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else fail "$1 (expected '$2', got '$3')"; fi; }
file_is() { check "$1" "$2" "$(cat "$case_dir/$3")"; }
count_calls() { grep -c -- "$1" "$case_dir/fixtures/calls" || true; }
ledger_last() { tail -n1 "$case_dir/deploy-ledger.log" | cut -f2-; }
report() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$case_dir/deploy-reports/compose.json" "$1"; }
reaches_end() { if grep -q REACHED_END "$case_dir/out"; then echo yes; else echo no; fi; }

NEW_COMPOSE=$'services:\n  caddy:\n    image: caddy:2.10\n  crm:\n    image: twentycrm/twenty:v2.51.0\n'
SWEEP=compose-env-drift-recreate.sh

echo "compose-rollback.sh"

# Green: nothing is restored, the ledger says deployed, the marker moves on.
new_case green
run_driver "$NEW_COMPOSE" 'rb_gate compose compose-up-verification "$KLAI_COMPOSE_DIR/scripts/'$SWEEP'"'
check "green sweep -> exit 0" 0 "$rc"
check "  ran to the end" yes "$(reaches_end)"
file_is "  new Caddyfile stays" "new caddyfile" caddy/Caddyfile
check "  nothing recreated" 0 "$(count_calls '^compose-up')"
check "  ledger" $'compose\tcompose\taaa111\tbbb222\tbbb222\tdeployed' "$(ledger_last)"
check "  report outcome" deployed "$(report outcome)"
file_is "  marker moves to the new commit" bbb222 .compose-commit

# AT-b1: a red recreate puts the .prev files back, recreates and verifies again.
new_case at_b1
run_driver "${NEW_COMPOSE}  bad: BAD"$'\n' 'rb_gate compose compose-up-verification "$KLAI_COMPOSE_DIR/scripts/'$SWEEP'"'
check "red sweep, previous files green -> exit 3" 3 "$rc"
check "  job does not run past the red gate" no "$(reaches_end)"
file_is "  compose file restored" "$(printf '%s' "$OLD_COMPOSE")" docker-compose.yml
file_is "  Caddyfile restored" "old caddyfile" caddy/Caddyfile
file_is "  grafana file restored" "old rule" grafana/provisioning/rule.yaml
check "  grafana file added by the sync removed" no "$([[ -e "$case_dir/grafana/provisioning/added.yaml" ]] && echo yes || echo no)"
check "  config service recreated through compose-up.sh" "compose-up --no-pull --force-recreate caddy" "$(grep '^compose-up' "$case_dir/fixtures/calls")"
check "  sweep ran twice (failed, then verified on the restored files)" 2 "$(count_calls '^sweep')"
check "  ledger" $'compose\tcompose\taaa111\tbbb222\tbbb222\trolled_back' "$(ledger_last)"
for key in service kind from to commit gate outcome ts log; do
    report "$key" > /dev/null || fail "  report lacks key $key"
done
check "  report gate" compose-up-verification "$(report gate)"
check "  report outcome" rolled_back "$(report outcome)"
check "  ROLLBACK_REPORT printed" 1 "$(grep -c '^ROLLBACK_REPORT {' "$case_dir/out")"
check "  auto_rollback event logged" 1 "$(grep -c '"event":"auto_rollback","outcome":"rolled_back"' "$case_dir/logs/auto-rollback.log")"
file_is "  marker keeps the previous commit" aaa111 .compose-commit

# A red sync_and_recreate names its own service in the ledger.
new_case sync_red
run_driver "$NEW_COMPOSE" 'rb_gate caddy sync-recreate red caddy'
check "red sync gate -> exit 3" 3 "$rc"
check "  ledger names the service" $'compose\tcaddy\taaa111\tbbb222\tbbb222\trolled_back' "$(ledger_last)"

# Only this run's copies are restored: a .prev from an older deploy, for a file
# this run never reached, must not overwrite the file that is still current.
new_case stale_prev
echo "stale from an earlier deploy" > "$case_dir/alloy/config.alloy.prev"
run_driver "${NEW_COMPOSE}  bad: BAD"$'\n' 'rb_gate compose compose-up-verification "$KLAI_COMPOSE_DIR/scripts/'$SWEEP'"'
check "red sweep -> exit 3" 3 "$rc"
file_is "  untouched file keeps its current content" "old alloy" alloy/config.alloy

# Twenty gate with an unchanged image: the normal rollback, and the check runs
# again on the restored state.
new_case twenty_unchanged
: > "$case_dir/fixtures/twenty_red_first"
run_driver "$NEW_COMPOSE" 'rb_gate crm twenty-upgrade --reverify "$KLAI_COMPOSE_DIR/scripts/check-twenty-upgrade.sh"'
check "red twenty check, image unchanged -> exit 3" 3 "$rc"
check "  check ran again after the restore" 2 "$(count_calls '^twenty-check')"
file_is "  Caddyfile restored" "old caddyfile" caddy/Caddyfile

# AT-b3: an image that migrates its own database changed in this sync. The new
# files stay, nothing is recreated, the run reports rollback_failed.
new_case at_b3
: > "$case_dir/fixtures/twenty_always_red"
run_driver "${NEW_COMPOSE//v2.51.0/v2.52.0}" 'rb_gate crm twenty-upgrade --reverify "$KLAI_COMPOSE_DIR/scripts/check-twenty-upgrade.sh"'
check "red gate, Twenty image changed -> exit 4" 4 "$rc"
file_is "  new Caddyfile left in place" "new caddyfile" caddy/Caddyfile
check "  new compose file left in place" 1 "$(grep -c 'v2.52.0' "$case_dir/docker-compose.yml")"
check "  nothing recreated" 0 "$(count_calls '^compose-up')"
check "  check not repeated" 1 "$(count_calls '^twenty-check')"
check "  report gate" self-migrating-image "$(report gate)"
check "  report outcome" rollback_failed "$(report outcome)"
check "  ledger" $'compose\tcrm\taaa111\tbbb222\tbbb222\trollback_failed' "$(ledger_last)"
check "  auto_rollback event logged" 1 "$(grep -c '"event":"auto_rollback","outcome":"rollback_failed"' "$case_dir/logs/auto-rollback.log")"
file_is "  marker keeps the previous commit" aaa111 .compose-commit

# R6: when the restored state is red as well there is no second round.
new_case one_attempt
: > "$case_dir/fixtures/sweep_always_red"
run_driver "$NEW_COMPOSE" 'rb_gate compose compose-up-verification "$KLAI_COMPOSE_DIR/scripts/'$SWEEP'"'
check "restored state red too -> exit 4" 4 "$rc"
file_is "  files were restored before the verdict" "old caddyfile" caddy/Caddyfile
check "  sweep ran exactly twice, never a third time" 2 "$(count_calls '^sweep')"
check "  report gate is the original gate" compose-up-verification "$(report gate)"
check "  report outcome" rollback_failed "$(report outcome)"

echo
if [[ "$failures" -eq 0 ]]; then
    echo "compose-rollback: all cases passed"
else
    echo "compose-rollback: $failures case(s) failed"
    exit 1
fi
