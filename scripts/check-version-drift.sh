#!/usr/bin/env bash
# check-version-drift.sh — what Renovate cannot update, or updates nobody merges.
#
# Renovate keeps the pinned images and packages it can see current, but two
# kinds of drift escape it. Pull requests it opens and nobody merges: on
# 2026-09-25 six had been open for up to five weeks, the oldest since
# 2026-08-22. And images Klai builds from upstream source, which no registry
# lookup can follow (crawl4ai, and Presidio and LibreChat, which are pinned by
# digest or rebuilt from an upstream tag). This fails, so the daily run
# opens a ci-failure issue (ci-failure-notice.yml), until someone acts.
#
# A stuck update must also show up within days, not weeks. From 2026-09-25 to
# 2026-10-22 a status check that could never pass held the Docker images, LiteLLM
# and lock file PRs open for four weeks while the Dependency Dashboard listed 13
# updates as Errored. Renovate only opens a PR for a version that already passed
# its release-age wait and merges green PRs hourly, so three more checks fail on
# the symptoms: an automerge PR older than a few days, an Errored or Repository
# Problems section on the dashboard, and no successful Renovate run lately.
#
# Env: GH_TOKEN with read access to REPOS; MAX_PR_AGE_DAYS (default 14);
# AUTOMERGE_MAX_AGE_DAYS (default 3); RENOVATE_MAX_RUN_AGE_HOURS (default 6);
# ACTIONS_TOKEN (optional) for reading workflow runs, which the Renovate app
# token cannot do (the app has no actions permission); defaults to GH_TOKEN.
set -uo pipefail
REPOS="${REPOS:-GetKlai/klai GetKlai/klai-website}"
MAX_PR_AGE_DAYS="${MAX_PR_AGE_DAYS:-14}"
AUTOMERGE_MAX_AGE_DAYS="${AUTOMERGE_MAX_AGE_DAYS:-3}"
RENOVATE_MAX_RUN_AGE_HOURS="${RENOVATE_MAX_RUN_AGE_HOURS:-6}"
RENOVATE_REPO=GetKlai/klai
FAIL=0
fail() { printf 'FAIL: %s\n' "$*"; FAIL=1; }
ok()   { printf 'OK:   %s\n' "$*"; }
note() { printf 'NOTE: %s\n' "$*"; }

cutoff=$(date -u -d "-${MAX_PR_AGE_DAYS} days" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-"${MAX_PR_AGE_DAYS}"d +%Y-%m-%dT%H:%M:%SZ)
for repo in $REPOS; do
  stale=$(gh pr list -R "$repo" --state open --limit 200 --json number,title,createdAt,author \
    --jq "[.[] | select((.author.login | test(\"renovate\")) and .createdAt < \"$cutoff\")] | .[] | \"#\(.number) \(.createdAt[0:10]) \(.title)\"")
  if [ -n "$stale" ]; then
    fail "$repo: Renovate PRs open longer than ${MAX_PR_AGE_DAYS} days:"
    printf '%s\n' "$stale" | sed 's/^/        /'
  else
    ok "$repo: no Renovate PR older than ${MAX_PR_AGE_DAYS} days"
  fi

  # An automerge PR only exists once its version passed the release-age wait,
  # and green ones merge hourly, so one that lives for days is stuck.
  stuck=$(gh pr list -R "$repo" --state open --limit 200 --json number,title,createdAt,author,body \
    --jq "[.[] | select((.author.login | test(\"renovate\")) and (.body | contains(\"Automerge**: Enabled\")) and (now - (.createdAt | fromdateiso8601) > $AUTOMERGE_MAX_AGE_DAYS * 86400))] | .[] | \"\(.number)\t\(((now - (.createdAt | fromdateiso8601)) / 86400) | floor)\t\(.title)\"")
  if [ -n "$stuck" ]; then
    fail "$repo: automerge PRs open longer than ${AUTOMERGE_MAX_AGE_DAYS} days (stuck, Renovate would have merged them):"
    while IFS=$'\t' read -r num age title; do
      printf '        #%s open %sd: %s\n' "$num" "$age" "$title"
      blocking=$(gh pr checks "$num" -R "$repo" --json bucket,name \
        --jq '.[] | select(.bucket != "pass" and .bucket != "skipping") | "\(.bucket) \(.name)"' 2>/dev/null)
      if [ -n "$blocking" ]; then printf '%s\n' "$blocking" | sed 's/^/            not green: /'
      else printf '            all checks green, so Renovate itself is not merging it\n'; fi
    done <<<"$stuck"
  else
    ok "$repo: no automerge PR older than ${AUTOMERGE_MAX_AGE_DAYS} days"
  fi

  # Renovate lists what it could not do in the Dependency Dashboard issue.
  body=$(gh issue list -R "$repo" --state open --search 'Dependency Dashboard in:title' --json title,author,body \
    --jq '.[] | select(.title == "Dependency Dashboard" and (.author.login | test("renovate"))) | .body' | tr -d '\r')
  if [ -z "$body" ]; then
    note "$repo: no Dependency Dashboard issue found"
  else
    clean=1
    for heading in Errored 'Repository Problems'; do
      lines=$(printf '%s\n' "$body" | awk -v h="## $heading" '/^## /{p=($0==h); next} p&&NF' | cut -c1-200)
      if [ -n "$lines" ]; then
        clean=0
        fail "$repo: Dependency Dashboard has a '$heading' section:"
        printf '%s\n' "$lines" | sed 's/^/        /'
      fi
    done
    [ "$clean" = 1 ] && ok "$repo: Dependency Dashboard has no Errored or Repository Problems section"
  fi
