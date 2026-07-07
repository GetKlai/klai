#!/usr/bin/env bash
# test-audit-compose-orphans.sh — regression fixture for audit-compose-orphans.sh
#
# Builds three temp compose+caddy fixtures:
#   1. CLEAN: passes all checks (matches current klai compose state)
#   2. CADDY-ORPHAN: Caddyfile points at a nonexistent service → must FAIL Check 1
#   3. ORPHAN-VOLUME: top-level volume not referenced anywhere → must FAIL Check 3
#
# Mirrors test-audit-compose.sh pattern.
#
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-2c regression test.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDIT="${SCRIPT_DIR}/audit-compose-orphans.sh"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

PASS=0
FAIL=0

assert() {
    local desc="$1" expected_exit="$2" compose="$3" caddy="$4"
    local actual
    set +e
    actual=$(AUDIT_COMPOSE_FILE="$compose" AUDIT_CADDYFILE="$caddy" bash "$AUDIT" 2>&1)
    local exit_code=$?
    set -e
    if [ "$exit_code" = "$expected_exit" ]; then
        echo "OK   ($exit_code) $desc"
        PASS=$((PASS + 1))
    else
        echo "FAIL got=$exit_code expected=$expected_exit  $desc"
        echo "    output: $actual" | head -3
        FAIL=$((FAIL + 1))
    fi
}

# ─── Fixture 1: CLEAN ────────────────────────────────────────────────────────

cat > "$TMPDIR/clean.yml" <<'EOF'
services:
  portal-api:
    image: ghcr.io/getklai/portal-api:latest
    container_name: portal-api
  caddy:
    image: ghcr.io/getklai/caddy-hetzner:latest
    container_name: caddy
    volumes:
      - caddy-data:/data

volumes:
  caddy-data:
EOF

cat > "$TMPDIR/clean.Caddyfile" <<'EOF'
my.getklai.com {
    reverse_proxy portal-api:8000
}
chat-acme.getklai.com {
    reverse_proxy librechat-acme:3080
}
EOF

assert "CLEAN compose+Caddyfile passes" 0 "$TMPDIR/clean.yml" "$TMPDIR/clean.Caddyfile"

# ─── Fixture 2: CADDY upstream points at a phantom service ───────────────────

cat > "$TMPDIR/caddy-orphan.Caddyfile" <<'EOF'
my.getklai.com {
    reverse_proxy portal-api:8000
}
ghost.getklai.com {
    reverse_proxy phantom-service:9999
}
EOF

assert "CADDY-ORPHAN: phantom upstream fails" 1 "$TMPDIR/clean.yml" "$TMPDIR/caddy-orphan.Caddyfile"

# ─── Fixture 3: top-level volume not referenced anywhere ─────────────────────

cat > "$TMPDIR/orphan-volume.yml" <<'EOF'
services:
  portal-api:
    image: ghcr.io/getklai/portal-api:latest
    container_name: portal-api

volumes:
  caddy-data:
  abandoned-volume:
EOF

assert "ORPHAN-VOLUME: unreferenced volume fails" 1 "$TMPDIR/orphan-volume.yml" "$TMPDIR/clean.Caddyfile"

# ─── Verdict ─────────────────────────────────────────────────────────────────

echo ""
echo "=== test-audit-compose-orphans: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]] || exit 1
exit 0
