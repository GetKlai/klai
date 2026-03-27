# DevOps Patterns

> Coolify deployments, Docker, service management, CI/CD

---

## docker-compose-sync

**When to use:** Adding or removing a service in `deploy/docker-compose.yml`

The CI service workflows (`knowledge-ingest.yml`, `retrieval-api.yml`, etc.) do NOT copy the compose file to the server — they only pull the new image and restart the specific service. The compose file must be synced separately.

**Automated sync:** The `deploy-compose.yml` workflow runs automatically when `deploy/docker-compose.yml` changes on main. It does a sparse checkout and copies the file to `/opt/klai/docker-compose.yml`.

**Manual sync (emergency):**
```bash
scp "$(git rev-parse --show-toplevel)/deploy/docker-compose.yml" core-01:/opt/klai/docker-compose.yml
```

**After syncing:** Restart any services whose definition changed:
```bash
ssh core-01 "cd /opt/klai && docker compose up -d <service>"
```

**New services:** After adding a new service to the compose file, the automated sync will copy the file, but you must still start the service manually the first time (CI only restarts services it knows about).

---

## coolify-env-update

**When to use:** Adding or changing an environment variable for a Coolify service

Variables set in `klai-infra/config.sops.env` are NOT automatically synced to Coolify.
Always update both places.

**Steps:**

```bash
# 1. Add to SOPS env file
cd klai-infra
sops config.sops.env
# Add: NEW_VAR=value

# 2. Update in Coolify UI
# Go to: http://public-01:8000 → Service → Environment Variables
# Add the same variable there

# 3. Trigger redeploy from Coolify (required for new env vars to take effect)
```

**Rule:** SOPS is the source of truth for secrets. Coolify needs a manual sync.

**See also:** `klai-claude/docs/pitfalls/infrastructure.md#infra-env-not-synced`

---

## public-01-ssh

**When to use:** Accessing public-01 (Coolify host, Uptime Kuma) via SSH

```bash
# Use klai_ed25519 key as root — NOT markv, NOT id_ed25519
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64
```

`markv@65.109.237.64` with `id_ed25519` is rejected. Root + klai_ed25519 is the working combination.

---

## core-01-ssh

**When to use:** Accessing core-01 (AI stack, portal, vexa) via SSH

```bash
# ALWAYS use the SSH config alias — NEVER direct IP
ssh core-01
```

**Critical rules:**
- NEVER use `ssh klai@65.21.174.162` or `ssh root@65.21.174.162` — direct IP connections time out (firewall/routing)
- NEVER try multiple key/user combinations — **fail2ban is active** and will ban the IP after failed attempts
- The SSH config alias handles the correct key, user, and routing automatically
- If `ssh core-01` times out or returns "Permission denied", check: are you using the correct terminal/SSH agent? Do not retry with different flags.

**fail2ban jail:** `sshd` — active, has banned 2000+ IPs historically from brute-force attempts.

---

## coolify-redeploy

**When to use:** After a config change, env var update, or to apply new code

```bash
# Via Coolify UI: Service → Deploy → Redeploy
# Or via Coolify API (if configured):
curl -X POST http://public-01:8000/api/v1/deploy \
  -H "Authorization: Bearer $COOLIFY_TOKEN" \
  -d '{"uuid": "SERVICE_UUID"}'
```

**Rule:** Always check build logs after redeploy. A successful trigger does not mean a successful deploy.

---

## docker-rebuild-no-cache

**When to use:** After updating a dependency, changing a base image, or when stale layers cause issues

```bash
# Force full rebuild without cache
docker build --no-cache -t service-name .

# Or via docker-compose
docker compose build --no-cache service-name
docker compose up -d service-name
```

**Rule:** Use `--no-cache` after any dependency or base image change. Cached layers can silently run old code.

---

## ghcr-ci-deploy-build-on-server

**When to use:** CI deploys a private `ghcr.io/getklai/...` image to core-01, but the server's docker login credentials are stale or missing

`docker pull ghcr.io/getklai/<image>:latest` on core-01 fails with `denied` when the server has no valid ghcr.io credentials. The GITHUB_TOKEN passed via `appleboy/ssh-action` `envs:` also fails — it is scoped to the CI runner context, not the remote shell.

