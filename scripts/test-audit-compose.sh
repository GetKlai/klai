#!/usr/bin/env bash
# test-audit-compose.sh — regression test for scripts/audit-compose-volumes.sh.
#
# Scenarios:
#   1. Baseline: real compose + inventory → audit MUST pass.
#   2. 2026-04-19 regression: falkordb bind back to /data → audit MUST fail.
#   3. Missing inventory entry: remove an entry for an existing mount → audit
#      MUST fail (Part A catches it).
#   4. Generic image mismatch: edit a data entry's container_path to a bogus
#      value unrelated to falkordb → audit MUST fail (Part B catches it).
#
# The point of scenarios 3 and 4 is to prove that the audit catches the CLASS
# of bug — not just the one specific pattern we hit on 2026-04-19.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

setup_fixture() {
  local dest="$1"
  mkdir -p "${dest}/deploy" "${dest}/scripts"
  cp "${REPO_ROOT}/deploy/docker-compose.yml" "${dest}/deploy/"
  cp "${REPO_ROOT}/deploy/volume-mounts.yaml"  "${dest}/deploy/"
  cp "${REPO_ROOT}/scripts/audit-compose-volumes.sh" "${dest}/scripts/"
  chmod +x "${dest}/scripts/audit-compose-volumes.sh"
}

run_audit() {
  # Returns the audit's exit code; hides stdout/stderr unless --show.
  local fx="$1" show="${2:-}"
  if [[ "${show}" == "--show" ]]; then
    (cd "${fx}" && bash scripts/audit-compose-volumes.sh)
  else
    (cd "${fx}" && bash scripts/audit-compose-volumes.sh >/dev/null 2>&1)
  fi
}

failures=0

# ── Scenario 1: baseline ────────────────────────────────────────────────────
echo ""
echo "=== Scenario 1: baseline — real compose + inventory should pass ==="
fx1=$(mktemp -d); setup_fixture "${fx1}"
if run_audit "${fx1}"; then
  echo "PASS"
else
  echo "FAIL — audit rejected the baseline" >&2
  run_audit "${fx1}" --show >&2 || true
  failures=$((failures+1))
fi
rm -rf "${fx1}"

# ── Scenario 2: 2026-04-19 falkordb regression ─────────────────────────────
echo ""
echo "=== Scenario 2: falkordb bind back to /data must fail ==="
fx2=$(mktemp -d); setup_fixture "${fx2}"
sed -i \
  -e 's|/opt/klai/falkordb-data:/var/lib/falkordb/data|/opt/klai/falkordb-data:/data|' \
  "${fx2}/deploy/docker-compose.yml"
if ! grep -q '/opt/klai/falkordb-data:/data$' "${fx2}/deploy/docker-compose.yml"; then
  echo "SETUP-FAIL — sed did not land the regression pattern" >&2
  failures=$((failures+1))
elif run_audit "${fx2}"; then
  echo "FAIL — audit did NOT reject the 2026-04-19 regression" >&2
  failures=$((failures+1))
else
  echo "PASS"
fi
rm -rf "${fx2}"

# ── Scenario 3: missing inventory entry ─────────────────────────────────────
#
# Pick one inventory entry that maps 1:1 to a real compose mount and strip
# it. Audit must flag Part A (MISSING from volume-mounts.yaml). We use
# `firecrawl-postgres` because it's a leaf, no aliases, no shared volume.
echo ""
echo "=== Scenario 3: missing inventory entry must fail ==="
fx3=$(mktemp -d); setup_fixture "${fx3}"
# Delete the `firecrawl-postgres:` block (top-level under mounts:) using awk —
# portable, no yq dependency at test-runtime. Block ends at the next
# two-space-indented entry key or end-of-file.
awk '
  BEGIN { skipping = 0 }
  /^  firecrawl-postgres:/ { skipping = 1; next }
  skipping && /^  [a-zA-Z0-9_-]+:/ { skipping = 0 }
  !skipping { print }
' "${fx3}/deploy/volume-mounts.yaml" > "${fx3}/deploy/volume-mounts.yaml.new"
mv "${fx3}/deploy/volume-mounts.yaml.new" "${fx3}/deploy/volume-mounts.yaml"
if grep -q '^  firecrawl-postgres:' "${fx3}/deploy/volume-mounts.yaml"; then
  echo "SETUP-FAIL — awk did not remove the entry" >&2
  failures=$((failures+1))
elif run_audit "${fx3}"; then
  echo "FAIL — audit did NOT reject a missing inventory entry" >&2
  failures=$((failures+1))
else
  echo "PASS"
fi
rm -rf "${fx3}"

# ── Scenario 4: generic image mismatch ──────────────────────────────────────
#
# Change one data-entry's container_path to something the image does not
# write to. Must trigger Part B (MISMATCH). Uses `mongodb` to prove the
# audit generalises beyond falkordb.
echo ""
echo "=== Scenario 4: generic data-path mismatch (not falkordb) must fail ==="
fx4=$(mktemp -d); setup_fixture "${fx4}"
# Replace mongodb's container_path /data/db with /nonsense/not-a-db. This
# still refers to a 'data' category entry so Part B checks it.
sed -i \
  -e '/^  mongodb:$/,/^  [a-zA-Z0-9_-]\+:$/ s|container_path: /data/db|container_path: /nonsense/not-a-db|' \
  "${fx4}/deploy/volume-mounts.yaml"
if ! grep -q 'container_path: /nonsense/not-a-db' "${fx4}/deploy/volume-mounts.yaml"; then
  echo "SETUP-FAIL — sed did not land the mismatch pattern" >&2
  failures=$((failures+1))
elif run_audit "${fx4}"; then
  echo "FAIL — audit did NOT reject a generic image-path mismatch" >&2
  failures=$((failures+1))
else
  echo "PASS"
fi
rm -rf "${fx4}"

echo ""
if [[ "${failures}" -gt 0 ]]; then
  echo "test-audit-compose: ${failures} scenario(s) failed." >&2
  exit 1
fi
echo "test-audit-compose: all 4 scenarios behaved as expected."
