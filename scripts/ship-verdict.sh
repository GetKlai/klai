#!/usr/bin/env bash
# Tells whether one merged commit is live and proven in production.
#
#   scripts/ship-verdict.sh [--no-wait] [--repo OWNER/NAME] [--poll S] [--grace S]
#                           [--timeout S] <sha-or-pr-number>
#
# Final line: verdict=<error|live-and-proven|nothing-to-deploy|failed|failed-e2e|
#                      rolled-back|timeout|pending|deployed-no-e2e>
# Exit: 0 live-and-proven, nothing-to-deploy; 1 failed, failed-e2e, rolled-back;
#       2 timeout; 3 pending (--no-wait only), deployed-no-e2e; 4 usage or lookup error.
#
# E2E prod-tenant carries no link to the commit that triggered it, so a run
# proves the commit when its first job started after the last deploy run of the
# commit finished and it ended green. See docs/runbooks/ship-verdict.md.
set -euo pipefail

E2E_FILE="e2e-prod-tenant.yml"
# Not in E2E's workflow_run list: it deploys, but no E2E run follows from it.
EXTRA_DEPLOY="Sync LibreChat base config to core-01"

REPO="${SHIP_VERDICT_REPO:-}"
POLL=30 GRACE=120 TIMEOUT=2700 NOWAIT=0 TARGET=""

die() { echo "ship-verdict: $*" >&2; echo "verdict=error"; exit 4; }
usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 4; }

while (($#)); do
  case "$1" in
    --no-wait) NOWAIT=1 ;;
    --repo) REPO="${2:?--repo needs a value}"; shift ;;
    --poll) POLL="${2:?}"; shift ;;
    --grace) GRACE="${2:?}"; shift ;;
    --timeout) TIMEOUT="${2:?}"; shift ;;
    -h|--help) usage ;;
    -*) die "unknown option $1" ;;
    *) [[ -z "$TARGET" ]] || die "one commit or PR number only"; TARGET="$1" ;;
  esac
  shift
done
[[ -n "$TARGET" ]] || usage

# One retry: a single network blip must not end a 45-minute wait.
ghr() { gh "$@" || gh "$@" || die "gh $1 $2 failed"; }

[[ -n "$REPO" ]] || REPO="$(ghr repo view --json nameWithOwner --jq .nameWithOwner)"

# 1. Resolve the commit and refuse one that is not on main.
MERGED_AT=""
if [[ "$TARGET" =~ ^[0-9]{1,6}$ ]]; then
  pr="$(ghr pr view "$TARGET" --repo "$REPO" --json state,mergedAt,mergeCommit)"
  [[ "$(jq -r .state <<<"$pr")" == MERGED ]] || die "PR #$TARGET is not merged"
  SHA="$(jq -r .mergeCommit.oid <<<"$pr")"
  MERGED_AT="$(jq -r .mergedAt <<<"$pr")"
else
  SHA="$TARGET"
fi
commit="$(ghr api "repos/$REPO/commits/$SHA")"
SHA="$(jq -r .sha <<<"$commit")"
[[ -n "$MERGED_AT" ]] || MERGED_AT="$(jq -r .commit.committer.date <<<"$commit")"
rel="$(ghr api "repos/$REPO/compare/$SHA...main" --jq .status)"
[[ "$rel" == identical || "$rel" == ahead ]] || die "$SHA is not on main (compare says: $rel)"
SHORT="${SHA:0:9}"
MERGED_EPOCH="$(jq -rn --arg t "$MERGED_AT" '$t | fromdateiso8601')"

say() { echo "$*"; }
finish() { # verdict exit-code
  echo "verdict=$1"
  exit "$2"
}

e2e_runs() {
  ghr run list --repo "$REPO" --workflow "$E2E_FILE" --branch main --limit 60 \
    --json databaseId,status,conclusion,event,createdAt,updatedAt,headSha
}

