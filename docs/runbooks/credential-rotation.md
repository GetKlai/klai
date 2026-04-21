# Secret Rotation Runbook

> **AI playbook** — follow these steps when a compromised image or credential leak is suspected.
> Written for Claude to execute autonomously. All commands run from the MacBook unless marked `[core-01]`.
>
> Trigger scenario: a Docker image in the Klai stack contained malicious code with access to container env vars
> (e.g. the LiteLLM supply chain attack of March 2026).

---

## Step 0 — Triage: what was exposed?

Before rotating anything, determine which container was compromised and which env vars it had access to.

```bash
# Which image was affected?
ssh core-01 "docker inspect klai-core-<service>-1 --format '{{.Config.Image}}'"

# What env vars did it have? (do NOT run on a container you suspect is still running malicious code)
ssh core-01 "docker inspect klai-core-<service>-1 --format '{{json .Config.Env}}' | jq '.[]'"
```

**Exposure map by service:**

| Container | Secrets it can see |
|---|---|
| `klai-core-litellm-1` | `LITELLM_MASTER_KEY`, `LITELLM_DB_PASSWORD`, `MISTRAL_API_KEY`, `DATABASE_URL` |
| `klai-core-zitadel-1` | `ZITADEL_MASTERKEY`, `ZITADEL_DB_PASSWORD`, `POSTGRES_PASSWORD` |
| `klai-core-postgres-1` | `POSTGRES_PASSWORD`, all DB passwords |
| `klai-core-redis-1` | `REDIS_PASSWORD` |
| Any portal-api, retrieval-api, etc. | Reads from global `/opt/klai/.env` — see full list there |

---

## Step 1 — Stop the compromised container

```bash
ssh core-01 "cd /opt/klai && docker compose stop <service>"
```

Do NOT remove it yet — keep the container for forensics (logs, inspect).

---

## Step 2 — Rotate MISTRAL_API_KEY (external — do this first)

The Mistral API key is billable and can be used externally the moment it leaks.

