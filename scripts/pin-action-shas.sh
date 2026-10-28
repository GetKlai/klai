#!/usr/bin/env bash
# Pin every third-party `uses:` in .github/workflows to a 40-character commit
# SHA with the release it stands for in a trailing comment:
#
#   uses: actions/checkout@v7   ->   uses: actions/checkout@<sha>  # v7.0.1
#
# A tag can be moved after the fact (tj-actions/changed-files, March 2025); a
# commit SHA cannot. helpers:pinGitHubActionDigests in renovate.json5 keeps the
# pins fresh, this script does the one-off migration and pins any reference a
# person adds by hand. Re-running is safe: a reference that already carries a
# SHA is left alone, so a second run changes nothing.
#
# Not touched: local actions and reusable workflows (./...), docker:// actions,
# and aquasecurity/*, whose SHAs and git-tags datasource override are owned by
# the Aqua rules in renovate.json5 (Aqua blocks GitHub App tokens).
#
# An annotated tag is followed down to the commit it points at; a pin on the tag
# object itself is not what `uses:` resolves. A floating tag (v7) is labelled
# with the newest vX.Y.Z tag on the same commit, so the comment names a real
# release. Needs gh (authenticated), jq and perl.
#
# Usage: scripts/pin-action-shas.sh [workflow.yml ...]   (default: all workflows)
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
shopt -s nullglob
if [ "$#" -gt 0 ]; then files=("$@"); else files=("$repo_root"/.github/workflows/*.y{,a}ml); fi

# Prints the commit SHA a tag points at, following annotated tag objects.
tag_commit() {
  local repo=$1 tag=$2 json type sha
  json=$(gh api "repos/$repo/git/ref/tags/$tag")
  type=$(jq -r .object.type <<<"$json"); sha=$(jq -r .object.sha <<<"$json")
  while [ "$type" = tag ]; do
    json=$(gh api "repos/$repo/git/tags/$sha")
    type=$(jq -r .object.type <<<"$json"); sha=$(jq -r .object.sha <<<"$json")
  done
  echo "$sha"
}

# Prints the release label for a tag: the tag itself when it is already vX.Y.Z,
# else the newest vX.Y.Z tag that points at the same commit, else the tag.
release_label() {
  local repo=$1 tag=$2 commit=$3 prefix cand
  if [[ $tag =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "$tag"; return; fi
  prefix=${tag%%.*}
  while read -r cand; do
    [ -n "$cand" ] || continue
    if [ "$(tag_commit "$repo" "$cand")" = "$commit" ]; then echo "$cand"; return; fi
  done < <(gh api --paginate "repos/$repo/git/matching-refs/tags/$prefix." --jq '.[].ref' |
    sed 's#^refs/tags/##' | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | sort -t. -k1,1Vr -k2,2nr -k3,3nr)
  echo "$tag"
}

refs=$(grep -hoE '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*[^[:space:]#]+' "${files[@]}" |
  sed -E 's/^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*//' |
  grep -vE '^(\./|docker://|aquasecurity/)' | grep -vE '@[0-9a-f]{40}$' | sort -u || true)

[ -n "$refs" ] || { echo "pin-action-shas: nothing to pin"; exit 0; }

while read -r ref; do
  action=${ref%@*}; tag=${ref#*@}
  repo=$(cut -d/ -f1,2 <<<"$action")
  commit=$(tag_commit "$repo" "$tag")
  label=$(release_label "$repo" "$tag" "$commit")
  echo "pin $action@$tag -> ${commit:0:12}  # $label"
  # The lookahead keeps a longer tag (v7 vs v7.1) from matching as a prefix.
  OLD="$ref" NEW="$action@$commit  # $label" \
    perl -pi -e 's/(uses:\s*)\Q$ENV{OLD}\E(?=\s*(#.*)?$)/$1$ENV{NEW}/ and s/(  # [^ ]+)\s*#.*$/$1/' "${files[@]}"
done <<<"$refs"
