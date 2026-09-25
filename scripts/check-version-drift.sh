#!/usr/bin/env bash
# check-version-drift.sh — what Renovate cannot update, or updates nobody merges.
#
# Renovate keeps the pinned images and packages it can see current, but two
# kinds of drift escape it. Pull requests it opens and nobody merges: on
# 2026-09-25 six had been open for up to five weeks, the oldest since
# 2026-08-22. And images Klai builds from upstream source, which no registry
# lookup can follow (crawl4ai, and Presidio and LibreChat, which are pinned by
# digest or rebuilt from an upstream tag). This fails, so the weekly run
# mails, until someone acts.
#
# Env: GH_TOKEN with read access to REPOS; MAX_PR_AGE_DAYS (default 14).
set -uo pipefail
REPOS="${REPOS:-GetKlai/klai GetKlai/klai-website}"
MAX_PR_AGE_DAYS="${MAX_PR_AGE_DAYS:-14}"
FAIL=0
fail() { printf 'FAIL: %s\n' "$*"; FAIL=1; }
ok()   { printf 'OK:   %s\n' "$*"; }
note() { printf 'NOTE: %s\n' "$*"; }

cutoff=$(date -u -d "-${MAX_PR_AGE_DAYS} days" +%Y-%m-%dT%H:%M:%SZ)
for repo in $REPOS; do
  stale=$(gh pr list -R "$repo" --state open --limit 200 --json number,title,createdAt,author \
    --jq "[.[] | select((.author.login | test(\"renovate\")) and .createdAt < \"$cutoff\")] | .[] | \"#\(.number) \(.createdAt[0:10]) \(.title)\"")
  if [ -n "$stale" ]; then
    fail "$repo: Renovate PRs open longer than ${MAX_PR_AGE_DAYS} days:"
    printf '        %s\n' "$stale"
  else
    ok "$repo: no Renovate PR older than ${MAX_PR_AGE_DAYS} days"
  fi
done

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
