#!/bin/sh
# Fails on any third-party `uses:` in .github/workflows (.yml and .yaml) that is
# not pinned to a 40-character commit SHA followed by a `# <tag>` comment. A tag
# can be moved after the fact; a SHA cannot. The comment is not decoration:
# Renovate's github-actions manager reads it to know which release the SHA stands
# for, and a bare SHA silently stops receiving updates. Local actions and reusable workflows (./...) and docker:// actions are exempt.
# Fix a failure with scripts/pin-action-shas.sh (SPEC-INFRA-AUTO-ROLLBACK-001 S9).
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

unpinned=$(find "$repo_root/.github/workflows" \( -name '*.yml' -o -name '*.yaml' \) -print0 |
  xargs -0 grep -nE '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*[^[:space:]#]+' |
  grep -vE 'uses:[[:space:]]*(\./|docker://)' |
  grep -vE 'uses:[[:space:]]*[^[:space:]#@]+@[0-9a-f]{40}[[:space:]]+#[[:space:]]*[^[:space:]]' || true)

count=0
[ -z "$unpinned" ] || count=$(printf '%s\n' "$unpinned" | wc -l | tr -d ' ')
echo "uses: references without a commit SHA: $count"
if [ "$count" -ne 0 ]; then
  printf '%s\n' "$unpinned" >&2
  echo 'FAIL: every uses: needs @<40-char sha> followed by a # <tag> comment (Renovate reads the comment to keep the pin current); scripts/pin-action-shas.sh pins a tag; a bare SHA gets its # <tag> comment by hand' >&2
  exit 1
fi
