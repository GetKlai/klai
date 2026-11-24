#!/usr/bin/env bash
# Runs scripts/ship-verdict.sh against a fake `gh` that serves hand-written
# JSON, and checks the verdict line and exit code of each outcome. The fake
# `sleep` advances a tick; a fixture named <name>.<n>.json replaces <name>.json
# from tick n on, which is how a run goes from queued to green between polls.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/scripts/ship-verdict.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/common"

cat > "$tmp/bin/gh" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
args="$*"
tick=$(cat "$FIX/tick" 2>/dev/null || echo 0)
# pick <name> <default>: newest <name>.<n>.json with n <= tick, else <name>.json, else common, else default.
pick() {
  local t f
  for ((t = tick; t >= 0; t--)); do
    if ((t == 0)); then f="$FIX/$1.json"; else f="$FIX/$1.$t.json"; fi
    [[ -f "$f" ]] && { echo "$f"; return; }
  done
  [[ -f "$FIX/common/$1.json" ]] && { echo "$FIX/common/$1.json"; return; }
  printf '%s' "$2" > "$FIX/.default"; echo "$FIX/.default"
}
emit() {
  if [[ "$args" == *"--jq "* ]]; then jq -r "${args##*--jq }" "$1"; else cat "$1"; fi
}
case "$args" in
  "pr view"*) emit "$(pick pr '{}')" ;;
  "api -H"*contents*) cat "$FIX/common/e2e-prod-tenant.yml" ;;
  "api repos/"*"/commits/"*) emit "$(pick commit '{}')" ;;
  "api repos/"*"/compare/"*) emit "$(pick compare '{}')" ;;
  "api repos/"*"/actions/runs/"*"/artifacts"*)
    id=$(sed -E 's|.*/runs/([0-9]+)/artifacts.*|\1|' <<<"$args")
    emit "$(pick "artifacts.$id" '{"artifacts":[]}')" ;;
  "run list"*"--commit"*) emit "$(pick runs_commit '[]')" ;;
  "run list"*"e2e-prod-tenant.yml"*) emit "$(pick e2e '[]')" ;;
  "run list"*"auto-rollback.yml"*) emit "$(pick autorb '[]')" ;;
  "run view"*)
    id=$(awk '{print $3}' <<<"$args")
    emit "$(pick "jobs.$id" '{"jobs":[]}')" ;;
  "issue list"*) emit "$(pick issues '[]')" ;;
  *) echo "fake gh: unhandled: $args" >&2; exit 99 ;;
esac
FAKE
cat > "$tmp/bin/sleep" <<'FAKE'
#!/usr/bin/env bash
t=$(cat "$FIX/tick" 2>/dev/null || echo 0)
((t < 40)) || { echo "fake sleep: ran away" >&2; exit 99; }
echo $((t + 1)) > "$FIX/tick"
FAKE
chmod +x "$tmp/bin/gh" "$tmp/bin/sleep"

SHA=aaaaaaaa11112222333344445555666677778888
NEWER=bbbbbbbb11112222333344445555666677778888

cat > "$tmp/common/e2e-prod-tenant.yml" <<'YML'
name: E2E prod-tenant
on:
  workflow_run:
    workflows:
      - 'Build and push svc-a'
      - 'Sync compose and config to core-01'
    types: [completed]
    branches: [main]
  schedule:
    - cron: '0 4 * * *'
YML
cat > "$tmp/common/commit.json" <<JSON
{"sha":"$SHA","commit":{"committer":{"date":"2026-01-10T10:00:00Z"}}}
JSON
echo '{"status":"ahead"}' > "$tmp/common/compare.json"
cat > "$tmp/common/pr.json" <<JSON
{"state":"MERGED","mergedAt":"2026-01-10T10:00:00Z","mergeCommit":{"oid":"$SHA"}}
JSON

# newcase <name>: a case directory that shares the common fixtures.
newcase() { mkdir -p "$tmp/$1"; ln -s "$tmp/common" "$tmp/$1/common"; }
# fx <case> <name>: fixture content on stdin.
fx() { cat > "$tmp/$1/$2.json"; }
# deploys_done <case> <fixture-name>: two deploy runs; the last finished at 10:08:00Z (D).
deploys_done() {
  fx "$1" "$2" <<'JSON'
[{"databaseId":101,"workflowName":"Build and push svc-a","status":"completed","conclusion":"success","createdAt":"2026-01-10T10:00:05Z","updatedAt":"2026-01-10T10:06:00Z"},
 {"databaseId":102,"workflowName":"Sync compose and config to core-01","status":"completed","conclusion":"success","createdAt":"2026-01-10T10:00:06Z","updatedAt":"2026-01-10T10:08:00Z"},
 {"databaseId":103,"workflowName":"Unrelated lint","status":"completed","conclusion":"failure","createdAt":"2026-01-10T10:00:07Z","updatedAt":"2026-01-10T10:01:00Z"}]
JSON
}
running_deploy() {
  fx "$1" runs_commit <<'JSON'
[{"databaseId":101,"workflowName":"Build and push svc-a","status":"in_progress","conclusion":"","createdAt":"2026-01-10T10:00:05Z","updatedAt":"2026-01-10T10:06:00Z"}]
JSON
}
# job_at <case> <run id> <time> [from tick]: the run's first job started at <time>.
job_at() {
  fx "$1" "jobs.$2${4:+.$4}" <<JSON
{"jobs":[{"name":"e2e","conclusion":"success","startedAt":"$3","steps":[]}]}
JSON
}

