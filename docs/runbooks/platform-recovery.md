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

---

## caddy-5xx-surge

**Related alert**: SPEC-OBS-001-R9 (`caddy_5xx_rate_high`, CRIT)

**Situation**: More than 1% of Caddy responses returned a 5xx status code over the last 5 minutes. This catches errors from any upstream behind Caddy (portal-api, grafana, auth, knowledge services), not just one. Mostly fires when one specific upstream is degraded.

**Signal to look for**:
```
service:caddy AND status:5*
```
In Grafana → Explore → VictoriaLogs.

### Step 1 — Identify the affected upstream

```
service:caddy AND status:5* | stats by(request.host) count() as hits
```

The host with the most hits is the suspect. Common mappings:
- `my.getklai.com` or `*.getklai.com` (tenant subdomains) → portal-api
- `auth.getklai.com` → Zitadel
- `grafana.getklai.com` → Grafana itself
- `firecrawl.getklai.com` → firecrawl-api
- `logs-ingest.getklai.com` → alloy

### Step 2 — Check that upstream's logs

```bash
ssh core-01 "docker logs --tail 100 klai-core-{service}-1"
```

For portal-api specifically:
```bash
ssh core-01 "docker logs --tail 100 klai-core-portal-api-1 | grep -E 'level=error|status_code=5'"
```

### Step 3 — Sample a request_id end-to-end

```
service:caddy AND status:5* AND request.host:{problematic_host} | sort by(_time) desc | limit 1
```

Copy the `resp_headers.X-Request-Id` value, then trace cross-service:
```
request_id:{uuid}
```

This shows the full chain: Caddy → portal-api → downstream service. The 5xx originates wherever the chain breaks.

### Step 4 — Mitigate

If transient (load spike, single bad request): nothing to do, alert auto-resolves.

If sustained: depends on root cause:
- DB connection failure → check DB container health
- Upstream container down → see `container-down` runbook
- Upstream container in restart loop → see `container-restart-loop` runbook
- Application bug → revert the latest deploy of that service

### Verify

After fix: 5xx rate drops below 1%, alert auto-resolves within one evaluation cycle (1m) plus `for: 5m` debounce.

### Follow-up

If a specific upstream surges 5xx repeatedly across days, that service needs deeper instrumentation (per-endpoint metrics, structured error logs). Track in a follow-up SPEC.

---

## knowledge-ingest-error-surge

**Related alert**: SPEC-OBS-001-R16 (`ingest_error_rate_elevated`, HIGH)

**Situation**: knowledge-ingest emitted more than 10 error-level log lines in the last 10 minutes. Common causes: an upstream connector returning 401/429/500, a malformed document blocking a Celery worker, or graphiti/Neo4j connectivity issues.

**Signal to look for**:
```
service:knowledge-ingest AND level:error
```

### Step 1 — Pattern-classify the errors

```
service:knowledge-ingest AND level:error | sort by(_time) desc | limit 20
```

Group by the most common error fingerprint. Fast triage:
- `Job ... failed` with traceback → application-level job failure
- `401 Unauthorized` / `403 Forbidden` → connector secret expired
- `429 Too Many Requests` → upstream rate-limit hit; throttle our calls
- `Neo4j` / `graphiti` in error → graph backend connectivity issue
- `OperationalError` / `connection refused` → Postgres or other dependency

### Step 2 — If a single org_id dominates

```
service:knowledge-ingest AND level:error | stats by(org_id) count() as hits
```

A spike concentrated in one org usually means their connector is misconfigured (expired token, revoked OAuth, deleted Confluence space). Notify the customer or temporarily disable the connector.

### Step 3 — If the ingest worker itself is unhealthy

```bash
ssh core-01 "docker ps --filter name=knowledge-ingest --format 'table {{.Names}}\t{{.Status}}'"
ssh core-01 "docker logs --tail 200 klai-core-knowledge-ingest-1 | tail -100"
```

If restart loop: see `container-restart-loop` runbook.

### Step 4 — Connector-secret rotation

If 401/403 dominates and matches a known secret-expiry: see `klai-infra/CONNECTOR_SECRET_ROTATION.md`.

### Verify

After fix: error log volume drops below 10/10min, alert auto-resolves.

### Follow-up

If the same connector class (Confluence, HubSpot, Notion) repeatedly errors across orgs, that connector needs hardening (better retry logic, structured error reporting). Track in a follow-up SPEC.

---

## alerter-down-recovery

**Related alert**: SPEC-OBS-001-R23 (Uptime Kuma push monitor `Klai alerter heartbeat (OBS-001)`)

**Situation**: Uptime Kuma on public-01 stopped receiving the 5-minute heartbeat push from Grafana. This means EITHER Grafana itself is down, OR Grafana is up but its alerting evaluation is broken (rule paused, contact-point misconfig, network split between core-01 and public-01).

**Signal to look for**: an email with subject `[KLAI-ALERTER-DOWN] Klai alerter heartbeat (OBS-001) (...)` from `hello@getklai.com` to `mark.vletter@voys.nl`. The mail arrives via Uptime Kuma's own SMTP path, NOT via Grafana — so receiving it confirms the dead-man's-switch worked.

