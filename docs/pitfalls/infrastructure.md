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

*(Add more entries here with `/retro "description"` after infrastructure incidents.)*
