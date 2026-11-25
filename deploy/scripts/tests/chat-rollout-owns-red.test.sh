#!/usr/bin/env bash
# Tests for chat-rollout-owns-red.sh: which red E2E run Auto-rollback leaves to
# the Chat image rollout. gh is a stub that applies the script's own --jq
# filters to canned JSON, so the real selection runs.
#
# Run: bash deploy/scripts/tests/chat-rollout-owns-red.test.sh

set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/chat-rollout-owns-red.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
failures=0

cat > "$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
[ -e "$STUB_DIR/fail" ] && exit 1
path="" expr=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jq) expr="$2"; shift 2 ;;
    repos/*) path="$1"; shift ;;
    *) shift ;;
  esac
done
case "$path" in
  */workflows/chat-image-rollout.yml/runs*) file="$STUB_DIR/runs.json" ;;
  */jobs) id="${path%/jobs}"; file="$STUB_DIR/jobs-${id##*/}.json" ;;
esac
jq -r "$expr" "$file"
STUB
chmod +x "$tmp/bin/gh"

RED_CREATED=2026-11-24T10:00:00Z
RED_UPDATED=2026-11-24T10:15:00Z
owns() { out=$(STUB_DIR="$tmp" PATH="$tmp/bin:$PATH" bash "$target" example/repo "$RED_CREATED" "$RED_UPDATED" 2>"$tmp/err"); rc=$?; }
check() {
  local desc="$1"; shift
  if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc (out '$out', rc $rc)"; failures=$((failures + 1)); fi
}
# run <id> <started> <updated>
run() { jq -n --argjson id "$1" --arg s "$2" --arg u "$3" '{id: $id, run_started_at: $s, updated_at: $u}'; }
runs() { jq -s '{workflow_runs: .}' > "$tmp/runs.json"; }
# jobs <id> <name started completed>...
jobs() {
  local id="$1"; shift
  local out='[]'
  while (( $# )); do
    out="$(jq --arg n "$1" --arg s "$2" --arg c "$3" '. + [{name: $n, started_at: $s, completed_at: (if $c == "null" then null else $c end)}]' <<< "$out")"
    shift 3
  done
  jq '{jobs: .}' <<< "$out" > "$tmp/jobs-$id.json"
}

echo "# a rollout job that ran while the red run did: owned"
{ run 1 2026-11-24T09:50:00Z 2026-11-24T10:30:00Z; } | runs
jobs 1 plan 2026-11-24T09:50:00Z 2026-11-24T09:51:00Z rollout 2026-11-24T09:51:00Z 2026-11-24T10:30:00Z
owns
check "prints the rollout run" test "$out" = 1

echo "# a rollout job still running: owned"
jobs 1 plan 2026-11-24T09:50:00Z 2026-11-24T09:51:00Z rollout 2026-11-24T09:51:00Z null
owns
check "prints the rollout run" test "$out" = 1

echo "# only the plan job ran during the red run (the hourly noop): not owned"
{ run 2 2026-11-24T10:05:00Z 2026-11-24T10:06:00Z; } | runs
jobs 2 plan 2026-11-24T10:05:00Z 2026-11-24T10:06:00Z
owns
check "nothing printed" test -z "$out"

echo "# a rollout job that finished before the red run was created: not owned"
{ run 3 2026-11-24T09:00:00Z 2026-11-24T10:01:00Z; } | runs
jobs 3 rollout 2026-11-24T09:01:00Z 2026-11-24T09:59:00Z
owns
check "nothing printed" test -z "$out"

echo "# a rollout job that started after the red run ended: not owned"
{ run 4 2026-11-24T10:10:00Z 2026-11-24T10:40:00Z; } | runs
jobs 4 rollout 2026-11-24T10:16:00Z 2026-11-24T10:40:00Z
owns
check "nothing printed" test -z "$out"

echo "# the API cannot be read: fail open"
touch "$tmp/fail"
owns
check "nothing printed, exit 0" test -z "$out" -a "$rc" = 0

if (( failures > 0 )); then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all checks passed"