# 2. Is main already red before this merge? Report, then carry on.
streak="$(e2e_runs | jq -c --arg m "$MERGED_AT" '
  [.[] | select(.createdAt < $m and .status == "completed" and .conclusion != "cancelled" and .conclusion != "skipped")] as $a
  | (($a | map(.conclusion == "success") | index(true)) // ($a | length)) as $i
  | $a[:$i] | select(length > 0)
  | {since: .[-1].createdAt, runs: length}')"
if [[ -n "$streak" ]]; then
  issue="$(ghr issue list --repo "$REPO" --label ci-failure --state open --json number,title \
    --jq '[.[] | select(.title | test("E2E prod-tenant"))][0].number // empty')"
  say "main was already red before this merge: E2E prod-tenant failing since $(jq -r .since <<<"$streak") ($(jq -r .runs <<<"$streak") runs)${issue:+, open ci-failure issue #$issue}"
fi

# 3. The deploy workflows: E2E's own trigger list at this commit, plus the config sync.
wf_yaml="$(ghr api -H 'Accept: application/vnd.github.raw' "repos/$REPO/contents/.github/workflows/$E2E_FILE?ref=$SHA")"
TRIGGERS="$(awk '
  /^  workflow_run:/ {in_wr=1; next}
  in_wr && /^    workflows:/ {in_list=1; next}
  in_list && /^      - / {sub(/^      - /, ""); gsub(/^[\047"]|[\047"]$/, ""); print; next}
  in_list {exit}
' <<<"$wf_yaml")"
[[ -n "$TRIGGERS" ]] || die "no workflow_run.workflows list found in $E2E_FILE at $SHORT"
DEPLOY_SET="$(jq -nc --arg t "$TRIGGERS" --arg x "$EXTRA_DEPLOY" '($t | split("\n") | map(select(length > 0))) + [$x]')"

never_started() { # run json -> true for a run that was cancelled before any job began
  [[ "$(jq -r .conclusion <<<"$1")" == cancelled ]] \
    && [[ "$(ghr run view "$(jq -r .databaseId <<<"$1")" --repo "$REPO" --json jobs | jq '.jobs | length')" == 0 ]]
}

# A concurrency group keeps one waiting run: a second push while a deploy is
# running cancels the run that was waiting, before it starts a job. The newer
# run deploys a main that contains this commit, so its outcome is this
# commit's outcome. Until that run exists the answer is "still queued".
successor() { # run json -> the first later push run of that workflow on main that did start
  local later r
  later="$(ghr run list --repo "$REPO" --workflow "$(jq -r .workflowName <<<"$1")" --branch main --event push --limit 30 \
    --json databaseId,workflowName,status,conclusion,createdAt,updatedAt \
    | jq -c --arg t "$(jq -r .createdAt <<<"$1")" '[.[] | select(.createdAt > $t)] | sort_by(.createdAt)[]')"
  while IFS= read -r r; do
    [[ -n "$r" ]] || continue
    never_started "$r" || { echo "$r"; return; }
  done <<<"$later"
  jq -c '.status = "queued" | .conclusion = ""' <<<"$1"
}

deploy_runs() {
  local runs r out='[]'
  runs="$(ghr run list --repo "$REPO" --commit "$SHA" --event push --limit 100 \
    --json databaseId,workflowName,status,conclusion,createdAt,updatedAt \
    | jq -c --argjson set "$DEPLOY_SET" '.[] | select(.workflowName as $n | $set | index($n))')"
  while IFS= read -r r; do
    [[ -n "$r" ]] || continue
    if never_started "$r"; then r="$(successor "$r")"; fi
    out="$(jq -c --argjson r "$r" '. + [$r]' <<<"$out")"
  done <<<"$runs"
  echo "$out"
}

# An Auto-rollback run since the merge that uploaded rollback-report.
rollback_report_run() {
  local runs id
  runs="$(ghr run list --repo "$REPO" --workflow auto-rollback.yml --branch main --limit 20 \
    --json databaseId,status,conclusion,createdAt | jq -c --arg m "$MERGED_AT" '[.[] | select(.createdAt >= $m and .conclusion != "skipped")]')"
  for id in $(jq -r '.[].databaseId' <<<"$runs"); do
    if ghr api "repos/$REPO/actions/runs/$id/artifacts" --jq '[.artifacts[].name] | index("rollback-report") != null' | grep -qx true; then
      echo "$id"; return 0
    fi
  done
  return 1
}

failed_step() { # run id -> "job / step"
  ghr run view "$1" --repo "$REPO" --json jobs | jq -r '
    [.jobs[] | select(.conclusion == "failure" or .conclusion == "timed_out")][0]
    | if . == null then "(no failing job recorded)"
      else "\(.name) / \([.steps[] | select(.conclusion == "failure" or .conclusion == "timed_out")][0].name // "(no failing step recorded)")" end'
}

now_epoch() { jq -n 'now | floor'; }

# Auto-rollback state after a red non-dispatch E2E run: rolled-back | waiting | none | cleared
rollback_state() { # red run updatedAt
  local ar rid
  if rid="$(rollback_report_run)"; then echo "rolled-back $rid"; return; fi
  ar="$(ghr run list --repo "$REPO" --workflow auto-rollback.yml --branch main --limit 20 \
    --json databaseId,status,conclusion,createdAt | jq -c --arg t "$1" '[.[] | select(.createdAt >= $t and .conclusion != "skipped")][0] // empty')"
  if [[ -z "$ar" ]]; then
    # The run starts within seconds of the red one ending; give it a minute.
    local age=$(( $(now_epoch) - $(jq -rn --arg t "$1" '$t | fromdateiso8601') ))
    if (( age < 60 )); then echo waiting; else echo none; fi
  elif [[ "$(jq -r .status <<<"$ar")" != completed ]]; then echo waiting
  elif [[ "$(jq -r .conclusion <<<"$ar")" == success ]]; then echo cleared
  else echo none; fi
}

# One evaluation of everything. Prints the summary and a verdict, or "continue".
evaluate() {
  local runs n done_n bad
  runs="$(deploy_runs)"
  n="$(jq length <<<"$runs")"

  if (( n == 0 )); then
    if (( $(now_epoch) - MERGED_EPOCH >= GRACE )); then
      say "no deploy workflow started for $SHORT within ${GRACE}s of the merge"
      finish nothing-to-deploy 0
    fi
    say "no deploy run for $SHORT yet (grace ${GRACE}s)"
    return 1
  fi

  say "deploy runs for $SHORT: $(jq -r 'map("\(.workflowName)=\(if .status == "completed" then .conclusion else .status end)") | join(", ")' <<<"$runs")"

  bad="$(jq -c '[.[] | select(.status == "completed" and (.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral"))][0] // empty' <<<"$runs")"
  if [[ -n "$bad" ]]; then
    say "failed: $(jq -r .workflowName <<<"$bad") run $(jq -r .databaseId <<<"$bad"): $(failed_step "$(jq -r .databaseId <<<"$bad")")"
    if rid="$(rollback_report_run)"; then
      say "Auto-rollback run $rid uploaded rollback-report"
      finish rolled-back 1
    fi
    finish failed 1
  fi

  done_n="$(jq '[.[] | select(.status == "completed")] | length' <<<"$runs")"
  (( done_n == n )) || return 1

  local d only_extra
  d="$(jq -r 'map(.updatedAt) | max' <<<"$runs")"
  only_extra="$(jq -r --arg x "$EXTRA_DEPLOY" 'all(.[]; .workflowName == $x)' <<<"$runs")"
  if [[ "$only_extra" == true ]]; then
    say "only '$EXTRA_DEPLOY' ran; no E2E run is triggered by it. Wait for the nightly 04:00 UTC run or start one: gh workflow run $E2E_FILE --ref main -f reason='verify $SHORT'"
    finish deployed-no-e2e 3
  fi
  say "deploys finished at $d"

  local cand r rid status conclusion event started upd rs
  cand="$(e2e_runs | jq -c --arg d "$d" 'sort_by(.createdAt) | map(select(.status != "completed" or .updatedAt >= $d))')"
  for rid in $(jq -r '.[].databaseId' <<<"$cand"); do
    r="$(jq -c --argjson id "$rid" '.[] | select(.databaseId == $id)' <<<"$cand")"
    status="$(jq -r .status <<<"$r")"; conclusion="$(jq -r .conclusion <<<"$r")"
    event="$(jq -r .event <<<"$r")"; upd="$(jq -r .updatedAt <<<"$r")"
    # The first job start, not the run creation: a run queued behind another one
    # in the concurrency group starts its job later than it was created.
    started="$(ghr run view "$rid" --repo "$REPO" --json jobs | jq -r '[.jobs[].startedAt | select(. > "2000")] | min // empty')"
    if [[ -z "$started" ]]; then say "E2E run $rid is queued, not started"; return 1; fi
    [[ "$started" > "$d" ]] || continue
    case "$status:$conclusion" in
      completed:cancelled|completed:skipped) continue ;;
      completed:success)
        rs="$(jq -r .headSha <<<"$r")"
        say "E2E prod-tenant run $rid ($event) started its job $started after the last deploy and passed"
        [[ "$rs" == "$SHA" ]] || say "note: main's tip was ${rs:0:9} at that run, so it also covers later commits"
        finish live-and-proven 0 ;;
      completed:*)
        say "E2E prod-tenant run $rid ($event) ended $conclusion after the deploy"
        [[ "$event" == workflow_dispatch ]] && finish failed-e2e 1
        rs="$(rollback_state "$upd")"
        case "$rs" in
          rolled-back*) say "Auto-rollback run ${rs#* } uploaded rollback-report"; finish rolled-back 1 ;;
          waiting) say "Auto-rollback is confirming; waiting for its outcome"; return 1 ;;
          cleared) say "Auto-rollback did not confirm the red run; looking at later runs"; continue ;;
          *) finish failed-e2e 1 ;;
        esac ;;
      *) say "E2E run $rid is $status"; return 1 ;;
    esac
  done
  say "no E2E run has started after the deploys yet"
  return 1
}

while true; do
  if evaluate; then :; fi   # evaluate exits through finish when it has a verdict
  if (( NOWAIT )); then finish pending 3; fi
  if (( SECONDS >= TIMEOUT )); then
    say "gave up after ${TIMEOUT}s"
    finish timeout 2
  fi
  sleep "$POLL"
done