**Solution:** Build the image directly on the server from the public monorepo, instead of pulling from the registry.

```yaml
- name: Deploy to core-01
  uses: appleboy/ssh-action@v1
  with:
    host: ${{ secrets.CORE01_HOST }}
    username: klai
    key: ${{ secrets.CORE01_DEPLOY_KEY }}
    script: |
      cd /tmp && rm -rf klai-build
      git clone --depth=1 --filter=blob:none --sparse https://github.com/GetKlai/klai.git klai-build
      cd klai-build && git sparse-checkout set focus/<service>
      docker build -t ghcr.io/getklai/<service>:latest ./focus/<service>
      rm -rf /tmp/klai-build
      cd /opt/klai && docker compose up -d <service>
```

**Why this works:**
- `GetKlai/klai` is a public repo — no auth needed for clone
- The sparse checkout pulls only the relevant service directory (~seconds)
- The built image uses the same tag as before; `docker compose up -d` picks it up normally
- The CI step still pushes to ghcr.io as an artifact (useful for rollbacks)

**Services using this pattern:** `research-api` (since 2026-03-26)

**Alternative (when registry auth can be fixed):** Store a GitHub PAT with `read:packages` in `/opt/klai/.env` as `GHCR_READ_PAT`, then: `echo "$GHCR_READ_PAT" | docker login ghcr.io -u getklai --password-stdin`

---

## uptime-kuma-add-monitor

**When to use:** Adding a new service to monitoring / status page (status.getklai.com)

Uptime Kuma runs on **public-01** as a Coolify-managed container. Its state lives in a SQLite database — there is no config file. Changes are made by querying the DB directly via `docker exec`.

**Push monitor** (for internal services checked via cron): heartbeat is sent by `push-health.sh` on core-01 every minute.
**HTTP monitor** (for public endpoints): polled directly by Uptime Kuma.

### Step 1 — Generate a push token (push monitors only)

```bash
ssh core-01 "openssl rand -hex 16"
```

### Step 2 — Insert monitor into Uptime Kuma DB

```bash
# SSH to public-01: use klai_ed25519 key as root (NOT markv, NOT id_ed25519)
# See: devops.md → public-01-ssh

# Find the container name
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64 "docker ps --format '{{.Names}}' | grep kuma"

# Insert via Python on the host (avoids shell quoting issues with JSON)
CONTAINER=uptime-kuma-ucowwogo0ogoskwk0ggg4o48

# Copy DB out, modify, copy back
ssh public-01 "docker cp ${CONTAINER}:/app/data/kuma.db /tmp/kuma.db && python3 -c \"
import sqlite3, json
db = sqlite3.connect('/tmp/kuma.db')

# Push monitor example:
db.execute('''INSERT INTO monitor (name, type, push_token, active, user_id, interval, maxretries, upside_down, accepted_statuscodes_json, retry_interval, resend_interval)
              VALUES (?, 'push', ?, 1, 1, 60, 0, 0, ?, 20, 0)''',
           ('My Service', 'YOUR_TOKEN_HERE', json.dumps(['200-299'])))

# HTTP monitor example:
# db.execute('''INSERT INTO monitor (name, type, url, active, user_id, interval, maxretries, upside_down, accepted_statuscodes_json, retry_interval, resend_interval)
#               VALUES (?, 'http', ?, 1, 1, 60, 0, 0, ?, 20, 0)''',
#            ('My Service', 'https://example.com/health', json.dumps(['200-299'])))

db.commit()
print('inserted id:', db.execute('SELECT last_insert_rowid()').fetchone()[0])
db.close()
\" && docker cp /tmp/kuma.db ${CONTAINER}:/app/data/kuma.db && rm /tmp/kuma.db"
```

**Critical:** Always use Python for the insert, not a bash heredoc with sqlite3. Shell quoting inside `docker exec "..."` strips double quotes from JSON strings, resulting in invalid `accepted_statuscodes_json` which crashes the monitor silently.

### Step 3 — Add to status page (if customer-facing)

