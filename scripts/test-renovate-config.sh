#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
workflow="$repo_root/.github/workflows/renovate.yml"
config="$repo_root/renovate.json5"
docs_workflow="$repo_root/.github/workflows/docs.yml"
quality_workflow="$repo_root/.github/workflows/quality.yml"

assert_contains() {
  file=$1
  expected=$2
  message=$3

  if ! grep -Fq "$expected" "$file"; then
    echo "FAIL: $message" >&2
    exit 1
  fi
}

# Whole-line variant. `assert_contains` matches substrings, so an assertion on
# `    name: quality` silently kept passing after the job was renamed to
# `quality-trivyignore` — the check survived the very rename it existed to
# catch. Use this wherever the exact identifier is the point.
assert_line() {
  file=$1
  expected=$2
  message=$3

  if ! grep -qxF "$expected" "$file"; then
    echo "FAIL: $message" >&2
    exit 1
  fi
}

assert_contains "$workflow" \
  "RENOVATE_X_GITHUB_HOST_RULES: 'true'" \
  'Renovate must pass its GitHub App token to GitHub Packages lookups'

# Renovate checks a newly written branch before GitHub Actions has necessarily
# published its check-runs. Its GitHub check-run cache is process-local, so a
# fresh Renovate process after the required workflow completes is the safe
# nudge: failed quality still blocks the merge, while successful quality gets
# observed without waiting for (or racing) the next cron run.
assert_line "$quality_workflow" \
  'name: Required quality' \
  'the quality workflow name must stay aligned with the Renovate completion trigger'
assert_line "$workflow" \
  '  workflow_run:' \
  'Renovate must be nudged after the required quality workflow completes'
assert_line "$workflow" \
  "    workflows: ['Required quality']" \
  'the Renovate nudge must follow the required quality workflow'
assert_line "$workflow" \
  '    types: [completed]' \
  'the Renovate nudge must wait until the required quality workflow is complete'
assert_line "$workflow" \
  "    branches: ['renovate/**']" \
  'quality completions must only nudge Renovate for Renovate branches'
assert_line "$workflow" \
  '  group: renovate-automerge' \
  'Renovate runs must be serialized so completion nudges cannot race each other'
assert_line "$workflow" \
  '  cancel-in-progress: false' \
  'a completion nudge must not interrupt a Renovate run that is already updating branches'
assert_line "$workflow" \
  "      github.event_name != 'workflow_run' ||" \
  'scheduled and manual Renovate runs must remain enabled'
assert_line "$workflow" \
  '      github.event.workflow_run.head_repository.full_name == github.repository' \
  'fork quality runs must not trigger a privileged Renovate nudge'

assert_contains "$config" \
  "matchPackageNames: ['ghcr.io/getklai/librechat']" \
  'the digest-only Klai LibreChat image must have an explicit Renovate policy'
assert_contains "$config" \
  "matchPackageNames: ['ghcr.io/getklai/crawl4ai']" \
  'the host-built crawl4ai image must not be queried as a published GHCR package'
assert_contains "$config" \
  "matchPackageNames: ['ghcr.io/getklai/presidio-analyzer']" \
  'the digest-only Klai presidio-analyzer image must have an explicit Renovate policy'
assert_contains "$config" \
  "matchPackageNames: ['aquasecurity/setup-trivy']" \
  'setup-trivy must have an IP-allowlist-safe lookup policy'
assert_contains "$config" \
  "overrideDatasource: 'git-tags'" \
  'Aqua dependencies must bypass the authenticated GitHub API'
assert_contains "$config" \
  "overridePackageName: 'https://github.com/aquasecurity/setup-trivy.git'" \
  'setup-trivy must use its public Git repository for lookups'
assert_contains "$config" \
  "matchPackageNames: ['aquasecurity/trivy']" \
  'Trivy must have an IP-allowlist-safe lookup policy'
assert_contains "$config" \
  "overridePackageName: 'https://github.com/aquasecurity/trivy.git'" \
  'Trivy must use its public Git repository for lookups'
assert_contains "$config" \
  "minimumReleaseAge: '0 days'" \
  'Aqua Git-tag lookups without timestamps must not remain pending forever'
assert_contains "$config" \
  "rebaseWhen: 'conflicted'" \
  'automerge updates must not invalidate green CI merely because main advanced'
