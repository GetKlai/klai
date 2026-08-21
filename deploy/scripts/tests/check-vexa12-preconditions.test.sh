#!/usr/bin/env bash
# SPEC-VEXA-004 — the host-readiness checks must be able to go red.
#
# This suite exists because of what it caught. The script's third check compared
# vexa12-meeting-api's Redis host against the 0.10 stack's; PR #914 deleted that
# stack, `docker inspect` on the gone container returned empty, and "empty !=
# vexa12-redis" is the SUCCESS branch — so the check reported OK for a week and
# could not have reported anything else. `set -eu` does not catch it: the exit
# status of VAR=$(docker inspect … | sed … | head -1) is head's.
#
# So every case below pins a red as well as a green. A check that has never been
# observed failing is indistinguishable from the one that was removed.
#
# Driven by a docker stub on PATH — no daemon required, so it runs in CI.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/check-vexa12-deploy-preconditions.sh"
STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT

# Stub knobs, read from the environment at call time:
#   STUB_IMAGE_PRESENT=yes|no     — answers `docker image inspect`
#   STUB_TABLES=<n>|fail          — answers `docker exec`; `fail` exits non-zero,
#                                   standing in for a missing postgres container
#                                   or an unreachable daemon
export STUB_INSPECT_LOG="$STUB_DIR/inspect-calls"; : > "$STUB_INSPECT_LOG"

cat > "$STUB_DIR/docker" <<'STUB'
#!/bin/sh
case "$1 $2" in
  "image inspect") [ "${STUB_IMAGE_PRESENT:-yes}" = yes ] && exit 0 || exit 1 ;;
esac
case "$1" in
  exec)
    if [ "${STUB_TABLES:-3}" = fail ]; then
      echo "Error: No such container: klai-core-postgres-1" >&2; exit 1
    fi
    echo "${STUB_TABLES:-3}"; exit 0 ;;
  inspect)
    # Nothing in this script may depend on `docker inspect` any more. Record the
    # call in a FILE, not on stderr: the caller may well write `2>/dev/null`,
    # which is exactly how the retired check hid its own failure. A file survives
    # any redirect the script applies.
    echo "$2" >> "$STUB_INSPECT_LOG"; exit 1 ;;
esac
exit 0
STUB
chmod +x "$STUB_DIR/docker"

# Failure accounting goes through a file, not a variable. `run` is called inside
# a command substitution so its body executes in a SUBSHELL — a `FAILURES=$((…))`
# there is discarded on return, and a wrong exit status would never turn the
# suite red. That is the same shape as the check this suite retires, so it is
# not repeated here. Verdicts go to stderr for the same reason: stdout belongs
# to the script output the caller is capturing.
FAILFILE="$STUB_DIR/failures"; : > "$FAILFILE"

pass() { echo "ok    $1" >&2; }
fail() { echo "FAIL  $1" >&2; echo x >> "$FAILFILE"; }

# run <expected-exit> <label> — echoes the script's output on stdout, verdict on stderr
run() {
  local want="$1" label="$2" out rc
  set +e
  out=$(PATH="$STUB_DIR:$PATH" sh "$SCRIPT" 2>&1); rc=$?
  set -e
  if [ "$rc" -ne "$want" ]; then
    fail "$label — expected exit $want, got $rc"
    printf '%s\n' "$out" | sed 's/^/        /' >&2
  else
    pass "$label (exit $rc)"
  fi
  printf '%s' "$out"
}

expect_contains() {
  if printf '%s' "$1" | grep -qF -- "$2"; then pass "$3"
  else fail "$3 — output does not contain: $2"; fi
}

expect_absent() {
  if printf '%s' "$1" | grep -qF -- "$2"; then fail "$3 — output still contains: $2"
  else pass "$3"; fi
}

echo "── all preconditions met ──"
out=$(STUB_IMAGE_PRESENT=yes STUB_TABLES=3 run 0 "green: image present + schema converged")
expect_contains "$out" "Host ready" "green: reports ready"

echo
echo "── check 1 red ──"
out=$(STUB_IMAGE_PRESENT=no STUB_TABLES=3 run 1 "red: bot image missing")
expect_contains "$out" "is NOT on this host" "red: names the missing image"
expect_contains "$out" "docker pull vexaai/vexa-bot:" "red: gives the fix"

echo
echo "── check 2 red ──"
out=$(STUB_IMAGE_PRESENT=yes STUB_TABLES=0 run 1 "red: schema empty (0/3 tables)")
expect_contains "$out" "vexa_v012 has 0/3" "red: names the shortfall"

out=$(STUB_IMAGE_PRESENT=yes STUB_TABLES=fail run 1 "red: postgres container gone / daemon unreachable")
expect_contains "$out" "vexa_v012 has ERR/3" "red: an unreadable database is a failure, not a pass"

echo
echo "── both red ──"
out=$(STUB_IMAGE_PRESENT=no STUB_TABLES=fail run 1 "red: image missing AND schema unreadable")
expect_contains "$out" "Host NOT ready" "red: reports not-ready"

echo
echo "── the retired check must stay retired ──"
out=$(STUB_IMAGE_PRESENT=yes STUB_TABLES=3 run 0 "regression: no Redis comparison")
expect_absent "$out" "Redis instances separated" "retired: no Redis verdict is printed"
expect_absent "$out" "(0.10=" "retired: nothing reports on the removed 0.10 stack"
# The strongest of the three: independent of anything the script prints, and
# immune to the 2>/dev/null that let the original defect hide.
if [ -s "$STUB_INSPECT_LOG" ]; then
  fail "retired: script made docker inspect call(s): $(tr '\n' ' ' < "$STUB_INSPECT_LOG")"
else
  pass "retired: script makes no docker inspect call"
fi

echo
COUNT=$(wc -l < "$FAILFILE" | tr -d ' ')
if [ "$COUNT" -eq 0 ]; then
  echo "All assertions passed."
else
  echo "$COUNT assertion(s) failed."
  exit 1
fi
