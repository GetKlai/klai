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

## infra-zitadel-console-http-api

**Severity:** CRIT

**Trigger:** Zitadel console is broken -- all API calls fail, blank or non-functional admin UI at `auth.getklai.com`

**Symptom:**
`/ui/console/assets/environment.json` returns `"api":"http://auth.getklai.com"` instead of `"https://"`. The Angular console makes gRPC-Web calls to `http://` URLs from an HTTPS page. Browsers block these as mixed content. The moment any CSP is present (even Zitadel's own), `connect-src 'self'` on an HTTPS page does not match `http://` URLs.

**Root cause:**
`--tlsMode disabled` as a CLI flag in the compose `command:` overrides `ZITADEL_EXTERNALSECURE=true`. These are different internal config keys. The startup logs will show:

```
External Secure: false   <-- wrong, means api URL will be http://
```

Even though `ZITADEL_EXTERNALSECURE=true` is set, the CLI flag wins.

**Fix:**
1. Remove `--tlsMode disabled` from the compose `command:`
2. Add `ZITADEL_TLS_ENABLED: "false"` as an explicit environment variable (this is the env var equivalent -- it tells Zitadel not to terminate TLS itself, which is correct behind a proxy)
3. Use `h2c://zitadel:8080` in the Caddy reverse_proxy (official Zitadel/Caddy pattern for HTTP/2 cleartext)

```yaml
# docker-compose.yml
zitadel:
  command: start-from-init --masterkey ${ZITADEL_MASTERKEY}  # no --tlsMode disabled
  environment:
    ZITADEL_TLS_ENABLED: "false"      # replaces --tlsMode disabled
    ZITADEL_EXTERNALSECURE: "true"    # public URL is https://
    ZITADEL_EXTERNALDOMAIN: auth.getklai.com
    ZITADEL_EXTERNALPORT: "443"
```

```caddyfile
# Caddyfile
handle @auth {
    reverse_proxy h2c://zitadel:8080
}
```

**Verification:**
```bash
curl -s https://auth.getklai.com/ui/console/assets/environment.json
# Must show: "api":"https://auth.getklai.com"
# Wrong:     "api":"http://auth.getklai.com"
```

Check startup logs for confirmation:
```bash
docker logs klai-core-zitadel-1 2>&1 | grep "External Secure"
# Must show: External Secure: true
```

**Do NOT fix this with:**
- `header_up X-Forwarded-Proto "https"` in Caddy (does not override the CLI flag)
- CSP changes (`upgrade-insecure-requests`, adding `http:` to `connect-src`) -- these mask the symptom and introduce security regressions
- Restarting Zitadel without changing the command/env -- the flag is read at startup

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

## infra-never-modify-env-secrets

**Severity:** CRIT

**Trigger:** Any session that needs to add, change, or fix a variable in `/opt/klai/.env` on core-01

NEVER modify existing secret values (PATs, API tokens, passwords) in `/opt/klai/.env` via shell commands like `echo >>`, `sed -i`, or manual editing. These operations are fragile: dollar signs get interpolated, values get truncated, and the result is a silently broken deployment where every auth call returns 401.

**What has gone wrong (multiple times):**
1. A session used `echo "VAR=value$with_dollar"` -- the shell ate the `$with_dollar` part
2. A session used `sed -i` to update a PAT -- the replacement value was wrong/truncated
3. The portal-api kept running with the corrupted PAT, returning 401 on every login attempt
4. Diagnosing the issue is hard because the server appears healthy and the PAT "looks" valid

**Rules:**
1. **NEVER overwrite** `PORTAL_API_ZITADEL_PAT`, `PORTAL_API_DB_PASSWORD`, or any other existing secret
2. **Adding a NEW variable** is allowed but must use single-quoted values: `echo 'NEW_VAR=value' >> /opt/klai/.env`
3. If a secret needs changing, tell the user to do it manually or use SOPS
4. After ANY `.env` change, verify the value inside the container: `docker exec <container> printenv VAR_NAME`

**If login breaks with "Errors.Token.Invalid":**
The PAT in the `.env` is almost certainly corrupted. Ask the user for the correct value from the Zitadel console. Do NOT assume the PAT expired -- it expires in 2030.

---

*(Add more entries here with `/retro "description"` after infrastructure incidents.)*
