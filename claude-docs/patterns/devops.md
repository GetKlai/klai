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
