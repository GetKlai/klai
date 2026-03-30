---
paths:
  - "**/.env*"
  - ".github/**/*.yml"
  - "**/docker-compose*.yml"
  - "klai-infra/**"
---
# Infrastructure Patterns

> Hetzner, SOPS secrets, env management, DNS, SSH

## Index
> Keep this index in sync — add a row when adding a pattern below.

| Pattern | When to use |
|---|---|
| [sops-overview](#sops-overview) | Understanding the SOPS secret management setup |
| [env-modification-rules](#env-modification-rules) | Adding or changing variables in `/opt/klai/.env` |
| [sops-secret-edit](#sops-secret-edit) | Editing an existing secret in a SOPS file |
| [sops-secret-add](#sops-secret-add) | Adding a new secret to a SOPS file |
| [sops-non-interactive](#sops-non-interactive) | Adding secrets to SOPS without an interactive editor (AI/automation) |
| [sops-per-service](#sops-per-service) | Creating a per-service `.env.sops` file (e.g. klai-mailer) |
| [sops-disaster-recovery](#sops-disaster-recovery) | Recovering access after losing the age key |
| [sops-add-new-server](#sops-add-new-server) | Adding a new server to the SOPS key ring |
| [ssh-server-access](#ssh-server-access) | SSH access to any Klai server |
| [dns-propagation-check](#dns-propagation-check) | Checking DNS propagation for new records |

---

## sops-overview

**What it is:** Klai uses [Mozilla SOPS](https://github.com/getsops/sops) with [age](https://github.com/FiloSottile/age) encryption to store secrets in git safely. Encrypted files are committed to the repo; plaintext never is.

**Key locations:**

| File | What it contains | Who can decrypt |
|------|-----------------|-----------------|
| `klai-infra/config.sops.env` | Server IPs, SSH config, domain | MacBook only |
| `klai-infra/core-01/.env.sops` | Global docker-compose secrets for core-01 (REDIS_PASSWORD, GITHUB_ADMIN_PAT, GLITCHTIP_* etc.) | MacBook + core-01 server |
| `klai-infra/core-01/caddy/.env.sops` | Hetzner DNS API token (Caddy TLS) | MacBook + core-01 server |
| `klai-infra/core-01/litellm/.env.sops` | LiteLLM master key, DB password, Mistral API key | MacBook + core-01 server |
| `klai-infra/core-01/zitadel/.env.sops` | Zitadel masterkey, Postgres passwords | MacBook + core-01 server |
| `klai-infra/core-01/klai-mailer/.env.sops` | SMTP credentials, webhook secrets for klai-mailer | MacBook + core-01 server |

**Per-service deployment:** `deploy.sh <service>` decrypts the matching `core-01/<service>/.env.sops` and writes it to `/opt/klai/<service>/.env` on the server (separate from the global `/opt/klai/.env`). Services: `zitadel`, `litellm`, `caddy`, `klai-mailer`.

**Age key locations:**

| Location | Purpose |
|----------|---------|
| `~/.config/sops/age/keys.txt` (MacBook) | Local development & deploy |
| `~/.config/sops/age/keys.txt` (core-01 server) | Server-side decrypt (future automation) |

Both public keys are registered in `klai-infra/.sops.yaml`. Either key can encrypt or decrypt independently.

---

## env-modification-rules

**When to use:** ANY time you need to add or change a variable in `/opt/klai/.env` on core-01

**See also:** pitfall `infra-never-modify-env-secrets` for what goes wrong when these rules are not followed.

| Action | Allowed? | How |
|--------|----------|-----|
| Add a NEW variable | Yes | `ssh core-01 "echo 'NEW_VAR=value' >> /opt/klai/.env"` (single quotes!) |
| Change an existing secret | NO | Ask the user, or use SOPS (`sops-secret-edit` pattern below) |
| Delete a variable | NO | Ask the user |
| Read a variable | Yes | `ssh core-01 'grep "^VAR_NAME=" /opt/klai/.env'` |

After ANY `.env` change:
1. Restart the service: `ssh core-01 'cd /opt/klai && docker compose up -d <service>'`
2. Verify inside the container: `ssh core-01 'docker exec <container> printenv VAR_NAME'`
3. Update SOPS: add the new variable to `core-01/.env.sops` so the encrypted backup stays in sync

---

## sops-secret-edit

**When to use:** Changing an existing secret or adding a new one to core-01

**Important:** This is the ONLY safe way to change existing secrets. Never use `sed -i` or `echo` to overwrite secret values on the server.

```bash
cd klai-infra

# Open and edit -- decrypts in your $EDITOR, re-encrypts on save
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

## sops-non-interactive

**When to use:** Adding secrets to an EXISTING SOPS file without an interactive editor — required when running in AI agent sessions, CI, or any non-TTY context where `$EDITOR` is unavailable.

**Why the interactive editor fails in agents:**
`sops core-01/.env.sops` opens your `$EDITOR`. In agent sessions there is no TTY, so the editor invocation hangs or fails. Use the decrypt-modify-encrypt approach instead.

**Key rule:** The temp file path MUST match the `.sops.yaml` `path_regex` (`core-01/.*\.env(\.sops)?$`) for SOPS to pick up the correct encryption keys. Always use the full explicit key path — `$HOME` expansion is unreliable in non-interactive shells.

```bash
cd klai-infra

# 1. Decrypt to a temp file at a path matching the .sops.yaml regex
SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/.env.sops > core-01/.new.env

# 2. Append the new variable (use Python to avoid shell escaping issues)
python3 -c "
with open('core-01/.new.env', 'a') as f:
    f.write('NEW_VAR=my-value-here\n')
"

# 3. Encrypt in-place (SOPS uses the file path to find creation rules)
SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --encrypt --in-place --input-type dotenv --output-type dotenv \
    core-01/.new.env

# 4. Replace the original
mv core-01/.new.env core-01/.env.sops

# 5. Verify
SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/.env.sops | grep NEW_VAR

# 6. Commit and deploy
git add core-01/.env.sops
git commit -m "feat(secrets): add NEW_VAR to core-01 SOPS"
git push
./core-01/deploy.sh main
```

**Common mistakes:**
- Using `$HOME` instead of the literal path (`/Users/mark/.config/sops/age/keys.txt`) — `$HOME` is empty in non-interactive shells
- Writing the temp file to a path that does NOT match the regex (e.g. `/tmp/new.env`) — SOPS can't find the creation rules and creates a new encrypted file with no recipients
- Using `sops edit` — requires interactive TTY

---

## sops-per-service

**When to use:** Creating or updating a per-service `.env.sops` file (e.g. `core-01/klai-mailer/.env.sops`)

Per-service SOPS files are separate from the global `core-01/.env.sops`. They are deployed by `deploy.sh <service>` to `/opt/klai/<service>/.env` on the server.

**Creating a new per-service file from scratch:**

```bash
cd klai-infra

# 1. Write plaintext to a path matching the .sops.yaml regex
#    The regex is: core-01/.*\.env(\.sops)?$
#    So core-01/klai-mailer/.env matches!
mkdir -p core-01/klai-mailer
cat > core-01/klai-mailer/.env << 'EOF'
SMTP_HOST=shared199.cloud86-host.io
SMTP_USER=hello@getklai.com
SMTP_PASSWORD=my-smtp-password
WEBHOOK_SECRET=my-webhook-secret
EOF

# 2. Encrypt in-place (SOPS reads path → finds creation rules → uses correct keys)
SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --encrypt --in-place --input-type dotenv --output-type dotenv \
    core-01/klai-mailer/.env

# 3. Rename to .env.sops
mv core-01/klai-mailer/.env core-01/klai-mailer/.env.sops

# 4. Verify decryption works
SOPS_AGE_KEY_FILE=/Users/mark/.config/sops/age/keys.txt \
    sops --decrypt --input-type dotenv --output-type dotenv \
    core-01/klai-mailer/.env.sops

# 5. Deploy the service env to the server
./core-01/deploy.sh klai-mailer
# → writes /opt/klai/klai-mailer/.env on core-01

# 6. Commit
git add core-01/klai-mailer/.env.sops
git commit -m "feat(secrets): add klai-mailer service secrets"
git push
```

**Supported services for `deploy.sh <service>`:** `main`, `zitadel`, `litellm`, `caddy`, `klai-mailer`, `all`

**Why the path matters:** SOPS reads `.sops.yaml` from the current working directory upward. The file `core-01/klai-mailer/.env` matches the regex `core-01/.*\.env(\.sops)?$` in `klai-infra/.sops.yaml`, so SOPS automatically uses the correct age recipients. A file at `/tmp/klai-mailer.env` would NOT match and SOPS would fail or use wrong keys.

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
