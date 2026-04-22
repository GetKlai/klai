# Platform Recovery Runbooks

> Step-by-step emergency procedures. Use these when something is already broken.
> For prevention, see `pitfalls/platform.md`.

---

## zitadel-login-v2-recovery

**Situation:** Portal login is broken AND Zitadel console (`auth.getklai.com/ui/console`) redirects to the broken portal login — chicken-and-egg deadlock.

### Step 1 — Break the deadlock

Delete the Login V2 row directly in PostgreSQL. Takes effect immediately — no Zitadel restart needed.

```bash
POSTGRES=$(docker ps --format '{{.Names}}' | grep -i postgres | head -1)
docker exec $POSTGRES psql -U zitadel -d zitadel -c \
  "DELETE FROM projections.instance_features5
   WHERE instance_id = '362757920133218310' AND key = 'login_v2';"
```

`auth.getklai.com/ui/console` now uses Zitadel's built-in login.

### Step 2 — Fix the underlying portal issue

Fix whatever caused portal login to break (missing env var, crashed container, etc.).

### Step 3 — Re-enable Login V2 via the Zitadel Feature API

**Do NOT directly UPDATE the projection table.** Projections are derived from
events in `eventstore.events2`; a direct UPDATE works temporarily but is
overwritten on the next projection rebuild (upgrade, `projection truncate`,
etc). Use the v2 Feature API — it writes a new event AND updates the
projection in one atomic step.

```bash
# Pull the dedicated admin PAT (klai-admin-sa, IAM_OWNER only — scope-limited).
# Never use PORTAL_API_ZITADEL_PAT for instance-level ops; that PAT is for
# tenant provisioning and should not carry admin authority at runtime.
PAT=$(ssh core-01 "sudo grep '^ZITADEL_ADMIN_PAT=' /opt/klai/.env | cut -d= -f2-")

# PUT — idempotent, writes event feature.instance.login_v2.set + updates projection
curl -sf -X PUT "https://auth.getklai.com/v2/features/instance" \
  -H "Authorization: Bearer $PAT" \
  -H "Content-Type: application/json" \
  -d '{"loginV2": {"required": true, "baseUri": "https://my.getklai.com"}}'

# Verify event landed (expect a new feature.instance.login_v2.set row with my.getklai.com)
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d zitadel -c \
  \"SELECT created_at, payload FROM eventstore.events2 \
    WHERE event_type = 'feature.instance.login_v2.set' \
    ORDER BY created_at DESC LIMIT 1;\""

# Verify live OIDC flow hits my.getklai.com (not getklai.getklai.com)
curl -s -o /dev/null -w '%{redirect_url}\n' \
  "https://auth.getklai.com/oauth/v2/authorize?response_type=code&client_id=369262708920483857&redirect_uri=https%3A%2F%2Fmy.getklai.com%2Fapi%2Fauth%2Foidc%2Fcallback&scope=openid&state=x&code_challenge=x&code_challenge_method=S256"
# Expected: https://my.getklai.com/login?authRequest=V2_...
```

**Why PUT, not PATCH:** Zitadel v2 API rejects PATCH on this endpoint (`Method
Not Allowed`). PUT is idempotent — re-running it returns `No changes` if the
state already matches.

**Never** use `baseURI: "https://getklai.getklai.com"` or any `{tenant}.getklai.com`.
See `.claude/rules/klai/platform/zitadel.md` § "Login V2 base_uri must be
my.getklai.com".

**Zitadel instance constants (core-01, do not guess):**

| Name | Value |
|---|---|
| Instance ID | `362757920133218310` |
| Feature aggregate creator | `362760545968848902` |
| portal-api machine user ID | `362780577813757958` |

---

## zitadel-pat-rotation

**Situation:** `Errors.Token.Invalid (AUTH-7fs1e)` in portal-api logs (PAT
expired or revoked), or scheduled quarterly rotation per
`runbooks/credential-rotation.md`.

Applies to both Zitadel PATs (identical procedure, different SA IDs):

| PAT | SA ID | SOPS key | Container to recreate |
|---|---|---|---|
| `PORTAL_API_ZITADEL_PAT` | `362780577813757958` | same | `portal-api` (recreate) |
| `ZITADEL_ADMIN_PAT` | `369320953139691537` | same | none (consumed by runbooks/CI only) |

