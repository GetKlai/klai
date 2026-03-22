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

echo ""
echo "Persisting rules to /etc/iptables/rules.v4 ..."
iptables-save > /etc/iptables/rules.v4
echo "Rules persisted."
