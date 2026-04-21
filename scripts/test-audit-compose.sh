#!/usr/bin/env bash
# test-audit-compose.sh — regression test for scripts/audit-compose-volumes.sh.
#
# Two fixtures:
#   1. Baseline (the repo's real compose + inventory): audit MUST pass.
#   2. Re-introduce the 2026-04-19 FalkorDB bug (change the falkordb bind back
#      to /data): audit MUST fail.
#
# This guarantees the audit actually detects the class of bug it exists to
# prevent — not just happy-path verification.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

fixture=$(mktemp -d)
trap 'rm -rf "${fixture}"' EXIT

mkdir -p "${fixture}/deploy" "${fixture}/scripts"
cp "${REPO_ROOT}/deploy/docker-compose.yml" "${fixture}/deploy/"
cp "${REPO_ROOT}/deploy/volume-mounts.yaml"  "${fixture}/deploy/"
cp "${REPO_ROOT}/scripts/audit-compose-volumes.sh" "${fixture}/scripts/"
chmod +x "${fixture}/scripts/audit-compose-volumes.sh"

echo ""
echo "=== Test 1: baseline — real compose + inventory should pass ==="
if (cd "${fixture}" && bash scripts/audit-compose-volumes.sh >/dev/null 2>&1); then
  echo "PASS — audit exited 0 on the baseline"
else
  echo "FAIL — audit rejected the baseline (exit non-zero)" >&2
  (cd "${fixture}" && bash scripts/audit-compose-volumes.sh 2>&1 | tail -20) >&2 || true
  exit 1
fi

echo ""
echo "=== Test 2: 2026-04-19 regression — falkordb bind back to /data must fail ==="
# Re-introduce the original bug by editing the compose fixture in place.
sed -i \
  -e 's|/opt/klai/falkordb-data:/var/lib/falkordb/data|/opt/klai/falkordb-data:/data|' \
  "${fixture}/deploy/docker-compose.yml"

# Sanity check — the edit must actually have landed.
if ! grep -q '/opt/klai/falkordb-data:/data$' "${fixture}/deploy/docker-compose.yml"; then
  echo "FAIL — sed did not introduce the regression pattern; test can't run" >&2
  exit 2
fi

if (cd "${fixture}" && bash scripts/audit-compose-volumes.sh >/dev/null 2>&1); then
  echo "FAIL — audit did NOT reject the regression (the original bug would slip through)" >&2
  exit 1
else
  echo "PASS — audit rejected the regression (exit non-zero, as required)"
fi

echo ""
echo "test-audit-compose: all scenarios behaved as expected."
