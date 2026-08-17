---
id: SPEC-SEC-022
version: 0.3.0
status: partially_implemented
created: 2026-04-19
updated: 2026-08-17
author: Mark Vletter
priority: medium
---

# SPEC-SEC-022: vexa-bots network egress allowlist

## HISTORY

### v0.3.0 (2026-08-17)

**REQ-2 implemented and verified. REQ-1 deliberately dropped. The rest of this
document was written against a stack that no longer exists — read the notes
below before acting on anything above them.**

The April premise was measured on Vexa 0.10: `vexa-bots`, subnet 172.27.0.0/16,
`Internal=false`, bots with "arbitrary internet + full access to our internal
network". Vexa 0.12.22 replaced that stack in August. What is true now:

- The bot network is `vexa12-bots`, subnet **172.29.0.0/16**. The SPEC's
  172.27.0.0/16 is now `vexa12-portal`, an `Internal=true` network — applying
  the Phase 3 rules verbatim would have filtered the wrong containers and left
  the bots untouched.
- Bots **cannot** reach klai-net containers. Docker's `DOCKER-FORWARD` isolation
  handles it; verified by probing container IPs directly, not names, so the
  result is a packet drop and not a DNS failure.
- Bots **could** reach the host: SSH on 22, and all five GPU tunnel forwards
  (ollama 11434, vLLM 8000/8001, embeddings 7997, reranker 7998), every one
  unauthenticated. `DOCKER-USER` is inbound-only (`-i $EXT_IF`) and never saw
  this traffic; it lands in INPUT, whose policy is ACCEPT and which nothing
  filters — ufw is not even installed here, only its empty chains remain.

**REQ-2 — done.** `deploy/scripts/harden-docker-user.sh` installs a default-deny
from the bot subnet to the host with one exception, applied by the existing
`klai-harden-firewall.service` and persisted through `netfilter-persistent`.
Verified from a container on `vexa12-bots`: SSH and four GPU endpoints closed,
transcription + redis + internet still open. `retrieval-api` on klai-net still
reaches all four GPU endpoints — that path was never in scope and is untouched.

The exception is tcp/8000, the transcription endpoint. `vexa12-meeting-api`
carries `TRANSCRIPTION_SERVICE_URL` pointing at it and sits on the same subnet.
With no bot running there was no way to prove which process actually opens that
connection, and transcription demonstrably works — so it stays open rather than
being closed on a guess. Narrowing it needs REQ-1's capture during a real
meeting. This is a documented hole, not an oversight.

`172.18.0.1:443` also remains reachable, and stays that way: DNAT rewrites it to
the Caddy container before INPUT sees it, so it is FORWARD traffic. Confirmed by
counters — the DROP counter moved by exactly the blocked probes and not by that
one. A bot reaching Caddy has the same access as any host on the internet.

**REQ-1 — dropped, not deferred.** The full-internet allowlist was never built
because Phase 1 needs a ≥4-hour capture across ≥15 meetings on three providers,
and that window never happened in four months. The SPEC's own risk section
explains why: Google, Microsoft and Zoom rotate CDN IPs continuously, so the
allowlist needs a 6-hourly refresh and breaks meetings when it drifts. That is a
permanent maintenance cost against a browser whose job is to visit URLs we do
not control — and the lateral-movement risk it was meant to contain is now
covered by REQ-2 plus Docker's network isolation. Egress to the internet on
443 stays open. If this is revisited, start by measuring, not by enforcing.

REQ-3, REQ-4 and REQ-5 belonged to REQ-1's allowlist and go with it. REQ-5's
conntrack concern does not arise: the rules are subnet-scoped, not per-IP, so a
recycled bot IP is evaluated against the same rule.

**Guard:** `deploy/scripts/tests/harden-docker-user-bot-isolation.test.sh` pins
the shape — subnet read from Docker rather than hardcoded, exception ordered
above the deny, re-runs that do not stack rules, and a loud warning if the
network is missing instead of a silent skip. Verified by mutation: hardcoding
the SPEC's old CIDR fails six assertions, swapping the rule order fails one, and
downgrading the warning fails one.

