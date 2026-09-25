#!/usr/bin/env bash
# check-version-drift.sh — what Renovate cannot update, or updates nobody merges.
#
# Renovate keeps the pinned images and packages it can see current, but two
# kinds of drift escape it. Pull requests it opens and nobody merges: on
# 2026-09-25 six had been open for up to five weeks, the oldest since
# 2026-08-22. And images Klai builds from upstream source, which no registry
# lookup can follow. This fails, so the weekly run mails, until someone acts.
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

ours=$(grep -oE 'vexaai/transcription-service:[0-9.]+' deploy/docker-compose.gpu.yml | head -1 | sed 's/.*://')
theirs=$(upstream_latest Vexa-ai/vexa)
note "transcription-service: we build $ours, Vexa released ${theirs:-?}; bumped only for transcription fixes (docs/runbooks/transcription-service-bump.md)"

exit "$FAIL"
