#!/bin/sh
# SPEC-SEC-DOCKER-AUTHZ-001 REQ-U-002a — Alloy must not reach the raw socket.
#
# config.alloy has TWO independent Docker connections: discovery.docker finds
# containers, loki.source.docker reads their logs. They connect separately.
# Changing only the first is the failure this guard exists to prevent — discovery
# reported healthy while every log inspect failed, producing 10k errors in ten
# minutes with no signal that log collection had stopped.

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

hosts=$(grep -cE '^\s*host\s*=\s*"(unix|http)' "$CONFIG" || true)
proxied=$(grep -cE '^\s*host\s*=\s*"http://docker-socket-proxy-ro:2375"' "$CONFIG" || true)
if [ "$hosts" -ne "$proxied" ]; then
    echo "FAIL: $CONFIG has $hosts Docker host(s) but only $proxied via the read-only proxy" >&2
    grep -nE '^\s*host\s*=\s*"(unix|http)' "$CONFIG" >&2
    FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
    echo "OK: alloy reaches Docker only via docker-socket-proxy-ro ($proxied connection(s))"
fi
exit "$FAIL"
