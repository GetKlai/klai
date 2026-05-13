---
paths:
  - "klai-infra/**"
  - "deploy/**"
---
# Servers, Network & DNS

## Critical rules

**Production server (CRIT):**
- NEVER use direct IP — firewall blocks it. Always use the SSH alias.
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

Server IPs and access details are stored in the private `klai-infra` repository.
See `klai-infra/SERVERS.md` for the full inventory (core-01, public-01, gpu-01).

## SSH access

Always use the configured SSH alias (`ssh core-01`, etc.) — never direct IP.
SSH keys and jump-host configuration are documented in `klai-infra/SERVERS.md`.

## GPU tunnels

All GPU services tunneled via autossh from core-01. Check: `pgrep -a autossh` on core-01.
Connection details in `klai-infra/SERVERS.md`.

## DNS
Provider: Hetzner DNS. Registrar: Registrar.eu.
Propagation: up to 24h. Check: `dig getklai.com` or dnschecker.org.

## Coolify (public-01)
Env vars: update SOPS + Coolify UI separately (not auto-synced).
Always check build logs after redeploy — trigger ≠ success.

## Portal URL (CRIT — never guess this)
**`https://my.getklai.com`** — this is where ALL users log in. One URL for everyone.
- `{tenant}.getklai.com` = per-tenant portal view (e.g. `getklai.getklai.com` = the "getklai" tenant — NOT the portal)
- `FRONTEND_URL` in portal-api env MUST be `https://my.getklai.com`
- OAuth redirect URIs (Google, Microsoft) MUST point to `https://my.getklai.com/api/oauth/.../callback`
- Do NOT assume the portal URL from Caddy wildcard routing or Zitadel redirect URI config
- `config.py` fallback (`https://portal.{domain}`) is wrong for production — FRONTEND_URL must be explicit
- Verify: `docker exec portal-api printenv FRONTEND_URL` — must return `https://my.getklai.com`

## gpu-01 services — no CI workflow (HIGH)

Some gpu-01 services (`bge-m3-sparse`, possibly others) do NOT have a GitHub Actions workflow. Image rebuilds are manual via SSH.

**Services without CI:** `bge-m3-sparse` (`deploy/bge-m3-sparse/`).

**Manual rebuild sequence (from core-01 via jump):**
```bash
cd /opt/klai
docker compose build bge-m3-sparse
docker compose up -d bge-m3-sparse
docker logs --tail 20 klai-gpu-bge-m3-sparse-1
```

**Prevention:** Before assuming CI handles a gpu-01 service rebuild, check `.github/workflows/` for a matching workflow file. If none exists, plan a manual SSH deploy.

## Disaster recovery
All secrets in git (SOPS-encrypted). Full recovery: `deploy.sh all` → scp configs → `docker compose up -d`.
Prerequisite: `~/.config/sops/age/keys.txt` must be present.
Full procedure: `klai-infra/SERVERS.md` § Disaster recovery.