fails=0
# run_case <case> <verdict> <exit> <output pattern or ""> <args for the script...>
run_case() {
  local name=$1 want=$2 wantrc=$3 pat=$4; shift 4
  rm -f "$tmp/$name/tick"
  local out rc=0 last
  out=$(FIX="$tmp/$name" PATH="$tmp/bin:$PATH" SHIP_VERDICT_REPO=example/repo \
    bash "$SCRIPT" --poll 0 --grace 120 "$@" 2>&1) || rc=$?
  last=$(tail -n1 <<<"$out")
  if [[ "$last" != "verdict=$want" || "$rc" != "$wantrc" ]] || { [[ -n "$pat" ]] && ! grep -qF -- "$pat" <<<"$out"; }; then
    echo "FAIL $name: want verdict=$want rc=$wantrc pattern=[$pat], got [$last] rc=$rc"
    echo "    ${out//$'\n'/$'\n    '}"
    fails=$((fails + 1))
  else
    echo "ok   $name -> $last (exit $rc)"
  fi
}

# 1. Nothing to deploy: only an unrelated workflow ran for the commit.
newcase nothing
fx nothing runs_commit <<'JSON'
[{"databaseId":103,"workflowName":"Unrelated lint","status":"completed","conclusion":"success","createdAt":"2026-01-10T10:00:07Z","updatedAt":"2026-01-10T10:01:00Z"}]
JSON
run_case nothing nothing-to-deploy 0 "within 120s" "$SHA"

# 2. Deploy running, then done; E2E not there yet, then green. Resolved from a PR number.
newcase green
running_deploy green
deploys_done green runs_commit.1
fx green e2e <<'JSON'
[]
JSON
fx green e2e.2 <<JSON
[{"databaseId":201,"status":"completed","conclusion":"success","event":"workflow_run","createdAt":"2026-01-10T10:08:10Z","updatedAt":"2026-01-10T10:15:00Z","headSha":"$SHA"}]
JSON
job_at green 201 2026-01-10T10:09:00Z
run_case green live-and-proven 0 "and passed" 4242

# 3. A deploy run failed at a named step.
newcase failed
fx failed runs_commit <<'JSON'
[{"databaseId":101,"workflowName":"Build and push svc-a","status":"completed","conclusion":"failure","createdAt":"2026-01-10T10:00:05Z","updatedAt":"2026-01-10T10:06:00Z"}]
JSON
fx failed jobs.101 <<'JSON'
{"jobs":[{"name":"build-push","conclusion":"failure","startedAt":"2026-01-10T10:01:00Z","steps":[{"name":"Checkout","conclusion":"success"},{"name":"Build image","conclusion":"failure"}]}]}
JSON
run_case failed failed 1 "build-push / Build image" "$SHA"

# 4. The first E2E run after D is cancelled and the next one is queued behind it:
#    wait for the green one instead of counting the cancelled run.
newcase cancelled-then-green
deploys_done cancelled-then-green runs_commit
fx cancelled-then-green e2e <<'JSON'
[{"databaseId":202,"status":"queued","conclusion":"","event":"workflow_run","createdAt":"2026-01-10T10:08:20Z","updatedAt":"2026-01-10T10:08:20Z","headSha":"x"},
 {"databaseId":201,"status":"completed","conclusion":"cancelled","event":"workflow_run","createdAt":"2026-01-10T10:08:10Z","updatedAt":"2026-01-10T10:09:30Z","headSha":"x"}]
JSON
job_at cancelled-then-green 201 2026-01-10T10:09:00Z
fx cancelled-then-green e2e.2 <<JSON
[{"databaseId":202,"status":"completed","conclusion":"success","event":"workflow_run","createdAt":"2026-01-10T10:08:20Z","updatedAt":"2026-01-10T10:20:00Z","headSha":"$SHA"},
 {"databaseId":201,"status":"completed","conclusion":"cancelled","event":"workflow_run","createdAt":"2026-01-10T10:08:10Z","updatedAt":"2026-01-10T10:09:30Z","headSha":"$SHA"}]
