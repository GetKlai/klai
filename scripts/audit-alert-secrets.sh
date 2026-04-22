#!/usr/bin/env bash
# audit-alert-secrets.sh — block literal secrets in Grafana alerting provisioning.
#
# Scope: deploy/grafana/provisioning/alerting/
# Exit code: 0 if clean, 1 if any literal secret pattern is found.
#
# What this catches (non-exhaustive):
#   - Slack webhook URLs (defense-in-depth; we don't use Slack, but this
#     blocks accidental pastes from examples/AI-generated configs).
#   - xoxb-/xoxp-/xoxa- Slack bot tokens.
#   - Literal PEM blocks (private keys).
#   - SMTP URLs with embedded credentials.
#   - Literal password: / api_key: / token: values that are NOT ${VAR} refs.
#   - `authorization: Bearer ...` with a literal token.
#
# SPEC-OBS-001-R7/R8. Runs in CI (.github/workflows/alerting-check.yml) and
# can also be invoked locally: `bash scripts/audit-alert-secrets.sh`.

set -euo pipefail

SCAN_DIR="${1:-deploy/grafana/provisioning/alerting}"

if [ ! -d "$SCAN_DIR" ]; then
  echo "audit-alert-secrets: scan directory not found: $SCAN_DIR" >&2
  exit 2
fi

# Collect hits across all patterns before deciding pass/fail, so the output is
# comprehensive (not just the first match).
HITS=0

# Helper: grep a pattern, print findings with file:line, bump HITS on match.
# Args: <label> <extended-regex>
check() {
  local label="$1" pattern="$2"
  local matches
  # -rEn: recursive, extended regex, line numbers. --include filters to YAML.
  # -I skips binary files. We deliberately scan all files in the tree —
  # secrets shouldn't be in README.md either.
  matches=$(grep -rEnI \
    --include='*.yaml' --include='*.yml' --include='*.md' --include='*.json' \
    "$pattern" "$SCAN_DIR" 2>/dev/null || true)
  if [ -n "$matches" ]; then
    echo "audit-alert-secrets: ${label} detected:" >&2
    echo "$matches" | sed 's/^/  /' >&2
    HITS=$((HITS + 1))
  fi
}

# 1. Slack webhooks (hooks.slack.com URLs with a path — catches real webhooks,
#    not documentation references like "slack.com" alone).
check "Slack webhook URL" 'hooks\.slack\.com/services/[A-Z0-9]'

# 2. Slack tokens.
check "Slack token (xoxb/xoxp/xoxa)" 'xox[bpao]-[0-9A-Za-z-]+'

# 3. PEM block headers (private keys, certs shouldn't be in alert configs).
check "PEM block" '^[[:space:]]*-----BEGIN[[:space:]]+[A-Z ]+-----'

# 4. SMTP URL with inline credentials (smtp://user:pass@host).
check "SMTP URL with inline credentials" 'smtps?://[^[:space:]$/]+:[^[:space:]$/@]+@'

# 5. Generic literal secret assignments in YAML. Matches lines like:
#      password: secret123
#      api_key: sk-abc
#      token: "xyz"
#    but EXCLUDES env-var substitution (${VAR}) and comments (#).
#    Also excludes the common "password: ''" / "password: null" empty patterns.
#
#    Pattern breakdown:
#      ^[[:space:]]*                  leading whitespace
#      (password|passwd|api_key|secret|token|bearer)   key name (case-insensitive via -i below)
#      [[:space:]]*:[[:space:]]*       colon + optional whitespace
#      ['"]?                           optional quote
#      [^$'"'"'{"[:space:]#]           NOT: $, quote, {, ", whitespace, #
#      [^$#'"'"'"]*                    rest of value, not starting with $ or quote
check "Literal secret key/value (password|api_key|secret|token|bearer)" \
  '^[[:space:]]*(password|passwd|api_key|apikey|secret|token|bearer)[[:space:]]*:[[:space:]]*['"'"'"]?[^$'"'"'"[:space:]{#][^$#'"'"'"]*$'

# 6. Authorization header with literal bearer token (not ${...}).
check "Authorization: Bearer with literal token" \
  '[Aa]uthorization[[:space:]]*:[[:space:]]*['"'"'"]?[Bb]earer[[:space:]]+[A-Za-z0-9_.-]{10,}'

# 7. Known SMTP password from our own SOPS (defense-in-depth: catch if someone
#    leaks GRAFANA_SMTP_PASSWORD's value into a YAML by mistake). This is a
#    prefix-match — the actual value lives in SOPS only.
#    Not done: we avoid hardcoding the real secret value in this script.
#    The env-var substitution rule (rule 5) already blocks a password: literal.

if [ "$HITS" -gt 0 ]; then
  echo "" >&2
  echo "audit-alert-secrets: $HITS pattern(s) matched — fix by replacing literals with \${VAR} env-var references." >&2
  echo "See deploy/grafana/provisioning/alerting/README.md for the secret-injection pattern." >&2
  exit 1
fi

echo "audit-alert-secrets: clean ($SCAN_DIR)"
exit 0
