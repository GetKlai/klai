#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
workflow="$repo_root/.github/workflows/renovate.yml"
config="$repo_root/renovate.json5"

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

echo 'Renovate configuration contracts: PASS'
