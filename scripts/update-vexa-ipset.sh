#!/usr/bin/env bash
# update-vexa-ipset.sh — SPEC-SEC-022 Phase 2 ipset refresh.
#
# [STATUS: SKELETON — DO NOT WIRE UP UNTIL PHASE 1 DATA EXISTS]
#
# Reads deploy/vexa/egress-allowlist.txt, resolves each hostname to IPs,
# and atomically swaps the `vexa_allowlist` ipset on core-01.
#
# The DOCKER-USER iptables rule added by Phase 3 (scripts/harden-docker-user.sh)
# references `vexa_allowlist` via `-m set --match-set vexa_allowlist dst,dst`.
# An atomic swap (create temp, swap, destroy) prevents dropped bot connections
# during refresh.
#
# Run cadence: every 6 hours (cron on core-01). Provider CDN IPs rotate;
# hard-coding them WILL break meetings.
#
# Usage: sudo /opt/klai/scripts/update-vexa-ipset.sh [allowlist_file]
set -euo pipefail

ALLOWLIST="${1:-/opt/klai/deploy/vexa/egress-allowlist.txt}"
SET_NAME="vexa_allowlist"
SET_TEMP="${SET_NAME}_new"
# ipset type `hash:ip,port` supports IP + port:proto pairs.
SET_TYPE="hash:ip,port"
SET_OPTS="family inet hashsize 4096 maxelem 65536"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: must run as root (ipset needs CAP_NET_ADMIN)" >&2
  exit 1
fi

if ! command -v ipset >/dev/null 2>&1; then
  echo "ERROR: ipset not installed on this host." >&2
  echo "Install first: apt-get install -y ipset" >&2
  exit 1
fi

if [[ ! -f "$ALLOWLIST" ]]; then
  echo "ERROR: allowlist file not found: $ALLOWLIST" >&2
  exit 1
fi

# Guard: refuse to run when allowlist is effectively empty (comments only).
if ! grep -vE '^\s*(#|$)' "$ALLOWLIST" | grep -q .; then
  echo "ERROR: allowlist has no active entries yet (Phase 1 capture pending)." >&2
  echo "       DO NOT apply — would drop all bot egress." >&2
  exit 2
fi

# Ensure live set exists so the swap has something to swap against.
ipset list -n "$SET_NAME" >/dev/null 2>&1 \
  || ipset create "$SET_NAME" $SET_TYPE $SET_OPTS

# Fresh temp set.
ipset list -n "$SET_TEMP" >/dev/null 2>&1 && ipset destroy "$SET_TEMP"
ipset create "$SET_TEMP" $SET_TYPE $SET_OPTS

# Parse allowlist: each active line is "<hostname-or-cidr> <port>/<proto> ...".
# Multiple port/proto pairs per line allowed.
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(echo "$line" | xargs || true)"
  [[ -z "$line" ]] && continue

  host="$(awk '{print $1}' <<<"$line")"
  # All remaining tokens are port/proto tuples.
  port_specs="$(awk '{for (i=2; i<=NF; i++) print $i}' <<<"$line")"
  if [[ -z "$port_specs" ]]; then
    echo "WARN: no port/proto for $host (line: '$line')" >&2
    continue
  fi

  # Resolve host → IP list (A records). CIDRs pass through unchanged.
  if [[ "$host" == */* ]]; then
    ips=("$host")
  else
    mapfile -t ips < <(dig +short +time=2 +tries=1 "$host" A \
      | awk '/^[0-9.]+$/ {print}')
    if [[ ${#ips[@]} -eq 0 ]]; then
      echo "WARN: no A records for $host" >&2
      continue
    fi
  fi

  for ip in "${ips[@]}"; do
    while IFS= read -r spec; do
      [[ -z "$spec" ]] && continue
      port="${spec%%/*}"
      proto="${spec##*/}"
      ipset add "$SET_TEMP" "$ip,${proto}:${port}" -exist
    done <<<"$port_specs"
  done
done < "$ALLOWLIST"

# Atomic swap: live set now has new contents, old is in SET_TEMP.
ipset swap "$SET_NAME" "$SET_TEMP"
ipset destroy "$SET_TEMP"

echo "ipset '$SET_NAME' refreshed at $(date -u): $(ipset list "$SET_NAME" | grep '^Number of entries' || true)"
