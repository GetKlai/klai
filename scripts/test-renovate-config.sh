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
assert_line "$config" \
  "  minimumReleaseAgeBehaviour: 'timestamp-optional'," \
  'an update without a release timestamp (GHCR images, lock file refresh, Git tags) must not stay pending forever'
# Without it every customManagers entry is silently skipped: the litellm-tests
# and vexa-bot regex managers never ran until this was added (2026-10-22).
assert_line "$config" \
  "    'custom.regex'," \
  'customManagers only run when custom.regex is an enabled manager'
# Docker Hub carries a Qdrant tag before the GitHub release exists (v1.20.0 on
# 2026-10-21); the version must come from the release, and the data store
# group must still catch it under that datasource.
assert_line "$config" \
  "      matchStrings: ['qdrant/qdrant:v(?<currentValue>[0-9][0-9.]*)']," \
  'Qdrant must be read from deploy/docker-compose.yml by the custom manager'
assert_line "$config" \
  "      datasourceTemplate: 'github-releases'," \
  'Qdrant must only be proposed from a published GitHub release'
assert_line "$config" \
  "      matchDatasources: ['docker', 'github-releases', 'github-tags']," \
  'a Qdrant series bump from GitHub releases must stay in the non-automerge data store group'
# Docker Hub drops every timestamp for an image with more than 1000 tags, so
# these come from GitHub (#1730: meilisearch v1.56.0 passed the age check 43
# minutes after its push). Each needs its custom manager, and its GitHub name
# must sit in the data-store rules, which match packageName, not depName.
for pkg in 'getmeili/meilisearch:v' 'image: mongo:' 'image: redis:' 'image: rabbitmq:' \
  'pgvector/pgvector:' 'grafana/grafana:' 'ollama/ollama:'; do
  if ! grep -qF "matchStrings: ['${pkg}(?<currentValue>" "$config"; then
    echo "FAIL: ${pkg%:*} must be read by a GitHub-sourced custom manager (Docker Hub drops its timestamps)" >&2
    exit 1
  fi
done
for name in 'mongodb/mongo' 'redis/redis' 'rabbitmq/rabbitmq-server' 'meilisearch/meilisearch'; do
  if [ "$(grep -cF "        '${name}'," "$config")" -lt 3 ]; then
    echo "FAIL: ${name} must be listed in the data-store group, the major rule and the strict rule" >&2
    exit 1
  fi
done
# Under the global 'flexible' a still-young version is proposed anyway, with
# only the stability check pending; 'strict' is what holds the branch back.
qdrant_rules=$(grep -A1 -F "      matchPackageNames: ['qdrant/qdrant']," "$config")
if ! printf '%s\n' "$qdrant_rules" | grep -qxF '      enabled: false,'; then
  echo 'FAIL: the Docker Hub lookup for Qdrant must be disabled' >&2
  exit 1
fi
if ! printf '%s\n' "$qdrant_rules" | grep -qxF "      internalChecksFilter: 'strict',"; then
  echo 'FAIL: Qdrant must use the strict internal checks filter' >&2
  exit 1
fi
# SPEC-INFRA-AUTO-ROLLBACK-001 S9. Tags can be moved after the fact; pins cannot.
# The preset keeps the SHAs fresh, scripts/check-action-shas.sh (Deploy-script
# tests) fails CI on a new unpinned `uses:`.
assert_line "$config" \
  "  extends: ['config:recommended', 'helpers:pinGitHubActionDigests']," \
  'GitHub Actions must stay pinned to commit SHAs, and Renovate must keep them current'
# A vulnerability fix must skip the 7-day cooldown. These are Renovate's own
# defaults; asserting them keeps a later global setting from changing them.
assert_line "$config" \
  '  osvVulnerabilityAlerts: true,' \
  'vulnerability alerts must also read the OSV database'
vuln_block=$(awk '/^  vulnerabilityAlerts: \{/{f=1} f{print} /^  \},/{f=0}' "$config")
for line in '    enabled: true,' '    minimumReleaseAge: null,' "    prCreation: 'immediate',"; do
  if ! printf '%s\n' "$vuln_block" | grep -qxF "$line"; then
    echo "FAIL: vulnerabilityAlerts must contain '${line#    }' (a security fix may not wait out the release age)" >&2
    exit 1
  fi
done
# Rules are read from the file itself: a rule is the text between two lines that
# open a 4-space object, which is how customManagers and packageRules are laid out.
if ! python3 - "$config" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
chunks = re.split(r"(?m)^    \{$", text)
fail = []

def names(chunk, key):
    m = re.search(key + r": \[(.*?)\]", chunk, re.S)
    return re.findall(r"'([^']+)'", m.group(1)) if m else []

