#!/usr/bin/env bash
# A Redis-protocol dump may only be copied once its BGSAVE has finished. The
# nightly FalkorDB copy was three hours stale because backup.sh slept 3 s and
# copied the previous dump (2026-10-22). docker is stubbed: LASTSAVE moves only
# after the third poll, the way a long save looks from outside.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat >"$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
state="$STUB_DIR/lastsave-polls"
case "$*" in
  *" BGSAVE") echo "Background saving started" ;;
  *" LASTSAVE")
    n=$(( $(cat "$state" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$state"
    if [[ "${STUB_SAVE_NEVER_FINISHES:-}" == 1 || "$n" -le 3 ]]; then echo 1000; else echo 2000; fi ;;
  *" INFO persistence") printf 'rdb_bgsave_in_progress:0\nrdb_last_bgsave_status:ok\n' ;;
  *) exit 1 ;;
esac
STUB
chmod +x "$TMP/bin/docker"
export PATH="$TMP/bin:$PATH" STUB_DIR="$TMP"

# shellcheck source=/dev/null
source "$REPO_ROOT/deploy/scripts/backup.sh"
trap - ERR EXIT
trap 'rm -rf "$TMP"' EXIT
set +e

fail_test() { echo "FAIL: $1" >&2; exit 1; }

# The save finishes on the fourth LASTSAVE (the first is the baseline): the
# helper must not return before it.
bgsave_and_wait klai-core-falkordb-1 >/dev/null 2>&1 || fail_test "a save that finishes was reported as failed"
polls=$(cat "$TMP/lastsave-polls")
[[ "$polls" -ge 4 ]] || fail_test "returned after $polls LASTSAVE polls, before the save had finished"

# A save that never finishes must fail the step instead of copying a stale dump.
rm -f "$TMP/lastsave-polls"
BGSAVE_TIMEOUT=2 STUB_SAVE_NEVER_FINISHES=1 bgsave_and_wait klai-core-falkordb-1 >/dev/null 2>&1 \
  && fail_test "a save that never finished was reported as done"

# A LASTSAVE call that fails must fail the step, not read as a finished save.
rm -f "$TMP/lastsave-polls"
cat >"$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *" INFO persistence") echo 'rdb_bgsave_in_progress:0' ;;
  *" BGSAVE") echo "Background saving started" ;;
  *) exit 1 ;;
esac
STUB
BGSAVE_TIMEOUT=2 bgsave_and_wait klai-core-falkordb-1 >/dev/null 2>&1 \
  && fail_test "a failing LASTSAVE was reported as a finished save"

echo "backup-bgsave-wait: PASS"