### Step 1 — Is Grafana up?

```bash
ssh core-01 "docker ps --filter name=grafana --format '{{.Names}}\t{{.Status}}'"
```

If not running: see `container-down` runbook (likely cause: OOM, deploy-compose workflow failure, or core-01 host issue).

If `Restarting`: see `container-restart-loop`.

### Step 2 — Is alerting itself running?

```bash
ssh core-01 "docker logs --tail 30 klai-core-grafana-1 2>&1 | grep -iE 'alert|provisioning'"
```

Look for: `failed to provision`, `error`, missing scheduler ticks. The scheduler should log every minute.

### Step 3 — Is the heartbeat rule healthy?

```bash
ssh core-01 'docker exec klai-core-grafana-1 sh -c "curl -sf -u admin:\$GF_SECURITY_ADMIN_PASSWORD http://localhost:3000/api/prometheus/grafana/api/v1/rules" 2>&1' | grep -o '"name":"alerter_heartbeat".*"health":"[^"]*"' | head -1
```

Expected: `"health":"ok"`. If `error`: the rule's expression broke (rare — it's just `math: 1`).

### Step 4 — Is the webhook URL set?

```bash
ssh core-01 "docker exec klai-core-grafana-1 printenv KUMA_HEARTBEAT_URL"
```

Expected: `https://status.getklai.com/api/push/<token>?status=up&msg=ok`. If empty, the SOPS env-var didn't deploy — see `deploy.md` for SOPS sync.

### Step 5 — Test the webhook manually

```bash
ssh core-01 "docker exec klai-core-grafana-1 curl -sf \"\$KUMA_HEARTBEAT_URL\""
```

Expected: `{"ok":true,"msg":"OK"}` (Kuma push API response). If 404 or different: the push token is wrong (check Kuma SQLite `monitor` table for the current token).

### Step 6 — Force one heartbeat manually if needed

If the rule is healthy but Kuma still hasn't received a heartbeat, force one:

```bash
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64 "
  KUMA=\$(docker ps -q --filter name=uptime-kuma | head -1)
  docker exec \$KUMA sqlite3 /app/data/kuma.db \"SELECT id, name, push_token FROM monitor WHERE name LIKE '%alerter heartbeat%'\"
"
```

Use the push token to construct the URL, then `curl` it from anywhere — Kuma will mark UP.

### Verify

After fix: Uptime Kuma's monitor `Klai alerter heartbeat (OBS-001)` shows status UP. The next email from Kuma will be a "monitor up" recovery notification (suppressed if `disableResolveMessage` is set in its notification, which it isn't currently → expect a recovery mail).

### Follow-up

If this fires repeatedly without a clear root cause, investigate why core-01 ↔ public-01 connectivity is flaky (Hetzner internal network, firewall, Caddy on public-01 handling the inbound). One follow-up improvement: a SECOND independent SMTP provider for Kuma (currently shares Cloud86 with Grafana — a Cloud86 outage knocks out both paths). See DEFERRED.md.

---

## caddy-p95-latency-high

**Related alert**: SPEC-OBS-001-R10 (`caddy_p95_latency_high`, CRIT)

**Situation**: The 95th percentile of Caddy request durations exceeded 2 seconds over the last 5 minutes. Slow-but-not-erroring requests are often worse than 5xx — users experience timeouts, retries, frustration without a clear error to debug.

**Signal to look for**:
```
_time:5m service:caddy | stats quantile(0.95, duration) as p95
```

### Step 1 — Find the slow upstream

```
_time:5m service:caddy | stats by(request.host) quantile(0.95, duration) as p95
```

Sort by p95 descending. The host with the highest p95 is the suspect.

### Step 2 — Check upstream container health

For the slow upstream service:
```bash
ssh core-01 "docker stats --no-stream {container_name} --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}'"
```

CPU pinned at 100% or memory near limit → resource exhaustion. Restart loop → see `container-restart-loop`.

### Step 3 — Check downstream dependencies

```bash
ssh core-01 "docker logs --tail 100 {container_name} | grep -iE 'timeout|slow|connection|pool exhausted'"
```

Common causes:
- **DB connection pool exhausted** → upstream waits for a free connection. Check Postgres/MongoDB load.
- **Upstream API rate-limit** → slow responses from LiteLLM, OpenAI, Mistral. Check their dashboards.
- **Large synchronous payloads** → file uploads or huge LLM contexts blocking the event loop.

### Step 4 — Decide: tune or fix

- **Sustained slowness in one upstream**: file SPEC for that service to add async/queueing.
- **Spike from one tenant**: throttle the offender's RPS at Caddy level.
- **Genuine slowness due to upstream LLM**: tune the threshold (>2s might be too aggressive for LLM endpoints) or exclude `request.host:llm-related` paths from the rule.

### Verify

After fix: p95 drops back below 2s, alert auto-resolves within 1 evaluation cycle plus `for: 5m` debounce.

### Follow-up

If LLM endpoints structurally exceed 2s p95 (which is likely for chat completions): split this rule. One for the static-page hosts (tighter threshold), one for LLM hosts (looser threshold or higher quantile, e.g. p99 > 30s).