# Majors of an action automerge only when a pull request run executes it. The
# deploy, registry, token, artifact and SARIF-upload steps run first on main (or
# only after a failure), so they stay manual.
allow = next((c for c in chunks if "matchManagers: ['github-actions']" in c and "matchUpdateTypes: ['major']" in c and "automerge: true" in c), "")
allowed = names(allow, "matchPackageNames")
if "actions/checkout" not in allowed:
    fail.append("actions/checkout majors must automerge (CI runs it on every pull request)")
for name in allowed:
    if re.match(r"(appleboy|webfactory|docker|renovatebot|aquasecurity)/|github/codeql-action|actions/(create-github-app-token|github-script|upload-artifact)", name):
        fail.append(f"{name} majors must stay manual: no pull request run executes it")
# Python dev groups: a klai-libs/* group is installed by no workflow, so a major
# there would merge without a test having run it.
py = next((c for c in chunks if "matchManagers: ['pep621']" in c and "matchUpdateTypes: ['major']" in c and "automerge: true" in c), "")
if "!klai-libs/**" not in names(py, "matchFileNames"):
    fail.append("pep621 dev majors must exclude klai-libs/** (no workflow runs a lib's own dev group)")
dev = next((c for c in chunks if "matchDepTypes: ['devDependencies']" in c), "")
if "automerge: true" not in dev or "matchUpdateTypes: ['major']" not in dev:
    fail.append("npm devDependencies majors must automerge")
files = names(dev, "matchFileNames")
if not files or any(not f.startswith(("klai-portal/frontend/", "klai-docs/", "klai-widget/")) for f in files):
    fail.append("npm dev automerge is limited to the packages with a CI job")

# A custom manager that reads a data store from GitHub changes the datasource
# and sometimes the packageName. A version ceiling written for the Docker Hub
# name and datasource then matches nothing (#2085: mongo '<8.1' went dead).
moved = {}
for c in chunks:
    if "customType: 'regex'" not in c:
        continue
    dep = re.search(r"depNameTemplate: '([^']+)'", c)
    ds = re.search(r"datasourceTemplate: '([^']+)'", c)
    if not dep or not ds or ds.group(1) in ("docker", "pypi", "npm"):
        continue
    pkg = re.search(r"packageNameTemplate: '([^']+)'", c)
    moved[dep.group(1)] = (pkg.group(1) if pkg else dep.group(1), ds.group(1))
checked = 0
for c in chunks:
    if "allowedVersions:" not in c:
        continue
    listed = names(c, "matchPackageNames")
    datasources = names(c, "matchDatasources")
    for dep, (pkg, ds) in moved.items():
        if dep not in listed and pkg not in listed:
            continue
        checked += 1
        if dep not in listed or pkg not in listed:
            fail.append(f"ceiling for {dep} must list both '{dep}' and '{pkg}'")
        if datasources and ds not in datasources:
            fail.append(f"ceiling for {dep} must include datasource '{ds}'")
if "mongo" not in moved or checked == 0:
    fail.append("the mongo ceiling must be found and checked against the GitHub-sourced mongo manager")
mongo = next((c for c in chunks if "allowedVersions: '<8.1'" in c), "")
if not all(n in names(mongo, "matchPackageNames") for n in ("mongo", "mongodb/mongo")):
    fail.append("the mongo '<8.1' ceiling must match both 'mongo' and 'mongodb/mongo'")
for f in fail:
    print("FAIL: " + f, file=sys.stderr)
sys.exit(1 if fail else 0)
PY
then
  exit 1
fi
assert_line "$config" \
  "    npm: '^11'," \
  'lock files must be generated with the npm major that CI installs them with'
assert_contains "$config" \
  "internalChecksFilter: 'flexible'" \
  'a group must not wait for its most frequently released member to age'
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

# SPEC-INFRA-AUTO-ROLLBACK-001 S5: after a compose rollback of a Renovate bump,
# scripts/renovate-revert-after-rollback.py excludes the exact version above this
# anchor. Without the anchor the revert PR cannot be made; a synthetic entry must
# land inside packageRules and exclude that version only, so the next one flows.
assert_line "$config" \
  '    // End of the rolled-back versions: scripts/renovate-revert-after-rollback.py inserts above this line.' \
  'the rolled-back versions anchor must stay in packageRules'
if ! python3 - "$repo_root" <<'PY'
import importlib.util, re, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("revert", root / "scripts/renovate-revert-after-rollback.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
out = mod.add_exclusion((root / "renovate.json5").read_text(), "example/synthetic", "v9.9.9", "synthetic")
entry, anchor = out.index("matchDepNames: ['example/synthetic']"), out.index(mod.ANCHOR)
raw = re.search(r"allowedVersions: '!/(.*)/',", out[entry:]).group(1).replace("\\\\", "\\")
ok = entry < anchor < out.index("\n  ],\n", anchor) and re.match(raw, "v9.9.9") and not re.match(raw, "v9.9.10")
sys.exit(0 if ok else 1)
PY
then
  echo 'FAIL: a rolled-back version must be excluded exactly, inside packageRules, above the anchor' >&2
  exit 1
fi

echo 'Renovate configuration contracts: PASS'