### Step 1 — Generate new PAT via API

Use `klai-admin-sa`'s PAT to mint a new one. If that PAT is itself
expired, fall back to the Zitadel console (Users → Service Accounts →
`klai-admin-sa` → Personal Access Tokens → + New).

```bash
ADMIN_PAT=$(ssh core-01 "sudo grep '^ZITADEL_ADMIN_PAT=' /opt/klai/.env | cut -d= -f2-")
SA_ID="362780577813757958"   # change for whichever PAT you rotate
EXPIRY=$(date -u -d "+1 year" +"%Y-%m-%dT%H:%M:%SZ")

curl -sf -X POST "https://auth.getklai.com/management/v1/users/$SA_ID/pats" \
  -H "Authorization: Bearer $ADMIN_PAT" \
  -H "X-Zitadel-Orgid: 362757920133283846" \
  -H "Content-Type: application/json" \
  -d "{\"expirationDate\": \"$EXPIRY\"}"
# Returns: {"tokenId": "<new-id>", "token": "<new-pat>", ...}
# Save both — tokenId is needed for revocation in Step 5.
```

### Step 2 — Update SOPS (server-side, no local age key needed)

Use the server-side SOPS procedure. See
`.claude/rules/klai/infra/sops-env.md` § "Non-interactive SOPS (for agents)".
Replace the old value with the new token:

```bash
# In short: scp SOPS file + .sops.yaml to core-01:/tmp/klai-sops/core-01/,
# decrypt, sed-replace the var, encrypt in-place, scp back, commit, push.
# The sync-env GitHub Action then updates /opt/klai/.env automatically.
```

### Step 3 — Recreate portal-api (only for PORTAL_API_ZITADEL_PAT)

```bash
ssh core-01 "cd /opt/klai && docker compose up -d portal-api"
ssh core-01 "docker logs --tail 10 klai-core-portal-api-1 | grep 'Zitadel PAT validated'"
# Expected: "Zitadel PAT validated successfully"
```

`ZITADEL_ADMIN_PAT` needs no container restart — it is only read ad-hoc.

### Step 4 — Verify the new PAT works

```bash
NEW_PAT=$(ssh core-01 "sudo grep '^PORTAL_API_ZITADEL_PAT=' /opt/klai/.env | cut -d= -f2-")
curl -sf "https://auth.getklai.com/auth/v1/users/me" \
  -H "Authorization: Bearer $NEW_PAT" | head -c 200
# Expected: {"user":{"id":"362780577813757958",...}} — not 401
```

### Step 5 — Revoke the old PAT

```bash
ADMIN_PAT=$(ssh core-01 "sudo grep '^ZITADEL_ADMIN_PAT=' /opt/klai/.env | cut -d= -f2-")
curl -sf -X DELETE "https://auth.getklai.com/management/v1/users/$SA_ID/pats/<OLD_TOKEN_ID>" \
  -H "Authorization: Bearer $ADMIN_PAT" \
  -H "X-Zitadel-Orgid: 362757920133283846"
# Verify: list should show only the new PAT
curl -sf -X POST "https://auth.getklai.com/management/v1/users/$SA_ID/pats/_search" \
  -H "Authorization: Bearer $ADMIN_PAT" \
  -H "X-Zitadel-Orgid: 362757920133283846" \
  -H "Content-Type: application/json" -d '{}'
```

### Legacy fallback (macOS SOPS with local age key)

Only if the server is unreachable. Run from `/tmp`:

```bash
cd /tmp
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops --decrypt --input-type dotenv --output-type dotenv \
  ~/Server/projects/klai/klai-infra/core-01/.env.sops > klai-env-plain

sed -i '' 's|^PORTAL_API_ZITADEL_PAT=.*|PORTAL_API_ZITADEL_PAT=<new-token>|' klai-env-plain

SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops --encrypt --input-type dotenv --output-type dotenv \
  --age age1lyd243tsj8j7rn2wy4hdmnya99wsf2p87fpphys9k65kammerqsqnzpsur,age15ztzw9vnngkdnw0pg5tn8upplglvhzkep23sm5zu86res5lcmv7syw5m4v \
  /tmp/klai-env-plain > ~/Server/projects/klai/klai-infra/core-01/.env.sops

rm /tmp/klai-env-plain  # never leave plaintext lying around

cd ~/Server/projects/klai/klai-infra && git add core-01/.env.sops && git commit -m "Rotate PORTAL_API_ZITADEL_PAT"
```

