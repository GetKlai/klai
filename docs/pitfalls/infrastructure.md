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

*(Add more entries here with `/retro "description"` after infrastructure incidents.)*
