#!/usr/bin/env bash
#
# scripts/smoke-ssrf-isolation.sh
#
# SPEC-SEC-SSRF-001 REQ-5 / AC-13 / AC-22 — post-deploy smoke-test.
# Verifies that every container listed in REQ-5.2's "must-not-join-
# socket-proxy" set genuinely cannot reach docker-socket-proxy:2375.
# A future compose edit adding one of these containers to
# `socket-proxy` reintroduces the A1 env-dump chain; this script is
# the CI regression guard.
#
# Run on core-01:
#   ssh core-01 "/opt/klai/scripts/smoke-ssrf-isolation.sh"
#
# Exit 0: every container is correctly isolated (curl --connect-timeout
#         2 fails with "Couldn't connect" / timeout).
# Exit 1: at least one container can reach docker-socket-proxy:2375.
#         This is a SECURITY FAIL — investigate and revert.

set -euo pipefail

# ─── Config ─────────────────────────────────────────────────────────────────

# Containers that MUST NOT be able to reach docker-socket-proxy.
# Source of truth: see the "must not join" table in
# .claude/rules/klai/platform/docker-socket-proxy.md (REQ-5.2).
# Keep this list in lock-step with that file. klai-mailer is NOT in
# the set — it has no user-URL fetch surface, verified 2026-04-24.
ISOLATED_CONTAINERS=(
  "klai-core-knowledge-ingest-1"
  "klai-core-crawl4ai-1"
  "klai-core-klai-connector-1"
  "klai-core-scribe-1"
  "klai-core-research-api-1"
  "klai-core-retrieval-api-1"
)

TARGET="http://docker-socket-proxy:2375/v1.42/info"
CONNECT_TIMEOUT=2

FAIL=0

log() { printf '%-14s %s\n' "$1" "$2"; }

# ─── Probes ────────────────────────────────────────────────────────────────

for ctr in "${ISOLATED_CONTAINERS[@]}"; do
  if ! docker inspect "$ctr" >/dev/null 2>&1; then
    log "[SKIP]" "$ctr not present on this host"
    continue
  fi

  log "[*] probe" "$ctr -> $TARGET"
  # --connect-timeout 2 bounds the probe; curl returns non-zero when
  # the connection is refused / unreachable / times out (what we
  # want). A 2xx / 4xx reply means the container IS on socket-proxy,
  # which is a security FAIL.
  if docker exec "$ctr" curl --connect-timeout "$CONNECT_TIMEOUT" -s -o /dev/null "$TARGET"; then
    log "[FAIL]" "$ctr REACHES docker-socket-proxy — SPEC-SEC-SSRF-001 REQ-5 violated"
    FAIL=1
  else
    log "[OK]" "$ctr correctly isolated (connection failed as expected)"
  fi
done

# ─── Result ─────────────────────────────────────────────────────────────────

if [ "$FAIL" -eq 0 ]; then
  echo
  echo "SPEC-SEC-SSRF-001 isolation smoke-test: PASS"
else
  echo
  echo "SPEC-SEC-SSRF-001 isolation smoke-test: FAIL"
  echo "  One or more containers that MUST be isolated from"
  echo "  docker-socket-proxy successfully reached it. Revert the"
  echo "  compose edit immediately — the A1 env-dump chain is back."
fi

exit "$FAIL"
