#!/bin/sh
# SPEC-SEC-DOCKER-AUTHZ-001 REQ-U-002a — Alloy must not reach the raw socket.
#
# config.alloy holds TWO independent Docker connections: discovery.docker finds
# containers, loki.source.docker reads their logs. Changing only one is the
# failure this guard exists to prevent — on 2026-08-14 that produced ~10k
# "error inspecting Docker container" failures while Alloy still reported
# healthy and log volume stayed high enough that nothing looked wrong.
#
# The count check matters as much as the grep: a THIRD Docker connection added
# later would slip past a raw-socket search by being new rather than by being
# wrong.

set -eu
CONFIG="${1:-deploy/alloy/config.alloy}"
FAIL=0

raw=$(grep -c 'unix:///var/run/docker.sock' "$CONFIG" || true)
if [ "$raw" -ne 0 ]; then
    echo "FAIL: $CONFIG still dials the raw socket ($raw reference(s))" >&2
    grep -n 'unix:///var/run/docker.sock' "$CONFIG" >&2
    echo "      Alloy has no raw socket mount; use http://docker-socket-proxy-ro:2375" >&2
    FAIL=1
fi

hosts=$(grep -cE '^[[:space:]]*host[[:space:]]*=[[:space:]]*"(unix|http)' "$CONFIG" || true)
proxied=$(grep -cE '^[[:space:]]*host[[:space:]]*=[[:space:]]*"http://docker-socket-proxy-ro:2375"' "$CONFIG" || true)
if [ "$hosts" -ne "$proxied" ]; then
    echo "FAIL: $CONFIG has $hosts Docker host(s) but only $proxied via the read-only proxy" >&2
    grep -nE '^[[:space:]]*host[[:space:]]*=[[:space:]]*"(unix|http)' "$CONFIG" >&2
    FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
    echo "OK: alloy reaches Docker only via docker-socket-proxy-ro ($proxied connection(s))"
fi
exit "$FAIL"
