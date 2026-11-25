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
#   5-9. Backup coverage (Part C). The baseline already holds entries that are
#      backed up by a step backup.sh runs and entries skipped with a reason,
#      and passes. These must fail: the password manager back at
#      `backup: skip` without a reason (its state until 2026-11-24), a skip
#      whose reason is not one that makes loss acceptable, a second consumer
#      pointing at a skipped entry, an entry whose step backup.sh does not run,
#      and a step backup.sh runs that no entry names.
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
  mkdir -p "${dest}/deploy/scripts"
  cp "${REPO_ROOT}/deploy/scripts/backup.sh" "${dest}/deploy/scripts/"
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

# ── Scenarios 5-9: backup coverage ─────────────────────────────────────────
#
# set_field <file> <entry> <field> <value>, within one entry: `backup` is
# replaced, an empty value deletes the field, any other field is added after
# the backup: line (the fields set here do not exist in those entries yet).
set_field() {
  awk -v e="$2" -v f="$3" -v v="$4" '
    /^  [a-zA-Z0-9_-]+:$/ { inside = ($0 == "  " e ":") }
    inside && $1 == f ":" && (f == "backup" || v == "") { if (v != "") print "    " f ": " v; next }
    { print }
    inside && $1 == "backup:" && f != "backup" && v != "" { print "    " f ": " v }
  ' "$1" > "$1.new" && mv "$1.new" "$1"
}

# expect_coverage_failure <title> <fixture> <stderr pattern>
expect_coverage_failure() {
  local err
  if err="$(cd "$2" && bash scripts/audit-compose-volumes.sh 2>&1 >/dev/null)"; then
    echo "FAIL — audit accepted: $1" >&2
    failures=$((failures+1))
  elif ! grep -q -- "$3" <<< "${err}"; then
    echo "FAIL — audit failed, but not on backup coverage ($3): $1" >&2
    echo "${err}" | tail -n 5 >&2
    failures=$((failures+1))
  else
    echo "PASS"
  fi
  rm -rf "$2"
}

echo ""
echo "=== Scenario 5: the password manager skipped without a reason must fail ==="
fx=$(mktemp -d); setup_fixture "${fx}"
set_field "${fx}/deploy/volume-mounts.yaml" vaultwarden backup skip
set_field "${fx}/deploy/volume-mounts.yaml" vaultwarden backup_step ""
expect_coverage_failure "vaultwarden at backup: skip without skip_reason" "${fx}" "vaultwarden: backup: skip needs a skip_reason"

echo ""
echo "=== Scenario 6: a skip whose reason does not make loss acceptable must fail ==="
fx=$(mktemp -d); setup_fixture "${fx}"
set_field "${fx}/deploy/volume-mounts.yaml" calnode backup skip
set_field "${fx}/deploy/volume-mounts.yaml" calnode skip_reason "not-yet-archived"
expect_coverage_failure "calnode skipped as not-yet-archived" "${fx}" "calnode: backup: skip needs a skip_reason"

echo ""
echo "=== Scenario 7: a second consumer pointing at a skipped entry must fail ==="
fx=$(mktemp -d); setup_fixture "${fx}"
set_field "${fx}/deploy/volume-mounts.yaml" crm-storage backup skip
set_field "${fx}/deploy/volume-mounts.yaml" crm-storage skip_reason "same-volume-as:crm-storage-worker"
set_field "${fx}/deploy/volume-mounts.yaml" crm-storage-worker backup skip
set_field "${fx}/deploy/volume-mounts.yaml" crm-storage-worker skip_reason "same-volume-as:crm-storage"
expect_coverage_failure "crm-storage and its worker each pointing at the other" "${fx}" "crm-storage: skip_reason same-volume-as:crm-storage-worker must name"

echo ""
echo "=== Scenario 8: an entry whose backup_step backup.sh does not run must fail ==="
fx=$(mktemp -d); setup_fixture "${fx}"
grep -v 'run_step "Vaultwarden: ' "${fx}/deploy/scripts/backup.sh" > "${fx}/backup.sh.new"
mv "${fx}/backup.sh.new" "${fx}/deploy/scripts/backup.sh"
expect_coverage_failure "vaultwarden named, its step not run" "${fx}" "vaultwarden: backup_step backup_vaultwarden is not run"

echo ""
echo "=== Scenario 9: a step backup.sh runs that no entry names must fail ==="
fx=$(mktemp -d); setup_fixture "${fx}"
set_field "${fx}/deploy/volume-mounts.yaml" gitea backup missing
set_field "${fx}/deploy/volume-mounts.yaml" gitea backup_step ""
set_field "${fx}/deploy/volume-mounts.yaml" gitea gap "a gap"
expect_coverage_failure "backup_gitea runs, no entry names it" "${fx}" "runs backup_gitea, but no inventory entry names it"

echo ""
echo "=== Scenario 10: a run_step call the audit cannot read must fail ==="
fx=$(mktemp -d); setup_fixture "${fx}"
printf '  run_step "Extra: tar" \\\n    backup_extra\n' >> "${fx}/deploy/scripts/backup.sh"
expect_coverage_failure "a run_step call split over two lines" "${fx}" "could be read"

echo ""
if [[ "${failures}" -gt 0 ]]; then
  echo "test-audit-compose: ${failures} scenario(s) failed." >&2
  exit 1
fi
echo "test-audit-compose: all 10 scenarios behaved as expected."