---

## caddy-traffic-drop

**Related alert**: SPEC-OBS-001-R11 (`caddy_traffic_drop`, CRIT)

**Situation**: Caddy request rate over the last 5 minutes is less than 20% of the rolling 1-hour baseline, AND the baseline shows meaningful volume (>600 req/h). Either users can't reach Caddy (DNS, certificate, network) OR all upstreams are down OR something on the public-facing path broke.

**Signal to look for**:
```
ratio = current_5m / (baseline_1h / 12)
fire if ratio < 0.2 AND baseline_1h > 600
```

### Step 1 — Verify Caddy itself is up

```bash
ssh core-01 "docker ps --filter name=caddy --format '{{.Names}}\t{{.Status}}'"
ssh core-01 "curl -sf -o /dev/null -w '%{http_code}\n' https://my.getklai.com"
```

If 200: Caddy is up and serving. The drop is upstream-of-Caddy (DNS, network) or downstream (all backends erroring).

If non-200: Caddy is broken. See container-down + container-restart-loop runbooks.

### Step 2 — Check DNS

```bash
dig +short my.getklai.com
dig +short auth.getklai.com
```

Should return `65.21.174.162`. If empty or different: Hetzner DNS issue.

### Step 3 — Check certificate

```bash
echo | openssl s_client -servername my.getklai.com -connect my.getklai.com:443 2>/dev/null | openssl x509 -noout -dates
```

If expired or expires in <7 days: certificate auto-renewal failed. Check Caddy logs for ACME errors.

### Step 4 — Per-host breakdown

```
_time:1h service:caddy | stats by(request.host) count() as hits | sort by(hits) desc
_time:5m service:caddy | stats by(request.host) count() as hits | sort by(hits) desc
```

Compare which hosts dropped. If one host is 0 in 5m but 100 in 1h: that specific upstream is unreachable. If all hosts dropped: front-door issue.

### Step 5 — Hetzner network status

Check https://status.hetzner.com/ — outages on HEL1 datacenter would cut all inbound traffic.

### Verify

After fix: traffic returns, ratio rises above 0.2, alert auto-resolves within 1 evaluation cycle plus `for: 10m` debounce.

### Follow-up

If this fires on weekends/evenings: tune the baseline-floor (currently 600 req/h) upward. The rule's design specifically uses absolute baseline volume to avoid quiet-period false-positives, but the 600 number is a starting guess — use observed traffic patterns to refine.

---

## librechat-health-failed-elevated

**Related alert**: SPEC-OBS-001-R15 (`librechat_health_failed_elevated`, HIGH)

**Situation**: More than 5 LibreChat log lines mentioning both "health" and "fail" appeared in 10 minutes. Either an upstream LLM (LiteLLM, vLLM, OpenAI, Mistral) is unavailable, a librechat container has a config issue, or one of its dependencies (MongoDB, Redis, Meilisearch) is unhealthy.

**Signal to look for**:
```
_time:10m container:~".*librechat.*" AND _msg:i("health") AND _msg:i("fail") | stats count() as hits
```

This is **text-substring matching** on the unstructured `_msg` field. LibreChat doesn't emit structured event fields, so we look for keyword combinations.

### Step 1 — Identify affected tenants

```
_time:10m container:~".*librechat.*" AND _msg:i("health") AND _msg:i("fail") | stats by(container) count() as hits
```

### Step 2 — Sample recent failures per container

For each affected `{NAME}`:
```
container:{NAME} AND _msg:i("health") | sort by(_time) desc | limit 10
```

Look for: "ECONNREFUSED", "timeout", "401", "no response", "rate limit". The text often points to the upstream.

### Step 3 — Test the health endpoint directly

```bash
ssh core-01 "docker exec {NAME} curl -sf -o - http://localhost:3080/api/health"
```

200 with body `{"ok":true}` = container is healthy, the failures are transient or affect a specific feature. Non-200 or no response = container itself is unhealthy → check container_restart_loop / container_down runbooks.

### Step 4 — Check upstream LLM

```bash
ssh core-01 "docker logs --tail 50 klai-core-litellm-1 | grep -iE 'error|timeout|429|503'"
```

If LiteLLM logs errors: upstream model provider is having issues. Check provider status pages (OpenAI, Mistral) before changing anything.

### Step 5 — Restart the affected container if isolated

If only ONE container is affected and the upstream looks healthy:
```bash
ssh core-01 "docker restart {NAME}"
```

For widespread impact across many tenants: don't restart-bomb everything. Investigate upstream first.

### Verify

After fix: health-failure log volume drops, alert auto-resolves within 10m of the last matching log line (rule's `keepFiringFor: 30m` extends visibility).

### Follow-up

If LibreChat ever upgrades and changes the "health" / "fail" text in their log format, this rule silently breaks. Quarterly review: confirm fire-count > 0 for SOME period of last quarter — if always zero, either we're suspiciously healthy (good) or the rule broke (bad).

A long-term fix is to instrument LibreChat with structured-event logging via a sidecar, so we can match `event:health_failed` instead of substring text. Out of OBS-001 scope.
