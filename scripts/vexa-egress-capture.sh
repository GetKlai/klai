#!/usr/bin/env bash
# vexa-egress-capture.sh — SPEC-SEC-022 Phase 1 data collection.
#
# Captures outbound packets from the vexa-bots Docker bridge (172.27.0.0/16)
# to any destination outside klai-net (172.18.0.0/16). Produces:
#   - a raw pcap file for forensic re-analysis
#   - a per-destination summary with ports
#   - a resolved list of rDNS hostnames (fallback: "<no-rdns>")
#
# Intended to be run on core-01 while real meetings are active, so the
# resulting allowlist covers the hostnames Vexa bots actually reach.
#
# Usage (on core-01, as root since tcpdump needs CAP_NET_RAW):
#   sudo /opt/klai/scripts/vexa-egress-capture.sh [duration_seconds] [output_dir]
#
# Defaults: 14400 seconds (4 hours), /opt/klai/captures/vexa-egress
set -euo pipefail

DURATION="${1:-14400}"
OUTPUT_DIR="${2:-/opt/klai/captures/vexa-egress}"
BRIDGE_CIDR="172.27.0.0/16"           # vexa-bots
KLAINET_CIDR="172.18.0.0/16"          # exclude: klai-net internal
MAX_RESOLVE_PARALLEL=10               # rDNS lookups in parallel

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: must run as root (tcpdump needs CAP_NET_RAW)" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
PCAP="$OUTPUT_DIR/vexa-egress-$TS.pcap"
DESTS="$OUTPUT_DIR/vexa-egress-$TS.dests.txt"
RESOLVED="$OUTPUT_DIR/vexa-egress-$TS.resolved.txt"
SUMMARY="$OUTPUT_DIR/vexa-egress-$TS.summary.txt"
LOG="$OUTPUT_DIR/vexa-egress-$TS.log"

echo "=== SPEC-SEC-022 Phase 1 capture ==="
echo "Duration:   ${DURATION}s ($(printf "%dh%dm" $((DURATION/3600)) $(((DURATION/60)%60))))"
echo "Source net: $BRIDGE_CIDR (vexa-bots)"
echo "Exclude:    dst net $KLAINET_CIDR (klai-net internal)"
echo "Output:     $OUTPUT_DIR"
echo
echo "Capture started at $(date -u)."
echo "Ask the operator to run 5+ meetings per provider (Meet / Teams / Zoom)."
echo "Press Ctrl+C to stop early."
echo

# -i any: all interfaces (docker bridges appear dynamically)
# -n: no DNS (done in post-process, keeps capture fast + deterministic)
# Filter: src net 172.27/16 AND NOT dst net 172.18/16
timeout --foreground "$DURATION" \
  tcpdump -i any -n -U -w "$PCAP" \
    "src net $BRIDGE_CIDR and not dst net $KLAINET_CIDR" \
    2> >(tee -a "$LOG" >&2) || true

echo
echo "Capture ended at $(date -u). Analysing pcap..."

# Extract unique dst-IP:dst-port pairs by protocol.
# tcpdump -r text form: "HH:MM:SS.ffff IP src.port > dst.port: proto ..."
# Use -tt -q for epoch timestamp + quiet protocol output; easier to parse.
tcpdump -r "$PCAP" -n -tt -q 2>/dev/null \
  | awk '
      # Match IPv4 or IPv6 lines. Field layout with -tt -q:
      #   $1=epoch  $2=IP/IP6  $3=src.port  $4=">"  $5=dst.port:  $6=proto  ...
      $2=="IP" || $2=="IP6" {
        proto = $6;
        dst = $5;
        sub(":$", "", dst);
        # IPv4: "1.2.3.4.443" → split on last dot. IPv6: "[::1].443" possible.
        n = split(dst, parts, ".");
        if (n >= 2) {
          port = parts[n];
          ip = parts[1];
          for (i = 2; i < n; i++) ip = ip "." parts[i];
          print ip, port, proto;
        }
      }
    ' \
  | sort -u > "$DESTS"

echo "Unique IP:port:proto tuples: $(wc -l < "$DESTS")"

# Parallel rDNS lookup with a fallback marker.
echo "Resolving rDNS (this may take a while)..."
: > "$RESOLVED"
awk '{print $1}' "$DESTS" | sort -u | xargs -n1 -P"$MAX_RESOLVE_PARALLEL" -I{} bash -c '
  ip="$1"
  host=$(dig +short +time=2 +tries=1 -x "$ip" 2>/dev/null | head -1 | sed "s/\.$//")
  printf "%s\t%s\n" "$ip" "${host:-<no-rdns>}"
' _ {} > "$OUTPUT_DIR/vexa-egress-$TS.rdns.tsv"

# Join dests + rdns to produce resolved.txt
awk 'NR==FNR { rdns[$1]=$2; next } { print $1, $2, $3, (rdns[$1] ? rdns[$1] : "<no-rdns>") }' \
  "$OUTPUT_DIR/vexa-egress-$TS.rdns.tsv" "$DESTS" \
  | sort -k4,4 -k1,1 > "$RESOLVED"

# Summary: top-N rDNS suffixes + port distribution
{
  echo "=== SPEC-SEC-022 Phase 1 capture summary ==="
  echo "Capture file:  $PCAP"
  echo "Duration:      ${DURATION}s"
  echo "Finished:      $(date -u)"
  echo "Tuples:        $(wc -l < "$DESTS")"
  echo "Unique IPs:    $(awk '{print $1}' "$DESTS" | sort -u | wc -l)"
  echo
  echo "=== Port distribution (top 20) ==="
  awk '{print $2 "/" $3}' "$DESTS" | sort | uniq -c | sort -rn | head -20
  echo
  echo "=== rDNS suffix distribution (top 30, stripped to last 2 labels) ==="
  awk -F'\t' '$2!="<no-rdns>" {
    n = split($2, p, ".");
    if (n >= 2) print p[n-1] "." p[n];
    else print $2;
  }' "$OUTPUT_DIR/vexa-egress-$TS.rdns.tsv" \
    | sort | uniq -c | sort -rn | head -30
  echo
  echo "=== IPs with no rDNS (candidate allowlist entries by raw IP) ==="
  awk -F'\t' '$2=="<no-rdns>" {print $1}' "$OUTPUT_DIR/vexa-egress-$TS.rdns.tsv" | sort -u
  echo
  echo "=== Full resolved list ==="
  echo "See: $RESOLVED"
} > "$SUMMARY"

echo
cat "$SUMMARY"
echo
echo "Done. Hand the summary to SPEC-SEC-022 Phase 2 allowlist design."
