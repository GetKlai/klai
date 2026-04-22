# SEC-022 Phase 1 — vexa-bots egress capture

This runbook covers the one-shot capture required before we can design the
vexa-bots egress allowlist. See [SPEC-SEC-022](../../.moai/specs/SPEC-SEC-022/spec.md)
for the full threat model and target end state.

## What this captures, and why

Vexa bot containers (Chromium instances) currently reach any host on the
internet plus every internal Docker network they share a bridge with. To
design a meaningful iptables allowlist we need empirical data: which
hostnames do bots actually talk to during a real Google Meet / Teams /
Zoom call?

The capture records every packet whose source IP is in the `vexa-bots`
bridge (`172.27.0.0/16`) and whose destination is NOT in `klai-net`
(`172.18.0.0/16`). That gives us every outbound packet that would need
an allowlist entry, and nothing else.

## Prerequisites on core-01

Already installed: `tcpdump`, `iptables`, `dig`.

Still required for Phase 3 enforce (not Phase 1):

```bash
ssh core-01 "sudo apt-get update && sudo apt-get install -y ipset"
```

## Running the capture

1. Pick a window of roughly 4 hours during which you can run at least
   five meetings per provider (Google Meet, Microsoft Teams, Zoom).
   Total ≥ 15 meetings is the target.
2. Copy the capture script to the server:

   ```bash
   scp scripts/vexa-egress-capture.sh core-01:/opt/klai/scripts/
   ssh core-01 "sudo chmod +x /opt/klai/scripts/vexa-egress-capture.sh"
   ```

3. Start the capture on core-01 (requires root for tcpdump):

   ```bash
   ssh core-01 "sudo /opt/klai/scripts/vexa-egress-capture.sh 14400" \
     | tee vexa-egress-capture.log
   ```

   Arguments: `[duration_seconds]` (default 14400 = 4 hours),
   `[output_dir]` (default `/opt/klai/captures/vexa-egress`).

4. While the capture runs, use the portal to spawn bots into real
   meetings. Variety matters more than volume: use multiple tenants,
   accounts, and meeting sizes.

5. When the window closes, tcpdump stops automatically. The script
   post-processes the pcap and writes:

   ```
   /opt/klai/captures/vexa-egress/
     vexa-egress-<ts>.pcap          # raw capture, retain for re-analysis
     vexa-egress-<ts>.dests.txt     # unique dst-IP dst-port proto
     vexa-egress-<ts>.rdns.tsv      # IP → rDNS (tab-separated)
     vexa-egress-<ts>.resolved.txt  # dests + rdns merged, sorted by hostname
     vexa-egress-<ts>.summary.txt   # top ports + rDNS suffixes + IPs without rDNS
     vexa-egress-<ts>.log           # tcpdump stderr
   ```

6. Download the summary for review:

   ```bash
   scp core-01:/opt/klai/captures/vexa-egress/vexa-egress-*-summary.txt .
   scp core-01:/opt/klai/captures/vexa-egress/vexa-egress-*-resolved.txt .
   ```

## Interpreting the summary

- **Top ports** should be dominated by `443/tcp` (meeting-platform HTTPS +
  control plane) and `443/udp` or `3478/udp` (WebRTC / STUN / TURN media).
  Anything else on the top-5 deserves an eyebrow raise.
- **rDNS suffix distribution** is where the allowlist design starts. Expect
  `1e100.net` / `googlevideo.com` (Google media), `teams.microsoft.com` /
  `trafficmanager.net` (Teams fronts), `zoom.us` / `zoomgov.com` /
  `zoomcdn.net` (Zoom). Anything Klai-unrelated should be investigated
  before being allowlisted.
- **IPs without rDNS** are your maintenance burden: WebRTC relays and
  anycast ranges often skip rDNS. These need to go in the allowlist as
  raw IP/CIDR blocks and will require periodic refresh.

## Before proceeding to Phase 2

Do not apply iptables rules or enable the `update-vexa-ipset.sh` cron
until the capture summary has been reviewed and
[deploy/vexa/egress-allowlist.txt](../../deploy/vexa/egress-allowlist.txt)
has been filled in and committed. An empty allowlist with enforce rules
active will block every bot immediately.

## Bail-out

If the capture window ends and you need to re-run, simply start the
script again — each run writes to a timestamped filename. Keep at least
the last two pcaps; they are the only record of "this is what the real
world looked like before we filtered it."