JSON
job_at cancelled-then-green 202 2026-01-10T10:10:00Z 2
run_case cancelled-then-green live-and-proven 0 "is queued, not started" "$SHA"

# 5. E2E red after D and no Auto-rollback run follows.
newcase red-after
deploys_done red-after runs_commit
fx red-after e2e <<'JSON'
[{"databaseId":201,"status":"completed","conclusion":"failure","event":"workflow_run","createdAt":"2026-01-10T10:08:10Z","updatedAt":"2026-01-10T10:15:00Z","headSha":"x"}]
JSON
job_at red-after 201 2026-01-10T10:09:00Z
run_case red-after failed-e2e 1 "ended failure" "$SHA"

# 6. The same red run, and an Auto-rollback run that uploaded rollback-report.
newcase rolled-back
deploys_done rolled-back runs_commit
cp "$tmp/red-after/e2e.json" "$tmp/rolled-back/e2e.json"
job_at rolled-back 201 2026-01-10T10:09:00Z
fx rolled-back autorb <<'JSON'
[{"databaseId":301,"status":"completed","conclusion":"failure","createdAt":"2026-01-10T10:15:05Z"}]
JSON
fx rolled-back artifacts.301 <<'JSON'
{"artifacts":[{"name":"rollback-report"}]}
JSON
run_case rolled-back rolled-back 1 "uploaded rollback-report" "$SHA"

# 7. main was already red before the merge: reported, then the normal flow ends green.
newcase main-red
deploys_done main-red runs_commit
fx main-red e2e <<JSON
[{"databaseId":201,"status":"completed","conclusion":"success","event":"workflow_run","createdAt":"2026-01-10T10:08:10Z","updatedAt":"2026-01-10T10:15:00Z","headSha":"$SHA"},
 {"databaseId":190,"status":"completed","conclusion":"cancelled","event":"workflow_run","createdAt":"2026-01-10T09:30:00Z","updatedAt":"2026-01-10T09:31:00Z","headSha":"x"},
 {"databaseId":189,"status":"completed","conclusion":"failure","event":"schedule","createdAt":"2026-01-10T09:00:00Z","updatedAt":"2026-01-10T09:20:00Z","headSha":"x"},
 {"databaseId":188,"status":"completed","conclusion":"failure","event":"schedule","createdAt":"2026-01-09T04:00:00Z","updatedAt":"2026-01-09T04:20:00Z","headSha":"x"},
 {"databaseId":187,"status":"completed","conclusion":"success","event":"schedule","createdAt":"2026-01-08T04:00:00Z","updatedAt":"2026-01-08T04:20:00Z","headSha":"x"}]
JSON
job_at main-red 201 2026-01-10T10:09:00Z
fx main-red issues <<'JSON'
[{"number":9001,"title":"CI failure: E2E prod-tenant"},{"number":9002,"title":"CI failure: Version drift"}]
JSON
run_case main-red live-and-proven 0 "failing since 2026-01-09T04:00:00Z (2 runs), open ci-failure issue #9001" "$SHA"

# 8. Only the config sync ran: no E2E run follows from it.
newcase configsync-only
fx configsync-only runs_commit <<'JSON'
[{"databaseId":104,"workflowName":"Sync LibreChat base config to core-01","status":"completed","conclusion":"success","createdAt":"2026-01-10T10:00:05Z","updatedAt":"2026-01-10T10:02:00Z"}]
JSON
run_case configsync-only deployed-no-e2e 3 "nightly" "$SHA"

# 9. A deploy that never finishes ends in a timeout; --no-wait reports the state instead.
newcase running
running_deploy running
run_case running timeout 2 "gave up" --timeout 0 "$SHA"
run_case running pending 3 "in_progress" --no-wait "$SHA"

# 10. A newer commit's deploys overlap: the green run is attributed to the newer tip.
newcase newer-tip
deploys_done newer-tip runs_commit
fx newer-tip e2e <<JSON
[{"databaseId":201,"status":"completed","conclusion":"success","event":"workflow_run","createdAt":"2026-01-10T10:08:10Z","updatedAt":"2026-01-10T10:15:00Z","headSha":"$NEWER"}]
JSON
job_at newer-tip 201 2026-01-10T10:09:00Z
run_case newer-tip live-and-proven 0 "main's tip was bbbbbbbb1" "$SHA"

# 11. A commit that is not on main is refused.
newcase offmain
echo '{"status":"diverged"}' > "$tmp/offmain/compare.json"
run_case offmain error 4 "not on main" "$SHA"

((fails == 0)) || { echo "$fails case(s) failed"; exit 1; }
echo "all ship-verdict cases passed"
