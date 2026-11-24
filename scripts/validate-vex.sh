#!/bin/sh
# Fail when a VEX statement for a third-party image has lost its footing.
#
# .vex/unused-components.json says "this component is in the image but this
# service never runs it". Each statement must name an OpenVEX justification,
# give a reason of at least 40 characters and its evidence, expire within 365
# days, and every compose condition it rests on (an unset variable, a network
# the service is not on) must still hold. A statement that silently outlived
# its premise would hide a real finding, which is worse than no statement.
#
# Also checks deploy/image-exposure.json: every listed service exists, none is
# both content-consuming and internal, and every tracked file that mentions
# reverse_proxy is classified as a route source or as not one.
#
# Logic: scripts/triage-image-findings.py (validate). Needs python3 and yq.
# Run: ./scripts/validate-vex.sh

set -eu

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT"

for tool in python3 yq; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "[validate-vex] FATAL: $tool not available." >&2
        exit 2
    fi
done

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
yq -o=json deploy/docker-compose.yml > "$TMP/deploy.json"
yq -o=json docker-compose.dev.yml > "$TMP/dev.json"

python3 scripts/triage-image-findings.py validate --deploy "$TMP/deploy.json" --dev "$TMP/dev.json"