1. Go to [console.mistral.ai](https://console.mistral.ai) → API Keys
2. Revoke the current key immediately
3. Create a new key — copy the value
4. Update in SOPS (non-interactive pattern):

```bash
cd ~/Server/projects/klai/klai-infra

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/litellm/.env.sops > core-01/litellm/.new.env

python3 -c "
import re, sys
content = open('core-01/litellm/.new.env').read()
content = re.sub(r'^MISTRAL_API_KEY=.*$', 'MISTRAL_API_KEY=<new-key>', content, flags=re.MULTILINE)
open('core-01/litellm/.new.env', 'w').write(content)
"

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --encrypt --in-place --input-type dotenv --output-type dotenv \
    core-01/litellm/.new.env

mv core-01/litellm/.new.env core-01/litellm/.env.sops

# Verify
SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/litellm/.env.sops | grep MISTRAL_API_KEY

git add core-01/litellm/.env.sops
git commit -m "security: rotate MISTRAL_API_KEY"
git push
```

---

## Step 3 — Rotate LITELLM_MASTER_KEY

This key authorizes all AI requests through the LiteLLM proxy. Rotate immediately.

```bash
# Generate a new key
NEW_KEY=$(openssl rand -hex 32)
echo "New LITELLM_MASTER_KEY: $NEW_KEY"  # copy this

cd ~/Server/projects/klai/klai-infra

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/litellm/.env.sops > core-01/litellm/.new.env

python3 -c "
import re
content = open('core-01/litellm/.new.env').read()
content = re.sub(r'^LITELLM_MASTER_KEY=.*$', 'LITELLM_MASTER_KEY=<new-key>', content, flags=re.MULTILINE)
open('core-01/litellm/.new.env', 'w').write(content)
"

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --encrypt --in-place --input-type dotenv --output-type dotenv \
    core-01/litellm/.new.env

mv core-01/litellm/.new.env core-01/litellm/.env.sops

git add core-01/litellm/.env.sops
git commit -m "security: rotate LITELLM_MASTER_KEY"
git push
```

**After deploying (step 6):** any service using a virtual key (portal-api, retrieval-api, research-api) continues to work — virtual keys are scoped under the master key but are not the master key itself. Only direct master key usage breaks.

---

## Step 4 — Rotate LITELLM_DB_PASSWORD

```bash
# Generate new password (no special chars — goes into a URL)
NEW_PW=$(openssl rand -base64 24 | tr -d '/+=')
echo "New LITELLM_DB_PASSWORD: $NEW_PW"

# 1. Update in postgres
ssh core-01 "docker exec klai-core-postgres-1 psql -U postgres -c \
  \"ALTER USER litellm WITH PASSWORD '$NEW_PW';\""

# 2. Update in SOPS
cd ~/Server/projects/klai/klai-infra

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/litellm/.env.sops > core-01/litellm/.new.env

python3 -c "
import re
content = open('core-01/litellm/.new.env').read()
content = re.sub(r'^LITELLM_DB_PASSWORD=.*$', 'LITELLM_DB_PASSWORD=<new-pw>', content, flags=re.MULTILINE)
open('core-01/litellm/.new.env', 'w').write(content)
"

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --encrypt --in-place --input-type dotenv --output-type dotenv \
    core-01/litellm/.new.env

mv core-01/litellm/.new.env core-01/litellm/.env.sops

git add core-01/litellm/.env.sops
git commit -m "security: rotate LITELLM_DB_PASSWORD"
git push
```

---

## Step 5 — Rotate Zitadel postgres password (if Zitadel was exposed)

Only needed if `klai-core-zitadel-1` or `klai-core-postgres-1` was the compromised container.

```bash
NEW_PW=$(openssl rand -base64 24 | tr -d '/+=')

# 1. Update in postgres
ssh core-01 "docker exec klai-core-postgres-1 psql -U postgres -c \
  \"ALTER USER zitadel WITH PASSWORD '$NEW_PW';\""

# 2. Update in SOPS
cd ~/Server/projects/klai/klai-infra

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/zitadel/.env.sops > core-01/zitadel/.new.env

python3 -c "
import re
content = open('core-01/zitadel/.new.env').read()
content = re.sub(r'^ZITADEL_DB_PASSWORD=.*$', 'ZITADEL_DB_PASSWORD=<new-pw>', content, flags=re.MULTILINE)
open('core-01/zitadel/.new.env', 'w').write(content)
"

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --encrypt --in-place --input-type dotenv --output-type dotenv \
    core-01/zitadel/.new.env

mv core-01/zitadel/.new.env core-01/zitadel/.env.sops

git add core-01/zitadel/.env.sops
git commit -m "security: rotate ZITADEL_DB_PASSWORD"
git push
```

---

## Step 6 — Deploy updated secrets and restart services

```bash
cd ~/Server/projects/klai/klai-infra

# Deploy updated per-service secrets to server
./core-01/deploy.sh main

# Restart in dependency order
ssh core-01 "cd /opt/klai && docker compose up -d litellm"
ssh core-01 "docker logs --tail 20 klai-core-litellm-1"

# If zitadel was rotated:
ssh core-01 "cd /opt/klai && docker compose up -d zitadel"
ssh core-01 "docker logs --tail 20 klai-core-zitadel-1"
```

---

## Step 7 — Verify

```bash
# LiteLLM health
ssh core-01 "curl -s http://localhost:4000/health"
# Expected: {"status":"healthy",...}

# LiteLLM master key works
ssh core-01 "curl -s http://localhost:4000/v1/models \
  -H 'Authorization: Bearer <new-master-key>' | jq '.data[].id' | head -3"

# Zitadel health (if rotated)
ssh core-01 "curl -s http://localhost:8080/debug/healthz"

# Portal login still works — try logging in at https://portal.klai.nl
```

---

## Step 8 — Rotate REDIS_PASSWORD (if redis was exposed)

Redis password is in the global `core-01/.env.sops`. Every service that uses Redis would need a restart.

```bash
NEW_PW=$(openssl rand -base64 24 | tr -d '/+=')

# 1. Update in redis (requires CONFIG REWRITE or restart)
ssh core-01 "docker exec klai-core-redis-1 redis-cli -a \"\$REDIS_PASSWORD\" \
  CONFIG SET requirepass '$NEW_PW'"

# 2. Update global SOPS
cd ~/Server/projects/klai/klai-infra

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/.env.sops > core-01/.new.env

python3 -c "
import re
content = open('core-01/.new.env').read()
content = re.sub(r'^REDIS_PASSWORD=.*$', 'REDIS_PASSWORD=<new-pw>', content, flags=re.MULTILINE)
open('core-01/.new.env', 'w').write(content)
"

SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --encrypt --in-place --input-type dotenv --output-type dotenv \
    core-01/.new.env

mv core-01/.new.env core-01/.env.sops

git add core-01/.env.sops
git commit -m "security: rotate REDIS_PASSWORD"
git push

# 3. Restart all services that use Redis
# WARNING: use container-preflight pattern before each up -d
ssh core-01 "cd /opt/klai && docker compose up -d litellm librechat portal-api"
```

---

## Step 9 — Post-incident

1. **Remove the stopped container** after forensics: `ssh core-01 "docker rm klai-core-<service>-1"`
2. **Pin the image** to a known-good version in `deploy/docker-compose.yml` — do not restart with the same compromised tag
3. **Check GlitchTip** for unusual errors in the hours before/after the incident window
4. **Check VictoriaLogs** for outbound traffic anomalies from the compromised container
5. **Notify tenants** if user data (messages, documents) was accessible to the compromised container

---

## Reference: secret → SOPS file mapping

| Secret | SOPS file | Affects |
|---|---|---|
| `MISTRAL_API_KEY` | `core-01/litellm/.env.sops` | All AI inference |
| `LITELLM_MASTER_KEY` | `core-01/litellm/.env.sops` | All AI requests via proxy |
| `LITELLM_DB_PASSWORD` | `core-01/litellm/.env.sops` | LiteLLM state (virtual keys, spend) |
| `ZITADEL_MASTERKEY` | `core-01/zitadel/.env.sops` | All auth tokens — **do not rotate unless certain** |
| `ZITADEL_DB_PASSWORD` | `core-01/zitadel/.env.sops` | Zitadel DB connection |
| `REDIS_PASSWORD` | `core-01/.env.sops` | LibreChat sessions, LiteLLM cache |
| `PORTAL_API_ZITADEL_PAT` | `core-01/.env.sops` | Portal → Zitadel API calls (tenant provisioning, login sessions) |
| `ZITADEL_ADMIN_PAT` | `core-01/.env.sops` | Runbook/CI → Zitadel instance admin API (feature flags, OIDC apps, IAM) |

**Note on ZITADEL_MASTERKEY:** rotating this invalidates all existing OIDC tokens — every user gets logged out across all tenants. Only do this if you have direct evidence Zitadel itself was compromised. See `runbooks/platform-recovery.md#zitadel-login-v2-recovery` for recovery after Zitadel issues.
