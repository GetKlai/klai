#!/bin/sh
# uvicorn-launch.sh — shared uvicorn launcher for all klai backend services.
#
# Purpose
# -------
# SPEC-SEC-WEBHOOK-001 REQ-1 hardened the trust boundary on uvicorn's
# reverse-proxy header parsing by requiring --proxy-headers together with
# a narrowly-scoped --forwarded-allow-ips instead of the wildcard "*".
#
# REQ-6 (this script) eliminates the duplication: four services previously
# each carried their own copy of the Caddy-IP resolution logic with subtle
# variations. A single source of truth prevents drift and makes the security
# invariant auditable in one place.
#
# How it works
# ------------
# 1. If UVICORN_FORWARDED_ALLOW_IPS is set (e.g. in tests or special
#    deployments), that value is used directly — no DNS lookup performed.
# 2. Otherwise the script resolves the "caddy" hostname via Docker's embedded
#    DNS (getent hosts caddy). If Caddy is reachable, its container IP is
#    trusted as the sole forwarding proxy.
# 3. If the DNS lookup fails (bootstrap race, network misconfiguration), the
#    script falls back to 127.0.0.1. This means "trust nobody's
#    X-Forwarded-For" which is the safe default — the service stays up but
#    client IPs will show Caddy's TCP peer address rather than the real
#    external client address, making the misconfiguration visible in logs.
#
# Usage
# -----
#   /app/scripts/uvicorn-launch.sh <module:app> [extra uvicorn args...]
#
# Example
#   /app/scripts/uvicorn-launch.sh app.main:app --host 0.0.0.0 --port 8000
#
# References
# ----------
#   SPEC-SEC-WEBHOOK-001 REQ-1, REQ-6
#   .claude/rules/klai/infra/servers.md (container IPs change per restart)
#
# POSIX compatibility: written for /bin/sh (alpine uses ash, not bash).

set -eu

APP_TARGET="${1:?uvicorn-launch.sh: first argument must be module:app target}"
shift  # remaining args are passed through to uvicorn

# Allow tests and special deployments to override without a DNS lookup.
if [ -n "${UVICORN_FORWARDED_ALLOW_IPS:-}" ]; then
    CADDY_IP="$UVICORN_FORWARDED_ALLOW_IPS"
    echo "[uvicorn-launch] UVICORN_FORWARDED_ALLOW_IPS override → --forwarded-allow-ips=$CADDY_IP"
else
    CADDY_IP="$(getent hosts caddy 2>/dev/null | awk '{print $1}' | head -n1)"
    if [ -z "$CADDY_IP" ]; then
        echo "[uvicorn-launch] WARN: cannot resolve caddy via Docker DNS — falling back to --forwarded-allow-ips=127.0.0.1 (X-Forwarded-For ignored)."
        CADDY_IP="127.0.0.1"
    else
        echo "[uvicorn-launch] Resolved caddy → $CADDY_IP (trusted source for X-Forwarded-For / X-Forwarded-Proto)."
    fi
fi

echo "[uvicorn-launch] Starting: uvicorn $APP_TARGET --proxy-headers --forwarded-allow-ips=$CADDY_IP $*"
exec uvicorn "$APP_TARGET" \
    --proxy-headers \
    --forwarded-allow-ips="$CADDY_IP" \
    "$@"