---

## portal-api-deploy-outage-recovery

**Situation:** `docker compose up -d portal-api` crashed — new required fields in `config.py` have no value in `.env`. All auth is broken (Login V2 routes Zitadel's own login through portal-api).

### Step 1 — Break the auth deadlock

If Login V2 is blocking the Zitadel console, run the Login V2 DELETE from [#zitadel-login-v2-recovery](#zitadel-login-v2-recovery) first.

### Step 2 — Get the missing secrets

| Env var | Config field | How to generate |
|---|---|---|
| `PORTAL_API_ZITADEL_PAT` | `zitadel_pat` | Zitadel console → Users → Service Accounts → Portal API → Personal Access Tokens → + New |
| `PORTAL_API_PORTAL_SECRETS_KEY` | `portal_secrets_key` | `openssl rand -hex 32` (must be 64 hex chars = 32 bytes) |
| `PORTAL_API_SSO_COOKIE_KEY` | `sso_cookie_key` | `python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

### Step 3 — Add to /opt/klai/.env

```bash
echo 'PORTAL_API_ZITADEL_PAT=<token>' >> /opt/klai/.env
echo 'PORTAL_API_PORTAL_SECRETS_KEY=<hex32>' >> /opt/klai/.env
echo 'PORTAL_API_SSO_COOKIE_KEY=<fernet-key>' >> /opt/klai/.env
```

Use single quotes. Never use double quotes — `$` in values gets interpolated and truncates the secret.

### Step 4 — Pre-flight check, then deploy

```bash
ssh core-01 "cd /opt/klai && docker compose config portal-api | grep -A 80 'environment:'"
# Verify all three new vars are non-empty in the output, then:
ssh core-01 "cd /opt/klai && docker compose up -d portal-api"
docker logs --tail 20 klai-core-portal-api-1
```

### Step 5 — Re-enable Login V2

Run the UPDATE SQL from [#zitadel-login-v2-recovery](#zitadel-login-v2-recovery) step 3.

### Step 6 — Update .env.sops

Follow the SOPS steps from [#zitadel-pat-rotation](#zitadel-pat-rotation) step 4 — add all three new vars.

---

## librechat-stale-config-recovery

**Situation:** A tenant reports that a recent config change (MCP server toggle, interface update, endpoint change) is not active — the chat iframe behaves as if the old settings are still in place even after a page reload.

**Root cause (most common):** portal-api's Redis FLUSHALL failed during provisioning, so the tenant's LibreChat container restarted against stale yaml cached in Redis (no TTL — see `platform/librechat.md`). The `/regenerate` API path surfaces this in its `errors` response but the `mcp_servers` path only logs a warning, so the failure can go unnoticed.

**Signal to look for:** structured log line in VictoriaLogs:

```
service:portal-api AND event:redis_flushall_failed
```

Returned fields: `slug`, `error`, `request_id` (propagated from Caddy).

### Step 1 — Confirm staleness

Get the failing event's `request_id` from the log line above, then trace the full chain to see which tenant and what triggered it:

```
request_id:<uuid>
```

If the trigger was an MCP toggle from the portal UI, the user-visible impact is "my MCP change didn't take effect." If the trigger was `/internal/librechat/regenerate`, the CI run already surfaced it in its response body.

### Step 2 — Manually re-run the regenerate workflow

The cleanest recovery is to re-trigger the global regenerate workflow. It re-reads the base yaml, writes per-tenant yaml, re-attempts FLUSHALL, and restarts every tenant container:

```bash
gh workflow run deploy-librechat-config.yml
gh run watch --exit-status $(gh run list --workflow=deploy-librechat-config.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```

Alternative for a single tenant (faster, less impact on other tenants):

```bash
ssh core-01 "docker exec klai-core-redis-1 redis-cli -a \$(sudo grep ^REDIS_PASSWORD= /opt/klai/.env | cut -d= -f2-) FLUSHALL"
ssh core-01 "docker restart librechat-<slug>"
```

> Note: the `docker exec` above runs **on the server**, not from portal-api — so it bypasses the docker-socket-proxy. This is why the runbook is the correct recovery surface, not portal-api code.

### Step 3 — Verify

- Reload the tenant's chat page; confirm the config change is now active.
- Check VictoriaLogs: no new `redis_flushall_failed` events should appear.

### Step 4 — If FLUSHALL keeps failing

Something is wrong with the Redis container itself. Check:

```bash
ssh core-01 "docker ps --filter name=redis --format '{{.Names}}\t{{.Status}}'"
ssh core-01 "docker logs --tail 50 klai-core-redis-1"
```

Common causes: container OOM, port conflict, auth mismatch between `/opt/klai/.env` and the running container. See `docker-socket-proxy.md` for the allowed proxy verbs — if you're debugging from portal-api, remember `/exec/*/start` is blocked and you need to run redis-cli on the host.

### Follow-up

A dedicated SPEC for alerting infra (alertmanager + Grafana alert-provisioning + notification channels) is the right long-term fix so this runbook doesn't have to be triggered by end-user reports. Until then: a weekly manual query of `service:portal-api AND event:redis_flushall_failed` in VictoriaLogs catches stragglers.

---

## container-down

**Related alert**: SPEC-OBS-001-R12 (`container_down`, CRIT)

**Situation**: cAdvisor stopped reporting `container_last_seen` for a klai-core container for more than 2 minutes. The container is either gone, restarting too fast for cAdvisor to catch a stable view, or the Docker daemon itself is unresponsive.

**Signal to look for**:
```promql
(time() - container_last_seen{name=~"klai-core-.*"}) > 120
```
Or in Grafana → Alerting → Rules → `container_down` → see which `name` label fires.

### Step 1 — Identify the container

```bash
ssh core-01 "docker ps -a --filter name={NAME} --format 'table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}'"
```

If the container is missing from `ps -a` entirely, it was removed — go to Step 4.

### Step 2 — Inspect exit reason

```bash
ssh core-01 "docker inspect {NAME} | grep -E 'ExitCode|Error|FinishedAt' | head -10"
```

ExitCode 137 = OOMKilled. ExitCode 139 = segfault. Non-zero recent FinishedAt = it died and didn't restart.

### Step 3 — Read its last logs

```bash
ssh core-01 "docker logs --tail 100 {NAME}"
```

Look for: panics, missing env vars, "Address already in use", connection failures to dependencies.

### Step 4 — Restart or recreate

Recoverable (transient cause):
```bash
ssh core-01 "cd /opt/klai && docker compose up -d {service}"
```

Container missing entirely (e.g. `docker rm` ran):
```bash
ssh core-01 "cd /opt/klai && docker compose up -d {service}"
```

### Verify

```bash
ssh core-01 "docker ps --filter name={NAME} --format '{{.Names}}\t{{.Status}}'"
# Expect: Up X seconds (healthy)
```

Within 1-2 minutes the alert should auto-resolve once cAdvisor sees the container again.

### Follow-up

If this fired without operator action: investigate root cause via logs. If pattern recurs across containers: check Docker daemon health (`systemctl status docker`), disk space (see `core01-disk-usage-high`), and cAdvisor itself.

---

## container-restart-loop

**Related alert**: SPEC-OBS-001-R13 (`container_restart_loop`, CRIT)

**Situation**: A container has restarted multiple times within a 15-minute window, and the pattern has continued for at least 15 consecutive minutes. This is not a single planned deploy — a deploy produces 1 restart that drops out of the rolling window. A real loop sustains.

**Signal to look for**:
```promql
changes(container_start_time_seconds{name=~"klai-core-.*"}[15m]) > 0
```
The alert annotation includes the actual restart count from `$value`.

### Step 1 — Confirm the loop and its cadence

```bash
ssh core-01 "docker inspect {NAME} --format '{{`{{.RestartCount}}\t{{.State.StartedAt}}\t{{.State.FinishedAt}}\t{{.State.ExitCode}}`}}'"
```

A high `RestartCount` and FinishedAt close to StartedAt confirms rapid restart cycling.

### Step 2 — Find the crash cause

```bash
ssh core-01 "docker logs --tail 200 {NAME} | tail -50"
```

Common patterns:
- `OOMKilled` (exit 137): bump container memory limit
- Healthcheck timeout: see Step 3
- Missing env var: check SOPS + recent compose changes
- DB connection failure: check that the dependency is healthy

### Step 3 — Check the healthcheck

```bash
ssh core-01 "docker inspect {NAME} --format '{{`{{range .State.Health.Log}}{{.Output}}---{{end}}`}}'"
```

If healthcheck is the culprit and the container is otherwise OK, the fix is in compose (timeout, retries, or healthcheck command itself).

### Step 4 — Pause and fix

While investigating, silence the alert in Grafana UI → Alerting → Silences → matcher `alertname=container_restart_loop` + `name={NAME}`, comment + 1h expiry. Then deploy fix:

```bash
ssh core-01 "cd /opt/klai && docker compose up -d {service}"
```

### Verify

After fix is deployed and the container has been stable for 15 minutes, the alert auto-resolves (firing condition no longer holds).

### Follow-up

If the loop was caused by a recent code/config push, document in commit message + `docs/runbooks/post-mortems/` if user-impacting. If recurring across deploys for one service: that service needs a hardening SPEC.

---

## core01-disk-usage-high

**Related alert**: SPEC-OBS-001-R17 (`core01_disk_usage_high`, MED)

**Situation**: The host root filesystem on core-01 (`/dev/md2`) has been below 15% free for at least 30 minutes. Once core-01's root fills completely, Docker stops accepting writes (image pulls fail, container logs stop), Postgres WAL writes fail, and incremental services degrade silently.

**Signal to look for**:
```promql
max(node_filesystem_avail_bytes{device="/dev/md2",fstype="ext4",mountpoint=~"/etc/.*"}
    / node_filesystem_size_bytes{device="/dev/md2",fstype="ext4",mountpoint=~"/etc/.*"})
  < 0.15
```

Or directly on the host:
```bash
ssh core-01 "df -h /"
```

### Step 1 — See what's eating disk

```bash
ssh core-01 "sudo du -hx --max-depth=1 / 2>/dev/null | sort -h | tail -10"
ssh core-01 "sudo du -hx --max-depth=1 /var/lib/docker 2>/dev/null | sort -h | tail -10"
```

Top suspects on klai-core:
- `/var/lib/docker/volumes/` — VictoriaLogs (30d retention can grow), Postgres data, MongoDB
- `/var/lib/docker/overlay2/` — image + container layer storage
- `/opt/klai/logs/` — script logs, push-health.sh output

### Step 2 — Reclaim safely

Safe always:
```bash
# Remove stopped containers (can be re-created from compose)
ssh core-01 "docker container prune -f"

# Remove dangling images (untagged, no container references)
ssh core-01 "docker image prune -f"

# Remove build cache
ssh core-01 "docker builder prune -af"
```

Riskier — read carefully:
```bash
# Remove ALL unused images (anything not used by a running container)
# Risk: loses cached versions; next deploy must re-pull from registry
ssh core-01 "docker image prune -af"

# Remove unused volumes (NOT used by any container)
# Risk: loses data if a service is currently down — list first!
ssh core-01 "docker volume ls -f dangling=true"
ssh core-01 "docker volume prune -f"
```

Never on a klai-core production host:
```bash
docker system prune -af --volumes   # nuclear option, drops everything not in-use
```

### Step 3 — Truncate VictoriaLogs / VictoriaMetrics retention

If logs retention is the dominant consumer:
```bash
ssh core-01 "docker exec klai-core-victorialogs-1 ls -la /vlogs"
```
Reducing retention requires changing the `-retentionPeriod` flag in `deploy/docker-compose.yml` and recreating. Coordinate with monitoring needs first.

### Verify

```bash
ssh core-01 "df -h /"
```
After cleanup, confirm `Use%` is below 80% (= avail above 20%, comfortably above the 15% alert threshold).

Alert auto-resolves once avail-ratio rises above 0.15 and stays there for one evaluation cycle (1 min).

### Follow-up

If disk fills repeatedly (more than once per quarter), this server needs more disk OR a stricter retention policy on logs/volumes. Track in a follow-up SPEC. Don't keep firefighting the same condition.
