# Uptime Kuma — Adding a Monitor

> Full procedure for adding a new service to Uptime Kuma (status.getklai.com).
> For SSH access to public-01, see `patterns/devops.md#public-01-ssh`.

Uptime Kuma runs on **public-01** as a Coolify-managed container. Its state lives in SQLite — no config file. Changes are made by querying the DB directly.

**Push monitor** (internal services, checked via cron from core-01): heartbeat sent by `push-health.sh` every minute.
**HTTP monitor** (public endpoints): polled directly by Uptime Kuma.

```bash
CONTAINER=uptime-kuma-ucowwogo0ogoskwk0ggg4o48
```

---

## Step 1 — Generate a push token (push monitors only)

```bash
ssh core-01 "openssl rand -hex 16"
```

---

## Step 2 — Insert monitor into the DB

Always use Python for the insert — shell quoting inside `docker exec "..."` strips double quotes from JSON, producing invalid `accepted_statuscodes_json` that crashes the monitor silently.

```bash
ssh public-01 "docker cp ${CONTAINER}:/app/data/kuma.db /tmp/kuma.db && python3 -c \"
import sqlite3, json
db = sqlite3.connect('/tmp/kuma.db')

# Push monitor:
db.execute('''INSERT INTO monitor (name, type, push_token, active, user_id, interval, maxretries, upside_down, accepted_statuscodes_json, retry_interval, resend_interval)
              VALUES (?, 'push', ?, 1, 1, 60, 0, 0, ?, 20, 0)''',
           ('My Service', 'YOUR_TOKEN_HERE', json.dumps(['200-299'])))

# HTTP monitor (use instead of push for public endpoints):
# db.execute('''INSERT INTO monitor (name, type, url, active, user_id, interval, maxretries, upside_down, accepted_statuscodes_json, retry_interval, resend_interval)
#               VALUES (?, 'http', ?, 1, 1, 60, 0, 0, ?, 20, 0)''',
#            ('My Service', 'https://example.com/health', json.dumps(['200-299'])))

db.commit()
print('inserted id:', db.execute('SELECT last_insert_rowid()').fetchone()[0])
db.close()
\" && docker cp /tmp/kuma.db ${CONTAINER}:/app/data/kuma.db && rm /tmp/kuma.db"
```

---

## Step 3 — Add to status page (customer-facing monitors only)

Internal monitors (infrastructure, error tracking) are NOT added to `monitor_group`.

```bash
# Get the monitor ID
ssh public-01 "docker exec ${CONTAINER} sh -c 'sqlite3 /app/data/kuma.db \"SELECT id, name FROM monitor ORDER BY id DESC LIMIT 5\"'"

# group_id 3 = "Services" group on the Klai Status page
# weight order (lower = higher on page): Chat=10, Scribe=15, Focus=17, Login=20
ssh public-01 "docker exec ${CONTAINER} sh -c 'sqlite3 /app/data/kuma.db \"INSERT INTO monitor_group (monitor_id, group_id, weight, send_url) VALUES (<ID>, 3, <WEIGHT>, 0)\"'"
```

---

## Step 4 — Restart Uptime Kuma

Uptime Kuma only loads monitors at startup — direct DB writes are not picked up until restart.

```bash
ssh public-01 "docker restart ${CONTAINER}"
```

---

## Step 5 — Add token to .env.sops and deploy (push monitors only)

Run from `/tmp` to avoid `.sops.yaml` path mismatch:

```bash
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

cd ~/Server/projects/klai/klai-infra/core-01 && bash deploy.sh main
```

---

## Step 6 — Add check to push-health.sh (push monitors only)

Edit `core-01/scripts/push-health.sh` and add a `push_exec` call. Then deploy:

```bash
scp core-01/scripts/push-health.sh core-01:/opt/klai/scripts/push-health.sh
ssh core-01 "bash /opt/klai/scripts/push-health.sh"  # run once to verify
```

---

## Step 7 — Verify heartbeat received

```bash
ssh public-01 "docker exec ${CONTAINER} sh -c 'sqlite3 /app/data/kuma.db \"SELECT m.name, h.status, h.msg, h.time FROM heartbeat h JOIN monitor m ON m.id=h.monitor_id WHERE m.name=\\\"My Service\\\" ORDER BY h.time DESC LIMIT 3\"'"
```

Status `1` = up, `0` = down.

---

## See Also

- `klai-infra/SERVERS.md` → "Uptime Kuma monitoring" — full list of `KUMA_TOKEN_*` vars and which services are monitored
- `push-health.sh` on core-01 — exec check examples
