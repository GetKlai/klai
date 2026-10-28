#!/bin/sh
# Fails on any third-party `uses:` in .github/workflows that is not pinned to a
# 40-character commit SHA. A tag can be moved after the fact; a SHA cannot.
# Local actions and reusable workflows (./...) and docker:// actions are exempt.
# Fix a failure with scripts/pin-action-shas.sh (SPEC-INFRA-AUTO-ROLLBACK-001 S9).
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

unpinned=$(grep -nE '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*[^[:space:]#]+' \
    "$repo_root"/.github/workflows/*.yml |
  grep -vE 'uses:[[:space:]]*(\./|docker://)' |
  grep -vE 'uses:[[:space:]]*[^[:space:]#@]+@[0-9a-f]{40}([[:space:]]|$)' || true)

count=0
[ -z "$unpinned" ] || count=$(printf '%s\n' "$unpinned" | wc -l | tr -d ' ')
echo "uses: references without a commit SHA: $count"
if [ "$count" -ne 0 ]; then
  printf '%s\n' "$unpinned" >&2
  echo 'FAIL: pin them with scripts/pin-action-shas.sh' >&2
  exit 1
fi
