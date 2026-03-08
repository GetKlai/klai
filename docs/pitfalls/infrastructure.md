# Infrastructure Pitfalls

> Hetzner, SOPS, environment variables, DNS, SSH

---

## infra-env-not-synced

**Severity:** HIGH

**Trigger:** After adding a new variable to `config.sops.env` and redeploying

SOPS and Coolify are NOT automatically synchronized. Adding a variable to the SOPS file does not make it available to running services.

**What went wrong:**
Service fails after deploy because new env var is missing at runtime, even though it exists in SOPS.

**Why it happens:**
Coolify reads env vars from its own UI configuration, not from `config.sops.env` directly. The SOPS file is the source of truth for secrets management, but Coolify has its own separate env var store.

**Prevention:**
1. After adding to SOPS: also add the same variable in Coolify → Service → Environment Variables
2. Trigger a fresh redeploy after adding env vars
3. Check application logs after deploy to verify the var is being read

**See also:** `patterns/devops.md#coolify-env-update`

---

## infra-sops-missing-main-env

**Severity:** HIGH

**Trigger:** After setting up SOPS for service-specific configs (caddy, litellm, zitadel)

The docker-compose main `.env` file can exist on the server as plaintext only, not backed up in SOPS. If the server is lost, all database passwords, JWT secrets, and OIDC client secrets are gone permanently.

**What went wrong:**
Service-specific `.env.sops` files (caddy, litellm, zitadel) were set up early. The main `/opt/klai/.env` used by docker-compose was generated on the server and never added to SOPS. Disaster recovery would require rotating every secret.

**Why it happens:**
The main `.env` is a flat file that accumulates variables over time. It doesn't map cleanly to one service, so it's easy to overlook when setting up SOPS.

**Prevention:**
`core-01/.env.sops` must be the encrypted backup of `/opt/klai/.env`. Whenever a new secret is added to the server `.env`, update the SOPS file first:
```bash
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops core-01/.env.sops
./core-01/deploy.sh main
```

**See also:** `patterns/infrastructure.md#sops-secret-edit`

---

## infra-sops-dotenv-dollar-sign

**Severity:** HIGH

**Trigger:** Adding a secret containing `$` characters (bcrypt hashes, generated passwords) to a SOPS dotenv file that is used as a docker-compose `.env` file

Bcrypt hashes (e.g. `$2a$14$...`) and some generated secrets contain `$` signs. Docker-compose interprets `$` in `.env` files as variable interpolation. A single `$` causes docker-compose to treat the rest as a variable name, substituting an empty string. The service starts with a blank value and may silently fail or behave incorrectly.

**What went wrong:**
```
GRAFANA_CADDY_HASH=$2a$14$nNgmBc87...
# docker-compose reads this as: GRAFANA_CADDY_HASH= (empty)
# because $2a, $14, $nNgmBc87... are treated as variable expansions
```

**Why it happens:**
SOPS stores the raw plaintext value. When `deploy.sh` decrypts and writes to `/opt/klai/.env`, the raw value (with single `$`) lands in the `.env` file. Docker-compose then interpolates it and the value becomes empty.

**Fix for `.env` on the server:**
```bash
# Use $$ (double dollar) in the .env file for literal $ characters
GRAFANA_CADDY_HASH=$$2a$$14$$nNgmBc87dq1Dkx5XPIVR1...
```

**Fix for storage in `.env.sops`:**
Store the value with `$$` so `deploy.sh` writes the correct escaped form to the server `.env`:
```bash
GRAFANA_CADDY_HASH=$$2a$$14$$nNgmBc87dq1Dkx5XPIVR1...
```

**Diagnosis:**
```bash
# If a service has a blank env var that should contain a hash:
docker exec <container> env | grep <VAR_NAME>
# Expected: $2a$14$...
# Symptom: empty string or variable name fragment
```

**Prevention:**
- When generating bcrypt hashes or any secret with `$`, immediately store it with `$$` in `.env.sops`
- After decrypting and writing to server `.env`, verify the variable value with `docker exec` before restarting dependent services

---

## infra-docker-user-container-ip-stale

**Severity:** CRIT

**Trigger:** Using container IPs in DOCKER-USER iptables rules, then restarting any container in the stack

Docker assigns container IPs dynamically from the bridge subnet. IPs are stable while the stack is running but change when containers restart. DOCKER-USER rules written with a specific container IP (`172.18.0.x`) break silently when the container gets a new IP after a restart.