rebase_when_conflicted_count=$(grep -Fc "rebaseWhen: 'conflicted'" "$config")
if [ "$rebase_when_conflicted_count" -ne 2 ]; then
  echo 'FAIL: lockfile and patch/minor automerge must both rebase only on conflicts' >&2
  exit 1
fi

# Renovate defaults prConcurrentLimit to 10. On 2026-08-22 ten unmergeable PRs
# filled every slot and Renovate stopped opening branches entirely — including
# the lock file refresh that exists precisely because silence had gone unnoticed
# for 28 weeks before. Deleting this line restores that default, and the symptom
# is a Renovate run that succeeds while doing nothing. prHourlyLimit stays at its
# default and is what actually paces a large backlog.
assert_line "$config" \
  '  prConcurrentLimit: 0,' \
  'the concurrent PR limit must stay unlimited, or PRs awaiting review can starve the queue'

# Main requires a GitHub Actions check named `quality`. Since #1113 that context
# comes from ONE unfiltered workflow: quality.yml runs on every PR, selects the
# affected reusable workflows, and aggregates their results. It replaced the
# older shape where each service workflow published its own `quality` job.
#
# The assertions below pin the current arrangement. They previously pinned the
# old one and went stale the day it changed: one failed loudly, two passed on
# substrings of the renamed jobs, and two checked `push:` path lists that no
# longer decide anything at PR time. A red gate that has to be read to be
# believed is worse than no gate, so they are re-pointed rather than deleted.

# The aggregator must stay unfiltered. A `paths:` filter here is exactly the bug
# #1113 fixed: on a PR matching no filter the required context never reports at
# all, and the PR can never merge — which is the failure mode this whole file
# exists to prevent.
quality_on_block=$(awk '/^on:/{f=1;next} /^[a-z]/{f=0} f' "$quality_workflow")
if ! printf '%s\n' "$quality_on_block" | grep -q '^  pull_request:'; then
  echo 'FAIL: quality.yml must trigger on pull_request, or the required check never reports on a PR at all' >&2
  exit 1
fi
# Both spellings and both YAML styles, deliberately. `paths-ignore:` does not
# contain the substring `paths:`, so matching on that alone let the exclusive
# form through — and excluding a directory is the more plausible edit of the
# two. Anchoring to the start of a line instead then missed the inline flow
# mapping (`pull_request: { paths: [...] }`), which is valid YAML and which the
# original substring check did catch. A word-boundary match covers all four.
if printf '%s\n' "$quality_on_block" | grep -qE '\bpaths(-ignore)?:'; then
  echo 'FAIL: quality.yml must stay unfiltered — a paths:/paths-ignore: filter means the required check cannot report on an unmatched PR' >&2
  exit 1
fi

assert_line "$quality_workflow" \
  '  quality:' \
  'quality.yml must define the job that carries the required context'
assert_line "$quality_workflow" \
  '    name: quality' \
  'the aggregator check name must match branch protection exactly'
assert_line "$quality_workflow" \
  '    if: always()' \
  'the aggregator must report even when a selected workflow was skipped or failed'

# Aggregating is what stops this from being the "no-op check that masks failing
# CI" the previous version of this comment warned about: the automerge-eligible
# branches must each be something the required check waits for.
for needed in docs portal-frontend trivyignore; do
  assert_line "$quality_workflow" \
    "      - $needed" \
    "the aggregator must wait for the $needed workflow, or its failures cannot block automerge"
done

# ...and the paths a Renovate automerge actually touches must select that work.
assert_contains "$quality_workflow" \
  "              - 'klai-docs/**'" \
  'docs dependency updates must select the docs workflow'
assert_contains "$quality_workflow" \
  "              - 'klai-widget/**'" \
  'every widget build input must select the portal-frontend workflow'
assert_contains "$quality_workflow" \
  "              - '.github/workflows/validate-trivyignore.yml'" \
  'Trivy workflow updates must select the trivyignore validator'

# The reusable workflows keep their own internal gating; only the check NAME
# moved. Pinned as a whole line so a future rename fails here instead of
# passing on the prefix.
assert_line "$docs_workflow" \
  '    needs: quality-docs' \
  'docs build-and-push must remain gated by its own quality job'

echo 'Renovate configuration contracts: PASS'