done

# Renovate itself broken: the other checks would stay quiet if it never ran.
run_age=$(GH_TOKEN="${ACTIONS_TOKEN:-${GH_TOKEN:-}}" gh run list -R "$RENOVATE_REPO" --workflow renovate.yml --status success --limit 1 \
  --json createdAt --jq '.[0] | if . then ((now - (.createdAt | fromdateiso8601)) / 3600 | floor) else empty end')
if [ -z "$run_age" ]; then fail "$RENOVATE_REPO: no successful renovate.yml run found"
elif [ "$run_age" -ge "$RENOVATE_MAX_RUN_AGE_HOURS" ]; then fail "$RENOVATE_REPO: last successful renovate.yml run was ${run_age}h ago (limit ${RENOVATE_MAX_RUN_AGE_HOURS}h)"
else ok "$RENOVATE_REPO: last successful renovate.yml run ${run_age}h ago"; fi

compose=deploy/docker-compose.yml
upstream_latest() { gh api "repos/$1/releases/latest" --jq .tag_name 2>/dev/null | sed 's/^v//'; }
newer() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$2" ]; }

ours=$(grep -oE 'ghcr.io/getklai/crawl4ai:[0-9.]+' "$compose" | head -1 | sed 's/.*://')
theirs=$(upstream_latest unclecode/crawl4ai)
if [ -z "$theirs" ]; then fail "crawl4ai: could not read the upstream release"
elif newer "$ours" "$theirs"; then fail "crawl4ai: we build $ours, upstream released $theirs (deploy/crawl4ai/Dockerfile)"
else ok "crawl4ai: $ours is the upstream release"; fi

ghcr_latest() { # newest plain X.Y.Z tag of a public GHCR image
  local t; t=$(curl -fsS "https://ghcr.io/token?scope=repository:$1:pull" | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])') || return 0
  curl -fsS -H "Authorization: Bearer $t" "https://ghcr.io/v2/$1/tags/list?n=1000" \
    | python3 -c 'import json,re,sys;v=[x for x in json.load(sys.stdin).get("tags",[]) if re.fullmatch(r"\d+\.\d+\.\d+",x)];print(max(v,key=lambda s:tuple(map(int,s.split(".")))) if v else "")'
}

# Presidio is pinned by digest (analyzer base in its Dockerfile, anonymizer in
# compose), which Renovate cannot map to a version.
ours=$(sed -n "s/^  BASE_PRESIDIO_VERSION: '\(.*\)'/\1/p" .github/workflows/presidio-analyzer-image-build.yml)
theirs=$(ghcr_latest data-privacy-stack/presidio-analyzer)
if [ -z "$theirs" ]; then fail "presidio: could not read the upstream tags"
elif newer "$ours" "$theirs"; then fail "presidio: we build on $ours, upstream published $theirs (deploy/presidio/analyzer/Dockerfile and the anonymizer digest in compose)"
else ok "presidio: $ours is the upstream release"; fi

# LibreChat is rebuilt from an upstream tag; release candidates do not count.
ours=$(sed -n 's/^  DEFAULT_LIBRECHAT_TAG: v//p' .github/workflows/librechat-image-build.yml)
theirs=$(gh api 'repos/danny-avila/LibreChat/tags?per_page=50' --jq '.[].name' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' | sort -V | tail -1)
if [ -z "$theirs" ]; then fail "librechat: could not read the upstream tags"
elif newer "$ours" "$theirs"; then fail "librechat: we build on $ours, upstream released $theirs (DEFAULT_LIBRECHAT_TAG)"
else ok "librechat: $ours is the latest stable upstream release"; fi

ours=$(grep -oE 'vexaai/transcription-service:[0-9.]+' deploy/docker-compose.gpu.yml | head -1 | sed 's/.*://')
theirs=$(upstream_latest Vexa-ai/vexa)
note "transcription-service: we build $ours, Vexa released ${theirs:-?}; bumped only for transcription fixes (docs/runbooks/transcription-service-bump.md)"

exit "$FAIL"
