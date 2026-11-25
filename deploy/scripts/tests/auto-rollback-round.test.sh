#!/usr/bin/env bash
# Tests for auto-rollback-round.sh: the window end auto-rollback.yml passes as
# --until, and the name of the E2E run it starts after a rollback.
#
# Run: bash deploy/scripts/tests/auto-rollback-round.test.sh

set -uo pipefail

target="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)/auto-rollback-round.sh"
failures=0
check() {
    local desc="$1"; shift
    if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc (out: $out, rc $rc)"; failures=$((failures + 1)); fi
}
round() { out=$(bash "$target" "$@" 2>/dev/null); rc=$?; }

echo "# until"
round until 2026-10-28T07:30:00Z
check "a red run's creation is the window end, not the replay start" test "$out $rc" = "2026-10-28T07:30:00Z 0"
round until ""
check "a rehearsal (no red run) ends the window now" bash -c "[[ '$out' =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$ && '$rc' == 0 ]]"
round until yesterday
check "a value that is not a timestamp fails the step" test -z "$out" -a "$rc" = 1

echo "# reason"
round reason rolled_back 900
check "rolled_back is a clean rollback" test "$out" = "after a clean rollback by auto-rollback run 900"
round reason rolled_back_partial 900
check "rolled_back_partial is a partial rollback" test "$out" = "after a partial rollback by auto-rollback run 900"
for outcome in rollback_failed something-else ""; do
    round reason "$outcome" 900
    check "'$outcome' is a failed rollback" test "$out" = "after a failed rollback by auto-rollback run 900"
done
round reason rolled_back "900; rm -rf /"
check "a run id that is not a number fails" test -z "$out" -a "$rc" = 1

if (( failures > 0 )); then echo "$failures check(s) failed"; exit 1; fi
echo "all checks passed"
