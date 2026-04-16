---
paths:
  - "klai-infra/**"
  - "deploy/**"
---
# Servers, Network & DNS

## Critical rules

**core-01 (CRIT):**
- NEVER use direct IP — firewall blocks it. Always `ssh core-01` alias.
- NEVER retry with different key/user — fail2ban bans after failed attempts.

**iptables / DOCKER-USER (CRIT):**
Container IPs change on restart — NEVER hardcode in rules. Port-based only:
```bash
iptables -A DOCKER-USER -i enp5s0 -p tcp -m multiport --dports 80,443 -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -j DROP
```
Script: `core-01/scripts/harden-docker-user.sh`. Systemd: `klai-harden-firewall.service`.

**Docker image versions (CRIT):**
Never use versions from AI training data. Always `WebSearch` current stable.
Never `:latest` in production — pin explicit versions. Exception: PostgreSQL pinned to `pg17`.

## Server inventory
| Server | IP | Type | Purpose | Cost |
|--------|-----|------|---------|------|
| core-01 | `65.21.174.162` | Hetzner EX44 (dedicated) | AI stack, portal, main services | €47/mo |
| public-01 | `65.109.237.64` | Hetzner CX42 (cloud) | Coolify, Uptime Kuma, Umami | €17/mo |
| gpu-01 | `5.9.10.215` | Hetzner GEX44 (RTX 4000 Ada 20GB) | GPU inference (TEI, whisper, reranker) | ~€100/mo |

IPs also stored encrypted in `klai-infra/config.sops.env`. All Helsinki HEL1.

## SSH access
| Server | Command | Notes |
|--------|---------|-------|
| core-01 | `ssh core-01` | ALWAYS alias, NEVER direct IP (firewall) |
| public-01 | `ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64` | Root + klai_ed25519 |
| gpu-01 | Via core-01 only: `ssh -i /opt/klai/gpu-tunnel-key root@5.9.10.215` | No direct MacBook access |

## GPU tunnels (gpu-01 → core-01)
All GPU services tunneled via autossh. core-01 reaches gpu-01 at `172.18.0.1:{port}`.
Check: `pgrep -a autossh` on core-01. Key: `/opt/klai/gpu-tunnel-key`.

## DNS
Provider: Hetzner DNS (migrated from Cloud86, March 2026). Registrar: Registrar.eu.
Propagation: up to 24h. Check: `dig getklai.com` or dnschecker.org.

## Coolify (public-01)
Env vars: update SOPS + Coolify UI separately (not auto-synced).
Always check build logs after redeploy — trigger ≠ success.

## Portal URL (CRIT — never guess this)
**`https://my.getklai.com`** — this is where ALL users log in. One URL for everyone.
- `{tenant}.getklai.com` = per-tenant portal view (e.g. `getklai.getklai.com` = the "getklai" tenant)
- `FRONTEND_URL` in portal-api env MUST be `https://my.getklai.com`
- OAuth redirect URIs (Google, Microsoft) MUST point to `https://my.getklai.com/api/oauth/.../callback`
- Do NOT assume the portal URL from Caddy wildcard routing or Zitadel redirect URI config

## Disaster recovery
All secrets in git (SOPS-encrypted). Full recovery: `deploy.sh all` → scp configs → `docker compose up -d`.
Prerequisite: `~/.config/sops/age/keys.txt` must be present.
Full procedure: `klai-infra/SERVERS.md` § Disaster recovery.
