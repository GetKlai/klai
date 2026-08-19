#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
workflow="$repo_root/.github/workflows/renovate.yml"
config="$repo_root/renovate.json5"
docs_workflow="$repo_root/.github/workflows/docs.yml"
portal_frontend_workflow="$repo_root/.github/workflows/portal-frontend.yml"
trivyignore_workflow="$repo_root/.github/workflows/validate-trivyignore.yml"

assert_contains() {
  file=$1
  expected=$2
  message=$3

  if ! grep -Fq "$expected" "$file"; then
    echo "FAIL: $message" >&2
    exit 1
  fi
}

assert_contains "$workflow" \
  "RENOVATE_X_GITHUB_HOST_RULES: 'true'" \
  'Renovate must pass its GitHub App token to GitHub Packages lookups'

assert_contains "$config" \
  "matchPackageNames: ['ghcr.io/getklai/librechat']" \
  'the digest-only Klai LibreChat image must have an explicit Renovate policy'
assert_contains "$config" \
  "matchPackageNames: ['ghcr.io/getklai/crawl4ai']" \
  'the host-built crawl4ai image must not be queried as a published GHCR package'
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

# Main requires a GitHub Actions check named `quality`. Every Renovate update
# that is eligible for automerge must therefore trigger a real validation job
# with that exact name; otherwise the app can never merge an otherwise-green
# PR. These paths cover the package/workflow files used by the current
# automerge branches without adding a no-op check that could mask failing CI.
assert_contains "$docs_workflow" \
  '  quality:' \
  'docs dependency updates must publish the required quality check'
assert_contains "$docs_workflow" \
  '    needs: quality' \
  'docs build-and-push must remain gated by the renamed quality job'
assert_contains "$portal_frontend_workflow" \
  "      - 'klai-widget/package-lock.json'" \
  'widget lockfile updates must trigger portal quality checks'
assert_contains "$portal_frontend_workflow" \
  "      - 'klai-portal/.github/workflows/portal-frontend.yml'" \
  'nested portal workflow updates must trigger portal quality checks'
assert_contains "$trivyignore_workflow" \
  '  quality:' \
  'Trivy workflow dependency updates must publish the required quality check'
assert_contains "$trivyignore_workflow" \
  '    name: quality' \
  'the Trivy validator check name must match branch protection'

echo 'Renovate configuration contracts: PASS'
