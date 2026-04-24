#!/bin/sh
# portal-api container entrypoint.
#
# Runs alembic migrations before starting the server. Fail-loud: any
# alembic error aborts the container so orchestration / observability
# sees the failure instead of silently continuing with a stale schema.
#
# Introduced by SPEC-CHAT-TEMPLATES-CLEANUP-001 after SPEC-CHAT-TEMPLATES-001
# required manual `docker exec ... alembic upgrade head` on production.
set -eu

echo "[entrypoint] Running alembic upgrade head…"
alembic upgrade head
echo "[entrypoint] Migrations applied."

# SPEC-SEC-WEBHOOK-001 REQ-1: uvicorn --proxy-headers trust-boundary.
#
# Without --proxy-headers, `request.client.host` equals the TCP peer — that
# is Caddy's klai-net container IP (172.x.y.z) for every external request.
# Cornelis audit #2 exploited exactly that to bypass the Vexa webhook auth
# check (the now-deleted "internal Docker network = trusted" shortcut
# short-circuited on Caddy's own IP). REQ-2 removed the shortcut in #155;
# this step adds the proper proxy-headers handling so future rate-limiting,
# CSRF-exempt audits, and log correlation see the REAL external client IP.
#
# `--forwarded-allow-ips` must NOT be `*` (REQ-1.2) and must NOT be a
# hardcoded IP (container IPs change per restart —
# .claude/rules/klai/infra/servers.md). We resolve `caddy` at container
# start time via Docker's embedded DNS. If caddy is unresolvable (bootstrap
# race, compose network misconfig), we fall back to 127.0.0.1 which means
# "trust nobody's X-Forwarded-For" — safe default that keeps the service
# up and surfaces the misconfiguration via `request.client.host` showing
# the TCP peer instead of a real client.
CADDY_IP="$(getent hosts caddy 2>/dev/null | awk '{print $1}' | head -n1)"
if [ -z "$CADDY_IP" ]; then
    echo "[entrypoint] WARN: cannot resolve caddy via Docker DNS — falling back to --forwarded-allow-ips=127.0.0.1 (X-Forwarded-For ignored)."
    CADDY_IP="127.0.0.1"
else
    echo "[entrypoint] Resolved caddy → $CADDY_IP (trusted source for X-Forwarded-For / X-Forwarded-Proto)."
fi

echo "[entrypoint] Starting uvicorn with --proxy-headers --forwarded-allow-ips=$CADDY_IP"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8010 \
    --proxy-headers \
    --forwarded-allow-ips="$CADDY_IP" \
    "$@"
