# Infrastructure Patterns

> Hetzner, SOPS secrets, env management, DNS, SSH

---

## sops-overview

**What it is:** Klai uses [Mozilla SOPS](https://github.com/getsops/sops) with [age](https://github.com/FiloSottile/age) encryption to store secrets in git safely. Encrypted files are committed to the repo; plaintext never is.

**Key locations:**

| File | What it contains | Who can decrypt |
|------|-----------------|-----------------|
| `klai-infra/config.sops.env` | Server IPs, SSH config, domain | MacBook only |
| `klai-infra/core-01/.env.sops` | All docker-compose secrets for core-01 | MacBook + core-01 server |
| `klai-infra/core-01/caddy/.env.sops` | Hetzner DNS API token (Caddy TLS) | MacBook + core-01 server |
| `klai-infra/core-01/litellm/.env.sops` | LiteLLM master key, DB password, Mistral API key | MacBook + core-01 server |
| `klai-infra/core-01/zitadel/.env.sops` | Zitadel masterkey, Postgres passwords | MacBook + core-01 server |

**Age key locations:**

| Location | Purpose |
|----------|---------|
| `~/.config/sops/age/keys.txt` (MacBook) | Local development & deploy |
| `~/.config/sops/age/keys.txt` (core-01 server) | Server-side decrypt (future automation) |

Both public keys are registered in `klai-infra/.sops.yaml`. Either key can encrypt or decrypt independently.

---

## sops-secret-edit

**When to use:** Changing an existing secret or adding a new one to core-01

```bash
cd klai-infra

# Open and edit — decrypts in your $EDITOR, re-encrypts on save
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops core-01/.env.sops

# Stage and commit the updated encrypted file
git add core-01/.env.sops
git commit -m "Update SECRET_NAME in core-01 secrets"

# Deploy the updated .env to the server
./core-01/deploy.sh main

# Restart affected services
ssh core-01 'cd /opt/klai && docker compose up -d'
```

---

## sops-secret-add

**When to use:** Adding a brand-new secret to core-01 docker-compose

```bash
cd klai-infra

# 1. Add the variable to the encrypted file
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops core-01/.env.sops
# → add: NEW_SECRET=value-here
# → save and close (SOPS re-encrypts automatically)

# 2. Reference it in docker-compose.yml
#    environment:
#      NEW_SECRET: ${NEW_SECRET}

# 3. Commit both files
git add core-01/.env.sops core-01/docker-compose.yml
git commit -m "Add NEW_SECRET"

# 4. Deploy
./core-01/deploy.sh main
ssh core-01 'cd /opt/klai && docker compose up -d'
```

**For config.sops.env** (server IPs, SSH, domain — MacBook only):
```bash
sops config.sops.env
git add config.sops.env
git commit -m "Update config"
```

---

## sops-disaster-recovery

**When to use:** Server is gone and needs to be rebuilt from scratch

All secrets are in git (encrypted). Full recovery:

```bash
cd klai-infra

# 1. Decrypt and push all secrets to new server
./core-01/deploy.sh all
# Writes: /opt/klai/.env, /opt/klai/caddy/.env,
#         /opt/klai/litellm/.env, /opt/klai/zitadel/.env

# 2. Copy static config files
scp core-01/docker-compose.yml              core-01:/opt/klai/docker-compose.yml
scp core-01/postgres/init.sql               core-01:/opt/klai/postgres/init.sql
scp core-01/litellm/config.yaml             core-01:/opt/klai/litellm/config.yaml
scp core-01/caddy/Caddyfile                 core-01:/opt/klai/caddy/Caddyfile
scp core-01/librechat/librechat.yaml        core-01:/opt/klai/librechat/librechat.yaml
scp core-01/alloy/config.alloy              core-01:/opt/klai/alloy/config.alloy
scp -r core-01/grafana/provisioning/        core-01:/opt/klai/grafana/
scp core-01/scripts/push-health.sh          core-01:/opt/klai/scripts/push-health.sh
ssh core-01 'chmod +x /opt/klai/scripts/push-health.sh'

# 3. Start services
ssh core-01 'cd /opt/klai && docker compose up -d'

# 4. Restore cron job (if not already present)
# Add to crontab for user klai: * * * * * /opt/klai/scripts/push-health.sh
```

**Prerequisite:** Your `~/.config/sops/age/keys.txt` must be present. Without it, nothing can be decrypted.

---

## sops-add-new-server

**When to use:** Provisioning a new server that needs to read SOPS secrets

```bash
# 1. Generate age keypair on the new server
ssh new-server 'age-keygen -o ~/.config/sops/age/keys.txt'

# 2. Get the new server's public key
ssh new-server 'grep "^# public key" ~/.config/sops/age/keys.txt'

# 3. Add it to .sops.yaml under the relevant path_regex
#    - path_regex: new-server/.*\.env$
#      age:
#        - age1... (MacBook)
#        - age1... (new server)

# 4. Re-encrypt all affected files so the new key can decrypt them
cd klai-infra
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops updatekeys core-01/.env.sops
# (or for all files): find . -name "*.sops*" -exec sops updatekeys {} \;

# 5. Commit the re-encrypted files
git add .
git commit -m "Add new-server age key to SOPS recipients"
```

---

## ssh-server-access

**When to use:** Connecting to the Hetzner production server

```bash
# Connect via SSH alias (IP is in klai-infra/config.sops.env)
ssh public-01

# Check running services
docker ps

# Check Coolify logs
docker logs coolify --tail=100 -f
```

**Rule:** Never run destructive commands on the server without verifying the context first.

---

## dns-propagation-check

**When to use:** After a DNS change at Hetzner DNS or Registrar.eu

DNS changes for getklai.com can take up to 24h to propagate fully.
DNS provider: Hetzner DNS (migrated from Cloud86 in March 2026).
Domain registrar: Registrar.eu.

```bash
# Check current DNS resolution
dig getklai.com
dig www.getklai.com

# Check from multiple locations (online tool)
# https://dnschecker.org/#A/getklai.com

# Check if Coolify-managed SSL cert has updated
# Go to: http://public-01:8000 → Proxy → SSL Certificates
```

**Rule:** Always verify DNS propagation before assuming a domain change is live.

---
