#!/usr/bin/env bash
# verify-alert-runbooks.sh — resolve every `runbook_url` annotation in the
# Grafana alerting provisioning tree against the actual files + headers in
# the repo. Block the PR if any annotation is dangling.
#
# SPEC-OBS-001-R18/R19. Runs in CI (.github/workflows/alerting-check.yml) and
# can be invoked locally: `bash scripts/verify-alert-runbooks.sh`.
#
# What this enforces:
#   - Any rule with a `runbook_url:` annotation → the referenced file MUST exist.
#   - If the annotation includes `#anchor` → a matching markdown header MUST
#     exist in that file (slug-match: lowercase, spaces→dashes, punctuation stripped).
#
# What this does NOT enforce (intentional, for SEC-024/INFRA-005 compatibility):
#   - Missing `runbook_url` on a rule is a WARNING, not an error. Retroactive
#     enforcement on pre-OBS-001 rules would break unrelated PRs. New rules
#     are expected to include runbook_url — checked via code review.

set -euo pipefail

SCAN_DIR="${1:-deploy/grafana/provisioning/alerting}"

if [ ! -d "$SCAN_DIR" ]; then
  echo "verify-alert-runbooks: scan directory not found: $SCAN_DIR" >&2
  exit 2
fi

# Slug-ify a header string the way GitHub/Grafana markdown renders anchors:
#   - lowercase
#   - trim
#   - strip all chars that aren't alphanumeric, space, or dash
#   - collapse whitespace runs to single dash
slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9[:space:]-]//g' \
    | sed -E 's/[[:space:]]+/-/g' \
    | sed -E 's/-+/-/g' \
    | sed -E 's/^-|-$//g'
}

# Collect rules files (*.yaml under SCAN_DIR containing `runbook_url:`).
mapfile -t YAML_FILES < <(find "$SCAN_DIR" -type f \( -name '*.yaml' -o -name '*.yml' \) | sort)

if [ "${#YAML_FILES[@]}" -eq 0 ]; then
  echo "verify-alert-runbooks: no YAML files found in $SCAN_DIR"
  exit 0
fi

ERRORS=0
WARNINGS=0

for yaml_file in "${YAML_FILES[@]}"; do
  # Skip non-rule files (contact-points, policies, mute-timings).
  case "$(basename "$yaml_file")" in
    contact-points.yaml|policies.yaml|mute-timings.yaml) continue ;;
  esac

  # Count alert rules in the file: each rule has a `- uid:` line.
  rule_count=$(grep -cE '^[[:space:]]+-[[:space:]]+uid:[[:space:]]' "$yaml_file" || true)
  [ "$rule_count" -eq 0 ] && continue

  # Count runbook_url annotations.
  rb_count=$(grep -cE '^[[:space:]]*runbook_url:[[:space:]]' "$yaml_file" || true)

  if [ "$rb_count" -lt "$rule_count" ]; then
    echo "verify-alert-runbooks: WARNING — $yaml_file has $rule_count rule(s) but only $rb_count runbook_url annotation(s)" >&2
    WARNINGS=$((WARNINGS + 1))
  fi

  # Extract each runbook_url value and validate.
  while IFS= read -r line; do
    # Line looks like:  `          runbook_url: docs/runbooks/platform-recovery.md#anchor`
    # or quoted:        `          runbook_url: 'docs/runbooks/...'`
    url=$(printf '%s\n' "$line" | sed -E "s/^[[:space:]]*runbook_url:[[:space:]]*['\"]?([^'\"[:space:]]+)['\"]?[[:space:]]*$/\1/")
    if [ -z "$url" ] || [ "$url" = "$line" ]; then
      echo "verify-alert-runbooks: ERROR — $yaml_file: cannot parse runbook_url line: $line" >&2
      ERRORS=$((ERRORS + 1))
      continue
    fi

    # Split on #.
    path="${url%%#*}"
    anchor=""
    if [ "$url" != "$path" ]; then
      anchor="${url#*#}"
    fi

    if [ ! -f "$path" ]; then
      echo "verify-alert-runbooks: ERROR — $yaml_file: runbook_url file not found: $path" >&2
      ERRORS=$((ERRORS + 1))
      continue
    fi

    if [ -z "$anchor" ]; then
      # File-only reference, no anchor to check. Accept.
      continue
    fi

    # Extract all markdown headers in the file, slugify, check for match.
    found=0
    while IFS= read -r header; do
      # Strip leading `#` chars + one space.
      text=$(printf '%s' "$header" | sed -E 's/^#+[[:space:]]+//')
      slug=$(slugify "$text")
      if [ "$slug" = "$anchor" ]; then
        found=1
        break
      fi
    done < <(grep -E '^#+[[:space:]]+' "$path" || true)

    if [ "$found" -eq 0 ]; then
      echo "verify-alert-runbooks: ERROR — $yaml_file: anchor '#$anchor' not found in $path" >&2
      ERRORS=$((ERRORS + 1))
    fi
  done < <(grep -E '^[[:space:]]*runbook_url:[[:space:]]' "$yaml_file")
done

echo ""
echo "verify-alert-runbooks: $ERRORS error(s), $WARNINGS warning(s)"

if [ "$ERRORS" -gt 0 ]; then
  exit 1
fi
exit 0
