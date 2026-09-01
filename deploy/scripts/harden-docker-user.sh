#!/usr/bin/env bash
# Apply DOCKER-USER iptables whitelist for core-01.
#
# Run this AFTER "docker compose up -d" — the DOCKER chain must exist first.
# Re-run whenever the firewall rules need to be re-applied (e.g. after a reboot
# that lost the live rules before iptables-persistent loads, or after a full
# rule flush).
#
# Design intent:
#   - Only ports 80/443 are reachable from the internet (Caddy reverse proxy).
#   - All other forwarded traffic from the external interface is dropped.
#   - Rules are port-based (NOT container-IP-based) so they survive container
#     restarts which change container IPs.
#   - Docker's own DOCKER chain still controls which container receives the
#     traffic; only mapped ports are forwarded.
#
# Run as root or with sudo.

set -euo pipefail

EXT_IF="${1:-enp5s0}"  # external interface; override if needed

echo "Applying DOCKER-USER whitelist on $EXT_IF ..."

# Flush existing DOCKER-USER rules
iptables -F DOCKER-USER

# Allow established/related connections (return traffic)
iptables -A DOCKER-USER -i "$EXT_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

# Allow inbound TCP 80 + 443 (Caddy)
iptables -A DOCKER-USER -i "$EXT_IF" -p tcp -m multiport --dports 80,443 -j ACCEPT

# Allow inbound UDP 443 (QUIC/HTTP3)
iptables -A DOCKER-USER -i "$EXT_IF" -p udp --dport 443 -j ACCEPT

# Drop all other inbound traffic from external interface to Docker containers
iptables -A DOCKER-USER -i "$EXT_IF" -j DROP

echo "Done. Current DOCKER-USER chain:"
iptables -L DOCKER-USER -n --line-numbers

# ── Meeting bots must not reach the host (SPEC-SEC-022 REQ-2) ────────────────
#
# DOCKER-USER above is inbound-only (-i $EXT_IF): it governs internet → container.
# Container → container is handled by Docker's own DOCKER-FORWARD isolation, and
# measurement confirms a bot cannot reach any klai-net container.
#
# Container → the HOST is neither. That traffic lands in INPUT, whose policy is
# ACCEPT and which nothing else filters — ufw is uninstalled here, only its empty
# chains remain. So on 2026-08-17 a container on vexa12-bots could reach every
# service the host binds on its bridge addresses:
#
#   172.18.0.1:22     SSH
#   172.18.0.1:11434  ollama          172.18.0.1:7997  embeddings
#   172.18.0.1:8000   transcription   172.18.0.1:7998  reranker
#   172.18.0.1:8001   vLLM
#
# Those are the GPU tunnel forwards, unauthenticated, plus the host's SSH port.
# The bots run Chromium against meeting pages we do not control, which is the
# whole reason SPEC-SEC-022 exists.
#
# Default-deny with one exception rather than a blocklist, so a service bound to
# a new host port later is covered without anyone remembering to add it.
#
# The exception is tcp/8000, the transcription endpoint, and it is scoped to one
# address: vexa12-transcription-proxy. Vexa bots POST their captured PCM to STT
# themselves, so meeting-api passes them the proxy's in-network URL. The proxy
# has one fixed TCP destination and no environment or secret surface.
#
# So the boundary cannot be drawn per network; it is drawn inside it. The compose
# file confines dynamic allocation on vexa12-bots to 172.29.128.0/17 and pins
# the transcription proxy at 172.29.0.11, outside that pool. A bot can never be
# handed that address, so allowing it grants nothing to a compromised browser.
#
# Read the pinned address from Docker rather than repeating the literal — if the
# compose pin is ever dropped, TRANSCRIPTION_PROXY_IP comes back empty and the
# exception is simply not installed, which fails closed.
#
# The subnet is read from Docker rather than hardcoded: 172.29.0.0/16 today, but
# the SPEC was written against 172.27.0.0/16 and that range now belongs to a
# different network entirely. A hardcoded CIDR here would have silently filtered
# the wrong containers.
BOTS_NET="${BOTS_NET:-vexa12-bots}"
BOTS_CIDR="$(docker network inspect "$BOTS_NET" \
    --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || true)"

if [ -z "$BOTS_CIDR" ]; then
    echo ""
    echo "WARNING: docker network '$BOTS_NET' not found — host-isolation rules NOT applied." >&2
    echo "         Bots can reach every host-bound port until this is resolved." >&2
    exit 1
fi

echo ""
echo "Restricting $BOTS_NET ($BOTS_CIDR) → host ..."

TRANSCRIPTION_PROXY_IP="$(docker inspect klai-core-vexa12-transcription-proxy-1 \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{if eq $k "vexa12-bots"}}{{$v.IPAddress}}{{end}}{{end}}' \
    2>/dev/null || true)"

# Idempotent: the unit re-runs on every boot and after every deploy. Clear the
# whole-subnet form too — it is what this script installed before the pin
# existed, and leaving it behind would keep every bot excepted.
while iptables -D INPUT -s "$BOTS_CIDR" -p tcp --dport 8000 -j ACCEPT 2>/dev/null; do :; done
[ -n "$TRANSCRIPTION_PROXY_IP" ] && \
    while iptables -D INPUT -s "$TRANSCRIPTION_PROXY_IP" -p tcp --dport 8000 -j ACCEPT 2>/dev/null; do :; done
while iptables -D INPUT -s "$BOTS_CIDR" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do :; done
while iptables -D INPUT -s "$BOTS_CIDR" -j DROP 2>/dev/null; do :; done

# Rules are inserted at position 1, so later inserts land above earlier ones.
iptables -I INPUT 1 -s "$BOTS_CIDR" -j DROP
iptables -I INPUT 1 -s "$BOTS_CIDR" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
if [ -z "$TRANSCRIPTION_PROXY_IP" ]; then
    echo "  WARNING: vexa12-transcription-proxy has no vexa12-bots address — transcription" >&2
    echo "           exception NOT installed. Meetings will fail until this resolves." >&2
    exit 1
fi
iptables -I INPUT 1 -s "$TRANSCRIPTION_PROXY_IP" -p tcp --dport 8000 -j ACCEPT
echo "  transcription exception: $TRANSCRIPTION_PROXY_IP only"

echo "Done. INPUT rules for $BOTS_CIDR:"
iptables -L INPUT -n --line-numbers | grep -E "^(num|[0-9]+ .*${BOTS_CIDR//./\\.})" || true

echo ""
echo "Persisting rules to /etc/iptables/rules.v4 ..."
iptables-save > /etc/iptables/rules.v4
echo "Rules persisted."
