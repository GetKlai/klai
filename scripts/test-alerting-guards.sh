#!/usr/bin/env bash
# test-alerting-guards.sh — regression tests for:
#   - scripts/audit-alert-secrets.sh
#   - scripts/verify-alert-runbooks.sh
#
# Proves both scripts catch the CLASS of bug they're designed for, not just
# the one specific pattern. Mirrors the SEC-024 pattern set by
# scripts/test-audit-compose.sh. Runs in CI via alerting-check.yml.
#
# Fails the suite if:
#   - Either script passes a deliberately-bad fixture (false negative)
#   - Either script fails a deliberately-good fixture (false positive)
#   - A script exits with an unexpected code

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

FIXTURE_ROOT="$(mktemp -d -t alerting-guards-XXXXXX)"
trap 'rm -rf "${FIXTURE_ROOT}"' EXIT

failures=0

write_good_runbook() {
  local dest="$1"
  mkdir -p "$(dirname "${dest}")"
  cat > "${dest}" <<'MD'
# Platform Recovery

## known-section

Known section content.

## another-section

Another one.
MD
}

write_clean_rule_yaml() {
  local dest="$1" anchor="$2"
  mkdir -p "$(dirname "${dest}")"
  cat > "${dest}" <<EOF
apiVersion: 1
groups:
  - orgId: 1
    name: clean-group
    rules:
      - uid: clean-rule
        title: clean_rule
        annotations:
          summary: 'ok'
          runbook_url: 'docs/runbooks/platform-recovery.md#${anchor}'
        labels:
          severity: warning
EOF
}

run_pass() {
  # Expect exit 0 on a clean fixture.
  # Caller passes (label, script_path, args...) — no eval, no shell reparse,
  # so paths with spaces (e.g. the repo dir under "02 - Voys") don't break.
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "[PASS] ${label}"
  else
    echo "[FAIL] ${label} — expected exit 0, got non-zero"
    failures=$((failures + 1))
  fi
}

run_fail() {
  # Expect non-zero exit on a broken fixture.
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "[FAIL] ${label} — expected non-zero exit, got 0 (script missed the bug)"
    failures=$((failures + 1))
  else
    echo "[PASS] ${label}"
  fi
}

# verify script wants to run from the fixture dir (so relative paths in
# rule YAMLs resolve). Wrap in a helper that cds first.
run_verify_pass() {
  local label="$1" fixture_dir="$2"
  if (cd "${fixture_dir}" && bash "${REPO_ROOT}/scripts/verify-alert-runbooks.sh" deploy/grafana/provisioning/alerting) >/dev/null 2>&1; then
    echo "[PASS] ${label}"
  else
    echo "[FAIL] ${label} — expected exit 0, got non-zero"
    failures=$((failures + 1))
  fi
}

