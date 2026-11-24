#!/usr/bin/env bash
# Tests for e2e-rollback-evidence.sh: which E2E run counts as proof that E2E is
# red for a reason outside the deploys. gh is a stub on PATH that applies the
# script's own --jq filter to canned JSON, so the real selection runs.
#
# Run: bash deploy/scripts/tests/e2e-rollback-evidence.test.sh

set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/e2e-rollback-evidence.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
failures=0
GREEN=2026-10-28T04:00:00Z

# gh api [-X GET] [--paginate] <path> [-f ...] --jq <expr>
cat > "$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
[ -e "$STUB_DIR/fail" ] && exit 1
path="" expr=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jq) expr="$2"; shift 2 ;;
    -X|-f) shift 2 ;;
    repos/*) path="$1"; shift ;;
    *) shift ;;
  esac
done
case "$path" in
  */workflows/e2e-prod-tenant.yml/runs) file="$STUB_DIR/runs.json" ;;
  */jobs) file="$STUB_DIR/jobs.json" ;;
  */actions/runs/*) file="$STUB_DIR/run-${path##*/}.json" ;;
esac
jq -r "$expr" "$file"
STUB
chmod +x "$tmp/bin/gh"
echo '{"path":".github/workflows/auto-rollback.yml"}' > "$tmp/run-900.json"
echo '{"path":".github/workflows/portal-api.yml"}' > "$tmp/run-901.json"
echo '{"jobs":[{"completed_at":"2026-10-28T06:08:00Z"},{"completed_at":"2026-10-28T06:10:00Z"}]}' > "$tmp/jobs.json"

# run <created> <id> <conclusion> <actor> <title>
run() { jq -n --arg c "$1" --argjson id "$2" --arg k "$3" --arg a "$4" --arg t "$5" \
  '{created_at: $c, id: $id, status: "completed", conclusion: $k, event: "workflow_dispatch", head_branch: "main", triggering_actor: {login: $a}, display_title: $t}'; }
runs() { jq -s '{workflow_runs: .}' > "$tmp/runs.json"; }
BOT="github-actions[bot]"
AFTER="E2E prod-tenant: after rollback by auto-rollback run"

evidence() { out=$(STUB_DIR="$tmp" PATH="$tmp/bin:$PATH" bash "$target" GetKlai/klai "$GREEN" 2>"$tmp/err"); rc=$?; }
check() {
  local desc="$1"; shift
  if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc"; echo "       out: $out"; sed 's/^/       err: /' "$tmp/err"; failures=$((failures + 1)); fi
}

echo "# a red post-rollback run by an earlier round is evidence"
{ run 2026-10-28T06:05:00Z 11 failure "$BOT" "$AFTER 900"; } | runs
evidence
check "exit 0" test "$rc" = 0
check "prints its last job completion and its id" test "$out" = "2026-10-28T06:10:00Z 11"

echo "# the newest post-rollback run was cancelled: no evidence, even with an older red one"
{ run 2026-10-28T06:05:00Z 11 failure "$BOT" "$AFTER 900"; run 2026-10-28T07:05:00Z 12 cancelled "$BOT" "$AFTER 900"; } | runs
evidence
check "nothing printed" test -z "$out"

echo "# a manual red run is not evidence, not even with the same name"
{ run 2026-10-28T06:05:00Z 13 failure someone "E2E prod-tenant: checking login"; run 2026-10-28T06:15:00Z 14 failure someone "$AFTER 900"; } | runs
evidence
check "nothing printed" test -z "$out"

echo "# a red post-rollback run from before the last green run has expired"
{ run 2026-10-28T03:05:00Z 15 failure "$BOT" "$AFTER 900"; } | runs
evidence
check "nothing printed" test -z "$out"

echo "# the name points at a run that is not Auto-rollback"
{ run 2026-10-28T06:05:00Z 16 failure "$BOT" "$AFTER 901"; } | runs
evidence
check "nothing printed, exit 0" test -z "$out" -a "$rc" = 0
check "warned" grep -q 'not an Auto-rollback run' "$tmp/err"

echo "# a completion time that is not a timestamp"
{ run 2026-10-28T06:05:00Z 11 failure "$BOT" "$AFTER 900"; } | runs
echo '{"jobs":[{"completed_at":null}]}' > "$tmp/jobs.json"
evidence
check "nothing printed, exit 0" test -z "$out" -a "$rc" = 0

echo "# the API cannot be read: fail open"
touch "$tmp/fail"
evidence
check "nothing printed, exit 0" test -z "$out" -a "$rc" = 0
check "warned" grep -q 'could not list the E2E prod-tenant runs' "$tmp/err"

if (( failures > 0 )); then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all checks passed"