### v0.2.0 (2026-04-22)
- **Phase 1 infrastructure ready.** `scripts/vexa-egress-capture.sh` wraps
  tcpdump + post-processing (rDNS resolution, per-port distribution, unknown-IP
  extraction). `deploy/vexa/egress-allowlist.txt` skeleton added with
  expected provider groupings. `scripts/update-vexa-ipset.sh` skeleton added
  with a guard that refuses to run on an empty allowlist. Runbook at
  `docs/runbooks/sec-022-egress-capture.md` documents the operator workflow.
- **Prerequisites on core-01 confirmed:** `tcpdump`, `iptables`, `dig` are
  present. `ipset` (required for Phase 3 enforce) is NOT installed yet —
  flagged in the runbook.
- **Open:** operator needs to schedule a ≥4-hour capture window with ≥15
  real meetings spread across Meet / Teams / Zoom. Phase 2 design cannot
  start until that capture produces real data.

### v0.1.0 (2026-04-19)
- Initial draft. Based on audit finding V-008 / F-037 in `.moai/audit/08-vexa.md` plus live verification: `vexa-bots` network has `Internal=false` (bridge driver, gateway 172.27.0.1) — bots currently have unrestricted outbound internet.

---

## Goal

Restrict the outbound network reach of Vexa bot containers (ephemeral Chromium instances spawned by `runtime-api`) to the minimum set of hosts required to join meetings, upload audio, and report status. Today a Chromium RCE on a meeting page would give the attacker arbitrary internet + full access to our internal network via the shared Docker bridge.

---

## Why now

- Bots run Chromium — a large browser attack surface exposed to untrusted meeting pages (third-party guests, screen-shared content).
- A compromised bot has direct network access to: vexa-redis, runtime-api, meeting-api, transcription-api (via tunnels), and arbitrary internet.
- Live check: `docker network inspect vexa-bots` shows `Internal=false`. Confirmed wide-open egress.
- The upside of an allowlist is large; the downside is maintenance (breaks when Google/Microsoft/Zoom change their CDN hostnames).

---

## Success Criteria

1. A documented allowlist of hostnames + IP ranges that bot containers legitimately need, grouped by provider (Google Meet, Microsoft Teams, Zoom, Vexa internal, STUN/TURN).
2. DOCKER-USER iptables rules (in `core-01/scripts/harden-docker-user.sh`) that apply egress filtering to the `vexa-bots` bridge IP range (172.27.0.0/16 currently).
3. Internal network reachability dropped: bots MUST NOT be able to reach `klai-net` containers (portal-api, postgres, qdrant, etc.) or host-localhost services.
4. Bot startup + meeting-join still works for the three major providers (Google Meet, Teams, Zoom) — no regression in `meeting.bot_joined` event rate for 7 days post-deploy.
5. Runbook in `klai-infra/SERVERS.md` explains how to add a new hostname to the allowlist when a provider rotates CDNs.

---

## EARS Requirements

**REQ-1** — WHILE a `vexa-bot` container is running, the system SHALL allow outbound TCP 443 and UDP 443/3478 (STUN/TURN) ONLY to hosts in the allowlist defined in `deploy/vexa/egress-allowlist.txt`.

**REQ-2** — WHILE a `vexa-bot` container is running, the system SHALL block all traffic from the bot container IP range to `klai-net` (172.18.0.0/16), `net-postgres`, and the host's loopback (127.0.0.0/8 as seen from the container → 172.27.0.1).

**REQ-3** — WHEN Vexa meeting provider adds a new CDN hostname not in the allowlist, the system SHALL return observable errors (bot fails to join, logged with DNS lookup failure) so the allowlist can be updated deterministically. No silent degradation.

**REQ-4** — WHILE the allowlist is enforced, a canonical test case (a simulated meeting with each of Google Meet / Teams / Zoom) SHALL run weekly and fail loudly if the allowlist becomes inaccurate.