run_verify_fail() {
  local label="$1" fixture_dir="$2"
  if (cd "${fixture_dir}" && bash "${REPO_ROOT}/scripts/verify-alert-runbooks.sh" deploy/grafana/provisioning/alerting) >/dev/null 2>&1; then
    echo "[FAIL] ${label} — expected non-zero exit, got 0"
    failures=$((failures + 1))
  else
    echo "[PASS] ${label}"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
# audit-alert-secrets.sh
# ═══════════════════════════════════════════════════════════════════════════

# --- Scenario A1: clean fixture (only env-var refs) → should PASS -----------
F="${FIXTURE_ROOT}/audit-a1-clean/alerting"
mkdir -p "${F}"
cat > "${F}/contact-points.yaml" <<'YAML'
apiVersion: 1
contactPoints:
  - name: test
    receivers:
      - type: email
        settings:
          addresses: ${ALERTS_EMAIL_RECIPIENTS}
          password: ${SMTP_PASSWORD}
YAML
run_pass "audit-A1: clean fixture with \${VAR} refs" \
  bash "${REPO_ROOT}/scripts/audit-alert-secrets.sh" "${FIXTURE_ROOT}/audit-a1-clean/alerting"

# --- Scenario A2: literal Slack webhook → should FAIL -----------------------
F="${FIXTURE_ROOT}/audit-a2-slack/alerting"
mkdir -p "${F}"
cat > "${F}/contact-points.yaml" <<'YAML'
apiVersion: 1
contactPoints:
  - name: bad
    receivers:
      - type: slack
        settings:
          url: https://hooks.slack.com/services/T01234/B5678/abcdefghijklmnop
YAML
run_fail "audit-A2: literal Slack webhook URL" \
  bash "${REPO_ROOT}/scripts/audit-alert-secrets.sh" "${FIXTURE_ROOT}/audit-a2-slack/alerting"

# --- Scenario A3: literal password → should FAIL ----------------------------
F="${FIXTURE_ROOT}/audit-a3-password/alerting"
mkdir -p "${F}"
cat > "${F}/bad.yaml" <<'YAML'
apiVersion: 1
contactPoints:
  - name: bad
    receivers:
      - type: email
        settings:
          password: hunter2-literal-value
YAML
run_fail "audit-A3: literal password (not \${VAR})" \
  bash "${REPO_ROOT}/scripts/audit-alert-secrets.sh" "${FIXTURE_ROOT}/audit-a3-password/alerting"

# --- Scenario A4: literal Bearer token → should FAIL ------------------------
F="${FIXTURE_ROOT}/audit-a4-bearer/alerting"
mkdir -p "${F}"
cat > "${F}/bad.yaml" <<'YAML'
apiVersion: 1
contactPoints:
  - name: bad
    receivers:
      - settings:
          authorization: "Bearer sk-live-abcdef1234567890"
YAML
run_fail "audit-A4: literal Bearer token in authorization header" \
  bash "${REPO_ROOT}/scripts/audit-alert-secrets.sh" "${FIXTURE_ROOT}/audit-a4-bearer/alerting"

# --- Scenario A5: PEM private key in yaml → should FAIL ---------------------
F="${FIXTURE_ROOT}/audit-a5-pem/alerting"
mkdir -p "${F}"
cat > "${F}/bad.yaml" <<'YAML'
apiVersion: 1
someKey: |
  -----BEGIN RSA PRIVATE KEY-----
  MIIEogIBAAKCAQEAi...
  -----END RSA PRIVATE KEY-----
YAML
run_fail "audit-A5: PEM private key block in YAML" \
  bash "${REPO_ROOT}/scripts/audit-alert-secrets.sh" "${FIXTURE_ROOT}/audit-a5-pem/alerting"

# ═══════════════════════════════════════════════════════════════════════════
# verify-alert-runbooks.sh
# ═══════════════════════════════════════════════════════════════════════════
#
# The verify script resolves runbook_url paths RELATIVE TO THE REPO. To test
# it, we build fixtures that include both rule YAMLs and the target runbook.
# We `cd` into the fixture so relative paths resolve from there.

# --- Scenario V1: rule + anchor both exist → PASS ---------------------------
F="${FIXTURE_ROOT}/verify-v1-clean"
mkdir -p "${F}/deploy/grafana/provisioning/alerting"
mkdir -p "${F}/docs/runbooks"
write_clean_rule_yaml "${F}/deploy/grafana/provisioning/alerting/clean.yaml" "known-section"
write_good_runbook "${F}/docs/runbooks/platform-recovery.md"
run_verify_pass "verify-V1: rule + anchor resolve cleanly" "${F}"

# --- Scenario V2: rule points at missing file → FAIL ------------------------
F="${FIXTURE_ROOT}/verify-v2-missing-file"
mkdir -p "${F}/deploy/grafana/provisioning/alerting"
write_clean_rule_yaml "${F}/deploy/grafana/provisioning/alerting/bad.yaml" "whatever"
# NOTE: we deliberately do NOT create docs/runbooks/platform-recovery.md
run_verify_fail "verify-V2: runbook_url file doesn't exist" "${F}"

# --- Scenario V3: rule points at dead anchor → FAIL -------------------------
F="${FIXTURE_ROOT}/verify-v3-dead-anchor"
mkdir -p "${F}/deploy/grafana/provisioning/alerting"
mkdir -p "${F}/docs/runbooks"
write_clean_rule_yaml "${F}/deploy/grafana/provisioning/alerting/bad.yaml" "does-not-exist"
write_good_runbook "${F}/docs/runbooks/platform-recovery.md"  # has only 'known-section' + 'another-section'
run_verify_fail "verify-V3: runbook_url anchor not found in file" "${F}"

# --- Scenario V4: rule with NO runbook_url → WARN-but-PASS ------------------
# verify-alert-runbooks.sh design: missing runbook_url is a WARNING, not an
# ERROR, so SEC-024/INFRA-005 pre-existing rules don't break the CI gate for
# unrelated PRs. This test confirms that design choice holds.
F="${FIXTURE_ROOT}/verify-v4-missing-annotation"
mkdir -p "${F}/deploy/grafana/provisioning/alerting"
cat > "${F}/deploy/grafana/provisioning/alerting/bad.yaml" <<'YAML'
apiVersion: 1
groups:
  - orgId: 1
    name: no-runbook
    rules:
      - uid: no-runbook-rule
        title: no_runbook_rule
        annotations:
          summary: 'ok'
        labels:
          severity: warning
YAML
run_verify_pass "verify-V4: missing runbook_url is warning not error" "${F}"

# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════

echo ""
if [[ "${failures}" -eq 0 ]]; then
  echo "All alerting-guard tests passed."
  exit 0
else
  echo "${failures} test(s) failed. Fix the underlying script before re-running."
  exit 1
fi