**What went wrong:**
```bash
# Rules were saved with Caddy at 172.18.0.9:
iptables -A DOCKER-USER -d 172.18.0.9 -i enp5s0 -p tcp -m multiport --dports 80,443 -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -j DROP

# After container restart, Caddy moved to 172.18.0.7.
# All inbound HTTPS traffic now hits the DROP rule — services unreachable from internet.
# push monitors still pass (they run from inside the server), so the outage is not obvious.
```

**Symptoms:**
- External monitors (Uptime Kuma HTTP) timeout; push monitors stay green
- `curl https://chat.getklai.com` times out from an external host
- `docker ps` shows all containers running and healthy
- `curl https://chat.getklai.com` works from within core-01 (bypasses DOCKER-USER)

**Correct approach (port-based rules):**
```bash
# Flush and re-add with port matching, not container IP
iptables -F DOCKER-USER
iptables -A DOCKER-USER -i enp5s0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -p tcp -m multiport --dports 80,443 -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -p udp --dport 443 -j ACCEPT
iptables -A DOCKER-USER -i enp5s0 -j DROP
iptables-save > /etc/iptables/rules.v4
```

Port-based rules match any container on those ports. Docker's own DOCKER chain ensures only explicitly-mapped containers receive the traffic — no additional security risk.

**Script:** `core-01/scripts/harden-docker-user.sh` — run after `docker compose up -d` to (re)apply correct rules.

**Why Docker's DOCKER chain is not enough alone:**
Docker adds ACCEPT rules in the FORWARD chain's DOCKER chain (for mapped ports) and then DROP. Without DOCKER-USER restrictions, all forwarded traffic passes through. Internal networks (Redis, PostgreSQL) are safe because they have no port mappings. But the intent of DOCKER-USER is to be explicit about what external traffic is allowed.

---

## infra-zitadel-x-forwarded-proto

**Severity:** CRIT

**Trigger:** Running Zitadel behind a TLS-terminating reverse proxy (Caddy, nginx, etc.)

Zitadel generates `"api":"http://..."` in `/ui/console/assets/environment.json` regardless of `ZITADEL_EXTERNALSECURE=true`. This is a known upstream bug (zitadel/zitadel#8675): Zitadel derives the API scheme from the incoming connection to itself (plain HTTP from the reverse proxy), not from the `EXTERNALSECURE` config. The `issuer` URL is correctly generated as `https://` from config, but the `api` field uses the request scheme.

**What breaks:**
The Zitadel Angular console makes gRPC-Web API calls to `http://` URLs from an HTTPS page. Browsers block these as CSP violations or mixed content. The console loads but all API calls fail — users see a blank or broken admin UI.

**Why it seems to work until it doesn't:**
Before any CSP is present, browsers may silently auto-upgrade `http://` → `https://` for same-origin requests. The moment any CSP is introduced (even Zitadel's own), `connect-src 'self'` on an HTTPS page does not match `http://` URLs — they are blocked.

**Fix — Caddy:**
Explicitly forward `X-Forwarded-Proto: https` in the Zitadel reverse_proxy block:
```caddyfile
handle @auth {
    reverse_proxy zitadel:8080 {
        header_up X-Forwarded-Proto "https"
    }
}
```

**Verification:**
```bash
curl -s https://auth.getklai.com/ui/console/assets/environment.json
# Must show: "api":"https://auth.getklai.com"
# Wrong:     "api":"http://auth.getklai.com"
```

**Do NOT fix this with CSP changes.** The root cause is a wrong scheme in environment.json. Patching CSP (upgrade-insecure-requests, adding http: to connect-src, removing CSP) are workarounds that mask the problem or introduce security regressions.

---

## infra-caddy-no-global-csp

**Severity:** HIGH

**Trigger:** Adding a Content-Security-Policy header to Caddy's global `header {}` block

A global CSP at the reverse proxy level is not industry standard and will break applications that manage their own CSP. Zitadel, LibreChat, and similar apps set application-specific CSP headers. A generic Caddy CSP overrides these and breaks the apps.

**What breaks:**
Any app whose own CSP is more permissive than the generic Caddy CSP will fail. Zitadel's Angular console in particular makes gRPC-Web calls that require `connect-src 'self' auth.getklai.com` — a generic CSP without this exact allowance blocks all API calls.

**Correct split:**
Caddy's global `header {}` block is appropriate for **transport-level security headers** that are safe to apply globally:
```caddyfile
header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains"
    X-Content-Type-Options "nosniff"
    X-Frame-Options "SAMEORIGIN"
    Referrer-Policy "strict-origin-when-cross-origin"
    Permissions-Policy "geolocation=(), microphone=(), camera=()"
    -Server
}
```

Do NOT add `Content-Security-Policy` here. Each application sets its own.

---

*(Add more entries here with `/retro "description"` after infrastructure incidents.)*