```bash
# Get the new monitor's ID
ssh public-01 "docker exec ${CONTAINER} sh -c 'sqlite3 /app/data/kuma.db \"SELECT id, name FROM monitor ORDER BY id DESC LIMIT 5\"'"

# group_id 3 = "Services" group on the Klai Status page
# weight determines order (lower = higher on page; existing: Chat=10, Scribe=15, Focus=17, Login=20, ...)
ssh public-01 "docker exec ${CONTAINER} sh -c 'sqlite3 /app/data/kuma.db \"INSERT INTO monitor_group (monitor_id, group_id, weight, send_url) VALUES (<ID>, 3, <WEIGHT>, 0)\"'"
```

Internal monitors (infrastructure, error tracking) are NOT added to monitor_group.

### Step 4 — Restart Uptime Kuma

Uptime Kuma loads monitors at startup. Direct DB writes are not picked up until restart.

```bash
ssh public-01 "docker restart ${CONTAINER}"
```

### Step 5 — Add token to .env.sops and deploy (push monitors only)

```bash
# Decrypt, append, re-encrypt (must run from /tmp to avoid .sops.yaml path mismatch)
cd /tmp
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops --decrypt --input-type dotenv --output-type dotenv \
  ~/Server/projects/klai/klai-infra/core-01/.env.sops > klai-env-plain

echo "KUMA_TOKEN_MYSERVICE=<token>" >> klai-env-plain

SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops --encrypt --input-type dotenv --output-type dotenv \
  --age age1lyd243tsj8j7rn2wy4hdmnya99wsf2p87fpphys9k65kammerqsqnzpsur,age15ztzw9vnngkdnw0pg5tn8upplglvhzkep23sm5zu86res5lcmv7syw5m4v \
  klai-env-plain > ~/Server/projects/klai/klai-infra/core-01/.env.sops

rm klai-env-plain  # never leave plaintext lying around

# Deploy to server
cd ~/Server/projects/klai/klai-infra/core-01 && bash deploy.sh main
```

### Step 6 — Add check to push-health.sh

Edit `core-01/scripts/push-health.sh` and add a `push_exec` call. Then deploy:

```bash
scp core-01/scripts/push-health.sh core-01:/opt/klai/scripts/push-health.sh

# Run once manually to verify
ssh core-01 "bash /opt/klai/scripts/push-health.sh"
```

### Step 7 — Verify heartbeat received

```bash
CONTAINER=uptime-kuma-ucowwogo0ogoskwk0ggg4o48
ssh public-01 "docker exec ${CONTAINER} sh -c 'sqlite3 /app/data/kuma.db \"SELECT m.name, h.status, h.msg, h.time FROM heartbeat h JOIN monitor m ON m.id=h.monitor_id WHERE m.name=\\\"My Service\\\" ORDER BY h.time DESC LIMIT 3\"'"
```

Status `1` = up, `0` = down.

**See also:**
- `push-health.sh` on core-01 — exec check examples
- `klai-infra/SERVERS.md` → "Uptime Kuma monitoring" — full list of all `KUMA_TOKEN_*` variables and which services are monitored

---

## umami-access

**When to use:** Accessing or managing the Umami website analytics dashboard

Umami runs on public-01 as a Coolify-managed service.

- **Dashboard:** `https://analytics.getklai.com` — credentials in team password manager (admin account)
- **Container:** `umami-o48wg8wc0cc448gkcs4scsko`
- **Database:** dedicated PostgreSQL container `postgresql-o48wg8wc0cc448gkcs4scsko` (managed by Coolify)
- **Image:** `ghcr.io/umami-software/umami:3.0.3`
- **Website ID:** `bf92a12b-08fe-47f7-a3f1-3ed88d2ba379` (used in the tracker script on getklai.com)

**To check health:**
```bash
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64 "docker logs --tail 20 umami-o48wg8wc0cc448gkcs4scsko"
curl -s https://analytics.getklai.com/api/heartbeat  # returns {"ok":true}
```

**Tracker script** is in `klai-website/src/layouts/Base.astro` — rendered production-only (`import.meta.env.PROD`).

**Custom events** tracked: `cta-click`, `waitlist-open/submit/close`, `billing-toggle`, `faq-expand`, `lang-switch`, `contact-submit`, `careers-submit`, `outbound-link`, `scroll-depth`.

---
