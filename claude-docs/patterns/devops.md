# DevOps Patterns

> Coolify deployments, Docker, service management, CI/CD

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

## deploy-verify-after-push

**When to use:** After every `git push` to main in any klai project that has a GitHub Actions deploy workflow

Every push must be verified in two stages: CI green + server rollout confirmed.

### Stage 1 — CI verification

```bash
# Watch the run (blocks until done, exit 0 = success)
gh run watch --exit-status

# If multiple workflows triggered, pick the right one:
gh run list --limit 5
gh run watch <run-id> --exit-status

# On failure — show only the failing step:
gh run view <run-id> --log-failed
```

### Stage 2 — Server rollout check

**Frontend (portal-frontend):**
```bash
# Verify new bundle is in the directory Caddy serves
ssh core-01 "ls -lt /srv/portal/assets/*.js | head -3"

# Confirm the bundle contains expected new code
ssh core-01 "grep -l 'feature_keyword' /srv/portal/assets/*.js"
```

**Backend (portal-api):**
```bash
# Container age must match deploy time
ssh core-01 "docker ps --filter name=portal-api --format 'table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}'"

# Health endpoint
ssh core-01 "curl -s http://localhost:8010/health"

# Recent logs (look for "Zitadel PAT validated successfully")
ssh core-01 "docker logs --tail 20 klai-core-portal-api-1"
```

### Prerequisites: `gh` CLI

| Platform | Install | Auth |
|----------|---------|------|
| macOS | `brew install gh` | `gh auth login` |
| Linux (Debian/Ubuntu) | `sudo apt install gh` | `gh auth login` |
| Windows (winget) | `winget install --id GitHub.cli` | `gh auth login` |
| Windows (scoop) | `scoop install gh` | `gh auth login` |

If `gh` is not in PATH on Windows Git Bash, try: `"/c/Program Files/GitHub CLI/gh.exe"`

**Rule:** Never declare a deploy complete until both stages pass. CI green alone does not guarantee the new code is running.

**See also:** `klai-claude/rules/klai/ci-verify-after-push.md` — the full rule for AI agents

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
# Find the container name
ssh public-01 "docker ps --format '{{.Names}}' | grep kuma"

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

**See also:** `push-health.sh` on core-01 for exec check examples

---