**REQ-5** — WHEN a bot container exits, the firewall SHALL NOT leak conntrack entries; connections from future bot containers on reused IPs SHALL be evaluated fresh.

---

## Out of scope

- Chromium hardening inside the bot image (separate upstream Vexa concern).
- Private-IP callback blocking for `runtime-api` (V-005 `ALLOW_PRIVATE_CALLBACKS=1` — separate follow-up).
- DNS filtering (would require a DNS proxy; too much scope for this SPEC, iptables-based IP/port filtering is sufficient first-pass).

---

## Approach

### Phase 1: Data collection (2 days)

Before enforcing anything, measure what bots actually reach:

1. Add `tcpdump -i any -n 'src net 172.27.0.0/16 and not dst net 172.18.0.0/16'` capture on core-01 during a 4-hour window that includes at least 5 meetings per provider.
2. Aggregate destinations per provider: resolve IPs to hostnames, group by ASN/CDN.
3. Compile baseline allowlist: google, youtube, googlevideo (Meet), teams.microsoft.com, api.teams.microsoft.com (Teams), zoom.us, cloudfront zoom CDN (Zoom), vexa-internal S3/storage if any, STUN/TURN endpoints.

### Phase 2: Build allowlist infrastructure (1 day)

1. `deploy/vexa/egress-allowlist.txt` — newline-separated CIDR + port list (supported by iptables ipset).
2. `core-01/scripts/update-vexa-ipset.sh` — resolves hostnames to IPs and populates an ipset atomically (no dropped connections during update).
3. Cron: refresh ipset every 6 hours (meeting providers rotate CDN IPs).

### Phase 3: Enforce (half day)

1. Add to `core-01/scripts/harden-docker-user.sh`:
   ```
   iptables -I DOCKER-USER -s 172.27.0.0/16 -m set ! --match-set vexa_allowlist dst,dst -j DROP
   iptables -I DOCKER-USER -s 172.27.0.0/16 -d 172.18.0.0/16 -j DROP    # block klai-net
   iptables -I DOCKER-USER -s 172.27.0.0/16 -d 172.19.0.0/16 -j DROP    # block other internal networks
   ```
2. Deploy via systemd unit `klai-harden-firewall.service` (already exists).

### Phase 4: Monitor (ongoing)

1. Alloy scrapes iptables counters for the DROP rules.
2. Grafana dashboard: "vexa-bots denied destinations" — spike = provider rotated or actual exfil attempt.
3. Weekly canary: scripted bot-spawn → join dummy meeting → verify transcription still works.

---

## Risks

- **False positives during allowlist compilation**: first deploy may block legitimate meeting endpoints, causing bots to fail to join. Mitigation: run Phase 1 long enough; deploy allowlist in log-only mode (`-j LOG`) for 48h before switching to `-j DROP`.
- **Provider CDN IP churn**: Google/Microsoft/Zoom use dynamic CDN IPs (Akamai, Fastly, CloudFront, Cloudflare). Hostname-based resolution via ipset refresh every 6 hours is mandatory. Hard-coding IPs WILL break.
- **Maintenance burden**: weekly canary test + quarterly review. Document owner.
- **STUN/TURN**: WebRTC negotiation uses random UDP ports. If WebRTC media is needed, the allowlist must permit a UDP port range per meeting provider. This SPEC assumes audio-only bot (no video) — verify with Vexa team.

---

## Acceptance tests

1. **Isolation test**: `docker exec <vexa-bot-container> curl -s http://portal-api:8000/health` → connection refused / timeout (blocked).
2. **External reachability**: `docker exec <vexa-bot-container> curl -s https://meet.google.com/` → 200 OK (allowed).
3. **Arbitrary internet blocked**: `docker exec <vexa-bot-container> curl -s https://example.com/` → timeout (not in allowlist).
4. **Real meeting**: spawn a bot for each of Google Meet, Teams, Zoom. Verify transcription completes + is correct.
5. **Metric**: `meeting.bot_joined` event rate for 7 days post-deploy is within 5% of pre-deploy baseline.
