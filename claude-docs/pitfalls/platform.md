# Platform Pitfalls

> LiteLLM, LibreChat, vLLM, Zitadel, Caddy, Meilisearch — Klai AI stack.
> Most entries are derived from the compatibility review in `architecture/platform.md`.

---

## platform-litellm-vllm-provider-prefix

**Severity:** HIGH

**Trigger:** Configuring LiteLLM to route to vLLM instances

The provider prefix must be `hosted_vllm/`, not `openai/`. Using `openai/` causes routing errors.

**Wrong:**
```yaml
model: openai/qwen3-32b
api_base: http://localhost:8001/v1
```

**Correct:**
```yaml
model: hosted_vllm/qwen3-32b
api_base: http://localhost:8001/v1
```

**Source:** `architecture/platform.md` — Compatibility Review: LiteLLM to vLLM

---

## platform-litellm-drop-params

**Severity:** HIGH

**Trigger:** Setting up LiteLLM configuration for vLLM backends

`drop_params: true` must be set in `litellm_settings`. vLLM does not accept all OpenAI parameters and will error without this setting.

```yaml
litellm_settings:
  drop_params: true
```

**Source:** `architecture/platform.md` — Compatibility Review: LiteLLM to vLLM

---

## platform-vllm-gpu-memory-utilization

**Severity:** CRIT

**Trigger:** Configuring `--gpu-memory-utilization` for two vLLM instances on one H100

`--gpu-memory-utilization` is a ceiling on total VRAM, not a split. Setting it too low leaves no room for KV cache and causes OOM or crash on startup.

**Wrong (original values):**
```
32B instance: --gpu-memory-utilization 0.41  # = 32.8 GB ceiling, barely fits weights
8B instance:  --gpu-memory-utilization 0.12  # = 9.6 GB, barely fits weights
```

**Correct:**
```
32B instance: --gpu-memory-utilization 0.55  # = 44 GB ceiling (33 GB weights + 11 GB KV cache)
8B instance:  --gpu-memory-utilization 0.40  # = 32 GB ceiling (9 GB weights + 23 GB KV cache)
# Combined: ~76 GB of 80 GB. Leaves ~4 GB for CUDA overhead and Whisper.
```

**Source:** `architecture/platform.md` — Compatibility Review: vLLM gpu-memory-utilization

---

## platform-vllm-sequential-startup

**Severity:** CRIT

**Trigger:** Starting two vLLM instances on one GPU

vLLM has a memory accounting bug where parallel startup causes the second instance to see the first's VRAM as occupied. Always start sequentially.

**Startup order:**
1. Start 32B instance (Qwen3-32B)
2. Wait for it to be healthy
3. Start 8B instance (Qwen3-8B)
4. Wait for it to be healthy
5. Then start Whisper (CTranslate2)

**Implementation:** Use `depends_on` with health checks in Docker Compose, or sequential systemd/startup scripts.

**Source:** `architecture/platform.md` — Compatibility Review: vLLM two instances on one GPU

---

## platform-vllm-mps-enforce-eager

**Severity:** HIGH

**Trigger:** Running vLLM with NVIDIA MPS enabled

vLLM CUDAGraph combined with MPS can cause instability (illegal memory access) on some configurations.

**Prevention:** Add `--enforce-eager` to the smaller (8B) vLLM instance to disable CUDAGraph.

```bash
vllm serve qwen3-8b ... --enforce-eager
```

**Source:** `architecture/platform.md` — Compatibility Review: NVIDIA MPS setup

---

## platform-librechat-oidc-reuse-tokens

**Severity:** CRIT

**Trigger:** Configuring LibreChat OIDC with `OPENID_REUSE_TOKENS=true`

This setting breaks existing users. Do not set it on any deployment that has existing user accounts.

**Prevention:**
```bash
# .env template for LibreChat containers:
OPENID_REUSE_TOKENS=false   # Never set to true on non-fresh deployments
```

**Source:** `architecture/platform.md` — LibreChat OIDC known issues (GitHub #9303)

---

## platform-librechat-username-claim

**Severity:** HIGH

**Trigger:** Setting up LibreChat OIDC integration with Zitadel

Without explicit configuration, LibreChat falls back to `given_name` as the username, which causes display and identity issues.

**Required setting in all LibreChat container `.env` files:**
```bash
OPENID_USERNAME_CLAIM=preferred_username
```

**Source:** `architecture/platform.md` — LibreChat OIDC known issues (GitHub #8672)

---

## platform-librechat-logout-no-zitadel-session

**Severity:** HIGH — **RESOLVED** (2026-03-11)

**Trigger:** Implementing logout in the customer portal

LibreChat logout does NOT call the Zitadel `end_session` endpoint. After LibreChat logout, the Zitadel session remains active. Users can immediately log back in without re-authenticating.

**Resolution (implemented):**
The portal sidebar logout:
1. Awaits `POST /api/auth/logout` (clears klai_sso cookie + SSO cache)
2. Calls `auth.signoutRedirect()` (react-oidc-context) — redirects to Zitadel end-session endpoint
3. Zitadel redirects to `post_logout_redirect_uri: /logged-out`

**Race condition fix:** The `fetch` to `/api/auth/logout` is awaited before `signoutRedirect()`. Without the await, the browser navigates away before the logout request completes.

**Source:** `architecture/platform.md` — Auth: Zitadel + LibreChat + FastAPI + React SPA

---

## platform-grafana-victorialogs-loki-incompatible

**Severity:** HIGH

**Trigger:** Adding VictoriaLogs as a datasource in Grafana

The generic Loki datasource plugin does NOT work with VictoriaLogs. LogsQL (VictoriaLogs) and LogQL (Loki) are incompatible query languages.

**Prevention:** Install the dedicated plugin:
```bash
GF_INSTALL_PLUGINS=victoriametrics-logs-datasource
```

Configure the datasource using the `victoriametrics-logs-datasource` plugin type, not the Loki plugin.

**Source:** `architecture/platform.md` — Monitoring: Grafana datasource

---

## platform-caddy-cloud86-no-plugin

**Severity:** HIGH (historical — DNS has been migrated)

**Trigger:** Setting up wildcard TLS for `*.getklai.com` with Caddy

Cloud86 (former DNS provider for getklai.com) has no Caddy DNS plugin. Caddy requires a DNS-01 ACME challenge to issue wildcard certificates. Without a plugin, wildcard TLS is not possible.

**Resolution (implemented March 2026):** DNS migrated from Cloud86 to Hetzner DNS.
- Hetzner DNS is free, fully European, GDPR-compliant
- Caddy plugin in use: `github.com/caddy-dns/hetzner`
- Custom Caddy image: `caddy-hetzner:latest` (built via `xcaddy build --with github.com/caddy-dns/hetzner`)
- Wildcard cert `*.getklai.com` is active via Let's Encrypt DNS-01 challenge

**Do not revert DNS to Cloud86 or any provider without a Caddy plugin.**

**Source:** `architecture/platform.md` — Per-Tenant Routing: Caddy + Wildcard DNS

---

## platform-caddy-not-auto-routing

**Severity:** HIGH

**Trigger:** Assuming Caddy automatically routes to new tenant containers

Caddy does NOT automatically discover new Docker containers. Provisioning a new tenant does not automatically make their subdomain work.

**Implemented architecture (as of 2026-03-07):**
- Caddy has one wildcard `*.getklai.com` block handling known services (auth, chat.getklai.com, grafana, llm)
- Per-tenant LibreChat blocks are **appended** to the Caddyfile at provisioning time by the portal-api
- After appending, the Caddy **container is restarted** via Docker SDK so it picks up the new block
- The Caddyfile is bind-mounted (`./caddy/Caddyfile:/etc/caddy/Caddyfile`) so the portal-api can write to it

**Source:** `architecture/platform.md` — Per-Tenant Routing: Tenant Router

---

## platform-caddy-admin-off-reload

**Severity:** HIGH

**Trigger:** Trying to reload Caddy config via Admin API when `admin off` is set

When Caddy is configured with `admin off`, the Admin API is completely disabled. Any call to `POST http://caddy:2019/load` or `/reload` will fail with a connection error.

**Wrong:**
```python
# This will always fail when admin off is set
await httpx.post("http://caddy:2019/load", content=config)
```

**Correct:**
```python
# Restart the container via Docker SDK — Caddy re-reads config on startup
import docker
client = docker.from_env()
container = client.containers.get("klai-core-caddy-1")
container.restart(timeout=10)
```

**Requirements:**
- The portal-api must have Docker API access (via `DOCKER_HOST` or socket mount)
- The Docker socket proxy must allow `POST: 1` (container restart is a POST operation)
- `settings.caddy_container_name` must match the actual running container name (`klai-core-caddy-1`)

**Trade-off:** Container restart causes ~1s TLS interruption. Acceptable at current scale; revisit when tenants exceed ~50.

**Source:** provisioning.py `_reload_caddy()`

---

## platform-rag-api-non-lite-image

**Severity:** HIGH

**Trigger:** Deploying LibreChat RAG with HuggingFace TEI embeddings (Phase 2)

The lite RAG API image does NOT support TEI embeddings. The full image is required.

**Correct image:**
```bash
ghcr.io/danny-avila/librechat-rag-api-dev:latest
# NOT: ghcr.io/danny-avila/librechat-rag-api-dev-lite:latest
```

**Also:** `EMBEDDINGS_MODEL` must be the TEI service URL, not a model name.

**Source:** `architecture/platform.md` — RAG Stack: LibreChat rag_api + HuggingFace TEI

---

## platform-whisper-cuda-version

**Severity:** HIGH

**Trigger:** Deploying faster-whisper on core-01 or ai-01

CTranslate2 (the Whisper runtime) requires CUDA 12 + cuDNN 9. Version mismatch is the most common deployment failure.

**Prevention:**
1. Verify CUDA version before deploying: `nvidia-smi`
2. Verify cuDNN version: `cat /usr/local/cuda/include/cudnn_version.h | grep CUDNN_MAJOR`
3. Use a base Docker image that already pins `cuda:12.x-cudnn9`

**Source:** `architecture/platform.md` — GPU Resource Management: faster-whisper on H100

---

## platform-fastapi-background-tasks-db-session

**Severity:** CRIT

**Trigger:** Passing a request-scoped `db: AsyncSession = Depends(get_db)` to a FastAPI `BackgroundTasks` function

FastAPI's request-scoped database session (`Depends(get_db)`) is closed when the HTTP response is sent. `BackgroundTasks` run **after** the response. Passing the session to the task means it will be closed (or in an undefined state) when the task tries to use it.

**Wrong:**
```python
@router.post("/signup")
async def signup(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # ... create records in db ...
    background_tasks.add_task(provision_tenant, org.id, db)  # WRONG: db is closed by the time this runs
    return Response(status_code=201)
```

**Correct:**
```python
@router.post("/signup")
async def signup(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # ... create records in db ...
    background_tasks.add_task(provision_tenant, org.id)  # pass only IDs, not the session
    return Response(status_code=201)

async def provision_tenant(org_id: int) -> None:
    async with AsyncSessionLocal() as db:  # open a new session
        # ... use db ...
```

**Rule:** Background tasks that need database access must open their own session via `AsyncSessionLocal()`. Never pass a request-scoped session to a background task.

---

## caddy-basicauth-monitoring-conflict

**Severity:** HIGH

**Trigger:** Adding `basic_auth` to a Caddy route that is also monitored by Uptime Kuma or any HTTP health checker

When you add `basic_auth` to a Caddy route, ALL requests to that host get challenged for credentials — including Uptime Kuma's health check. The monitor switches from "up" to "down" immediately after deploy.

**What went wrong:**
```
Grafana monitor: https://grafana.getklai.com/api/health
→ After adding basic_auth: 401 Unauthorized
→ Uptime Kuma marks service as DOWN
```

**Fix:**
Create a named matcher for the health path BEFORE the `basic_auth` handler:
```caddyfile
@grafana-health {
    host grafana.{$DOMAIN}
    path /api/health
}
handle @grafana-health {
    reverse_proxy grafana:3000
}

@grafana host grafana.{$DOMAIN}
handle @grafana {
    basic_auth { ... }
    reverse_proxy grafana:3000
}
```

**Prevention:**
Before deploying `basic_auth` to any route: check Uptime Kuma for monitors on that host. Add the health path bypass first, deploy, confirm monitors are green, then confirm auth works.

---

## caddy-log-not-in-handle

**Severity:** MEDIUM

**Trigger:** Adding a `log` directive inside a `handle` or `handle @matcher` block in Caddy

The `log` directive is a site-level directive and cannot be placed directly inside a `handle` block. Caddy will refuse to start with: `directive 'log' is not an ordered HTTP handler, so it cannot be used here`.

**Wrong:**
```caddyfile
handle @grafana {
    log { output file /var/log/caddy/access.log }  # ERROR
    reverse_proxy grafana:3000
}
```

**Correct:**
```caddyfile
*.example.com {
    log {
        output file /var/log/caddy/access.log { roll_size 10mb }
    }

    handle @grafana {
        reverse_proxy grafana:3000
    }
}
```

---

## caddy-basicauth-deprecated

**Severity:** LOW

**Trigger:** Using `basicauth` in a Caddyfile

Caddy v2.6+ deprecated `basicauth` in favour of `basic_auth` (with underscore). Using `basicauth` still works but logs a warning on every startup. Some future Caddy version may remove it.

**Fix:** Replace `basicauth` with `basic_auth` throughout the Caddyfile.

---

## platform-zitadel-project-grant-vs-user-grant

**Severity:** HIGH

**Trigger:** Assigning a role to a user in Zitadel (e.g. `org:owner` at signup)

Zitadel has two separate grant APIs that do completely different things:

| API | Purpose |
|-----|---------|
| `POST /management/v1/projects/{projectId}/grants` | Grants a project to another **org** — not for individual users |
| `POST /management/v1/users/{userId}/grants` | Assigns a role to a **specific user** — this is what you want |

**Wrong (creates an org-level project grant, user gets no role):**
```python
await http.post(
    f"/management/v1/projects/{project_id}/grants",
    json={"grantedOrgId": org_id, "roleKeys": ["org:owner"]},
)
```

**Correct (assigns role to the user, role appears in their token):**
```python
await http.post(
    f"/management/v1/users/{user_id}/grants",
    headers={"x-zitadel-orgid": org_id},
    json={
        "projectId": settings.zitadel_project_id,
        "roleKeys": ["org:owner"],
    },
)
```

**Also required:** The role (`org:owner`) must be defined on the Zitadel project before it can be assigned.

**Symptom when wrong:** User is created, token is issued, but `urn:zitadel:iam:org:project:roles` is empty in the userinfo response. The portal's `klai:isAdmin` flag is never set.

**Source:** `klai-portal/backend/app/services/zitadel.py` — `grant_user_role()`

---

## platform-zitadel-resourceowner-claim-unreliable

**Severity:** HIGH

**Trigger:** Using `urn:zitadel:iam:user:resourceowner:id` from userinfo to look up the user's organization in PostgreSQL

The `urn:zitadel:iam:user:resourceowner:id` claim contains the Zitadel org ID of the **org that owns the user** — but this claim is not always present in the userinfo response, and relying on it creates a fragile coupling to Zitadel's internal data model.

**Wrong:**
```python
info = await zitadel.get_userinfo(token)
zitadel_org_id = info.get("urn:zitadel:iam:user:resourceowner:id")
# Look up portal_orgs directly by zitadel_org_id
org = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == zitadel_org_id))
```

**Correct — use `sub` → `portal_users` → `portal_orgs`:**
```python
info = await zitadel.get_userinfo(token)
zitadel_user_id = info.get("sub")  # Always present, stable
result = await db.execute(
    select(PortalOrg)
    .join(PortalUser, PortalUser.org_id == PortalOrg.id)
    .where(PortalUser.zitadel_user_id == zitadel_user_id)
)
org = result.scalar_one_or_none()
```

**Why:** `sub` is the OIDC subject claim — always present and stable. The `portal_users` table is the authoritative mapping of Zitadel user → portal org. This also means user-org membership is controlled by the portal, not implicitly derived from where a user lives in Zitadel.

**Source:** `klai-portal/backend/app/api/billing.py`, `admin.py` — `_get_org()`, `_get_caller_org()`

---

## platform-sso-cache-single-instance

**Severity:** HIGH

**Trigger:** Scaling portal-api to multiple instances, or deploying a second instance for staging

The `_sso_cache` and `_pending_totp` caches in `portal-api/app/api/auth.py` are **in-memory Python dicts**. They are not shared between processes or containers.

**What goes wrong:**
- User logs in via instance A → SSO token stored in A's memory
- Next request routed to instance B → B has no record of the token → 401 Unauthorized
- Result: users get logged out randomly under load, or cannot complete TOTP login

**Current state:** Safe because portal-api runs as a single container on core-01. There is no horizontal scaling and no blue/green deployment.

**When this becomes a problem:**
- Adding a second portal-api replica (load balancing)
- Blue/green deploy where both containers are briefly live
- Moving to a multi-instance setup

**Resolution when needed:**
Replace in-memory caches with Redis:
```python
# Replace TTLCache with Redis-backed cache
# _sso_cache.put(value) → redis.setex(token, ttl, json.dumps(value))
# _sso_cache.get(token) → json.loads(redis.get(token))
# _sso_cache.pop(token) → redis.delete(token)
```

Redis is already in the stack on `klai-net-redis`. Add portal-api to that network.

**Source:** `klai-portal/backend/app/api/auth.py` — TTLCache, `_sso_cache`, `_pending_totp`

---

## caddy-permissions-policy-blocks-mediadevices

**Severity:** CRIT

**Trigger:** Browser API (`getUserMedia`, camera, geolocation) silently fails — no permission dialog, no error shown to user

The default Caddy global `Permissions-Policy` header may deny browser APIs entirely. A value of `microphone=()` blocks `navigator.mediaDevices.getUserMedia` at the browser level. The browser never shows a permission dialog — the call just fails silently (or with a `NotAllowedError` that looks like a user denial).

**Root cause:** Caddyfile had:
```caddyfile
header Permissions-Policy "geolocation=(), microphone=(), camera=()"
```

`microphone=()` means: deny access to microphone for ALL origins, including the page itself.

**Fix:**
```caddyfile
header Permissions-Policy "geolocation=(), microphone=self, camera=()"
```

`microphone=self` means: allow the page's own origin to request mic access via `getUserMedia`.

**Diagnosis:**
```bash
# Check what header the server is actually sending:
curl -sI https://getklai.getklai.com/ | grep -i permissions-policy
```

**After fixing the header:**
Users who previously visited the page may have a cached "denied" permission in their browser. They must manually reset it:
Chrome/Brave: click the lock icon → Site settings → Microphone → Reset to default.

**Deploy:**
```bash
scp klai-infra/core-01/caddy/Caddyfile core-01:/opt/klai/caddy/Caddyfile
ssh core-01 'docker restart klai-core-caddy-1'
```

**Note:** `caddy reload` will not work if `admin off` is set — see `platform-caddy-admin-off-reload`.

---

## platform-alembic-shared-postgres-schema-conflict

**Severity:** CRIT

**Trigger:** Deploying a second FastAPI service (e.g. `scribe-api`) that uses Alembic migrations to the same PostgreSQL database as an existing service (e.g. `portal-api`)

Alembic stores its migration version table as `alembic_version` in the `public` schema by default. If two services share the same database and both use the default location, they will overwrite each other's version table — causing migrations to fail or be silently skipped.

**Symptom:**
```
FAILED: Can't locate revision identified by 'abc123'
# or: migration appears to run but the schema is not created
```

**Fix:** Each service must scope its Alembic version table to its own schema, AND create the schema in a **separate committed transaction** before Alembic starts its own migration transaction.

In `alembic/env.py`:

```python
import sqlalchemy as sa

# IMPORTANT: run_migrations_offline must also set version_table_schema
def run_migrations_offline() -> None:
    context.configure(
        url=...,
        include_schemas=True,
        version_table="alembic_version",
        version_table_schema="scribe",
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table="alembic_version",
        version_table_schema="scribe",   # scope to this service's schema
    )
    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    engine = create_async_engine(settings.postgres_dsn)
    # Commit schema creation BEFORE starting the migration transaction.
    # If CREATE SCHEMA is inside the migration transaction, alembic cannot
    # create the version table in the not-yet-committed schema.
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS scribe"))
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()
```

**Rule:** Every FastAPI service that shares a PostgreSQL instance must:
1. Set `version_table_schema` to its own dedicated schema
2. Create that schema in a **separate `engine.begin()` block** before calling `run_sync(do_run_migrations)` — not inside `do_run_migrations` itself

Never rely on the default `public.alembic_version`.

**Source:** `klai-scribe/scribe-api/alembic/env.py` — fix commits `691db16` + `3e80070`

---

## platform-zitadel-login-v2-recovery

**Severity:** CRIT

**Trigger:** Portal login is broken AND Zitadel admin console (`auth.getklai.com/ui/console`) redirects to the broken portal login — creating a chicken-and-egg deadlock

When Login V2 is active (`required: true`), ALL Zitadel OIDC flows — including the admin console — redirect to the portal's custom login. If that login is broken, you cannot access Zitadel to fix it.

**Break the deadlock: delete the Login V2 projection row directly in PostgreSQL**

```bash
POSTGRES=$(docker ps --format '{{.Names}}' | grep -i postgres | head -1)
docker exec $POSTGRES psql -U zitadel -d zitadel -c \
  "DELETE FROM projections.instance_features5
   WHERE instance_id = '362757920133218310' AND key = 'login_v2';"
```

Takes effect immediately — no Zitadel restart needed. `auth.getklai.com/ui/console` now uses Zitadel's built-in login.

**Re-enable Login V2 after fixing the underlying issue:**

Write to a file to avoid shell quoting problems:

```bash
cat > /tmp/fix_login_v2.sql << 'EOF'
UPDATE projections.instance_features5
SET value = '{"base_uri": {"Host": "getklai.getklai.com", "Path": "", "User": null, "Opaque": "", "Scheme": "https", "RawPath": "", "Fragment": "", "OmitHost": false, "RawQuery": "", "ForceQuery": false, "RawFragment": ""}, "required": true}'::jsonb,
    change_date = NOW(),
    sequence = 5
WHERE instance_id = '362757920133218310' AND key = 'login_v2';
EOF
POSTGRES=$(docker ps --format '{{.Names}}' | grep -i postgres | head -1)
docker cp /tmp/fix_login_v2.sql $POSTGRES:/tmp/fix_login_v2.sql
docker exec $POSTGRES psql -U zitadel -d zitadel -f /tmp/fix_login_v2.sql
```

**Critical: exact JSON format for the `value` column**

The value uses Go's `url.URL` struct serialization. Do not abbreviate or change the structure:

```json
{
  "base_uri": {
    "Host": "getklai.getklai.com",
    "Path": "",
    "User": null,
    "Opaque": "",
    "Scheme": "https",
    "RawPath": "",
    "Fragment": "",
    "OmitHost": false,
    "RawQuery": "",
    "ForceQuery": false,
    "RawFragment": ""
  },
  "required": true
}
```

If you get this wrong, the projection row is written with `value = null` and Login V2 has no redirect target.

**Zitadel instance constants (core-01, do not guess):**

| Name | Value |
|---|---|
| Instance ID | `362757920133218310` |
| Feature aggregate creator | `362760545968848902` |
| portal-api machine user ID | `362780577813757958` |

---

## platform-zitadel-pat-invalid-after-upgrade

**Severity:** CRIT

**Trigger:** `create_session failed 401: Errors.Token.Invalid (AUTH-7fs1e)` in portal-api logs after a Zitadel version upgrade or after `portal-api` is restarted

The portal-api uses a Personal Access Token (PAT) to call Zitadel's `/v2/sessions` API as a service account. This PAT can become invalid after major Zitadel upgrades. The failure is masked as long as the portal-api is running — it caches active sessions in memory. After a restart the cache is empty and every login attempt hits the invalid PAT immediately.

**Symptoms:**
- All users get "E-mailadres of wachtwoord is onjuist" on login
- `docker logs klai-core-portal-api-1` shows `create_session failed 401: Errors.Token.Invalid`

**Fix: rotate the PAT**

1. Go to `https://auth.getklai.com/ui/console` (if Login V2 blocks you, see `platform-zitadel-login-v2-recovery`)
2. Navigate to **Users** → **Service Accounts** tab → **Portal API**
3. Go to **Personal Access Tokens** → **+ New** — copy the token value (shown once only)
4. On core-01:
   ```bash
   sed -i 's|^PORTAL_API_ZITADEL_PAT=.*|PORTAL_API_ZITADEL_PAT=<new-token>|' /opt/klai/.env
   cd /opt/klai && docker compose up -d portal-api   # must be up -d, not restart
   docker exec klai-core-portal-api-1 env | grep PORTAL_API_ZITADEL_PAT  # verify
   ```
5. Update `.env.sops` in the repo — do this on the MacBook from `/tmp` (to avoid `.sops.yaml` path mismatch):
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

**Verify the new PAT works before committing:**
```bash
ssh core-01 "curl -s https://auth.getklai.com/v2/sessions \
  -H 'Authorization: Bearer <new-token>' \
  -H 'Content-Type: application/json' \
  -d '{\"checks\":{\"user\":{\"loginName\":\"test\"},\"password\":{\"password\":\"test\"}}}'"
# Expected: {"code":5, "message":"User could not be found"} — not 401
```

---

## platform-vexa-timeout-looks-like-bug

**Severity:** MED

**Trigger:** Meeting stays in "Recording" status for up to 60 seconds after everyone leaves Google Meet

The Vexa bot intentionally waits `everyoneLeftTimeout` milliseconds before stopping. During that window the portal correctly shows "Recording" — this is not a bug, it is bot lifecycle behavior. The default in `process.py` was 60000ms (60 seconds), making every normal meeting end look like it was stuck.

**Why it happens:**
The Vexa bot orchestrator config lives in `/opt/klai/vexa-patches/process.py`, mounted as a volume override into the `vexa-bot-manager` container. This file is not in the codebase — it is on the server. Its timeout values are invisible unless you explicitly read it.

**Prevention:**
1. When a meeting appears "stuck in Recording", read `process.py` before looking at portal-api code
2. Confirmed working values (as of March 2026):
   - `everyoneLeftTimeout: 5000` (was 60000)
   - `noOneJoinedTimeout: 30000` (was 120000)
   - `waitingRoomTimeout: 60000` (was 300000)
3. After any Vexa deployment, verify these values on the server: `ssh core-01 cat /opt/klai/vexa-patches/process.py`

**See also:** `patterns/platform.md#platform-vexa-bot-lifecycle`

---

## platform-vexa-guard-breaks-stop-flow

**Severity:** HIGH

**Trigger:** Adding a status guard to the webhook handler to "prevent race conditions" while debugging Vexa

The normal Stop flow is: user clicks Stop → `status = processing` → Vexa fires `completed` webhook → `run_transcription`. If you add a guard like "skip if status == processing" to the webhook handler mid-debug, you block step 3. Transcription never runs after clicking Stop.

**What went wrong:**
A guard was added to prevent a perceived race condition between the bot_poller and the webhook. The guard was correct in intent but wrong in placement — it blocked the primary happy path, not just the race.

**Why it happens:**
When debugging a multi-path system (webhook + poller + manual stop), partial guards feel safe but break the coordinated flow. The DB showing `status: done` after the fix is also unreliable as a signal — the poller or a late webhook can resolve the meeting independently, masking whether the fix actually worked.

**Prevention:**
1. Never add status guards inside the webhook handler — the `completed` webhook IS the expected trigger for `run_transcription`
2. When the DB shows `status: done` after a change, check `docker logs` to confirm which path resolved it (webhook vs poller vs manual)
3. Before modifying the webhook handler, draw the full state machine: Idle → Joining → Active → Stopping → Completed

**See also:** `patterns/platform.md#platform-vexa-bot-lifecycle`

---

## docs-app (klai-docs / Next.js)

---

## platform-docs-app-port

**Severity:** HIGH

**Trigger:** Calling the docs-app internal API from portal-api (`docs_client.py`)

The docs-app (klai-docs) runs on port **3010**, not 3000. Docker service name is `docs-app`.

**Wrong:**
```python
base_url="http://docs-app:3000"
```

**Correct:**
```python
base_url="http://docs-app:3010/docs"
```

**Source:** SPEC-KB-003 integration debugging, 2026-03-25

---

## platform-docs-app-basepath

**Severity:** HIGH

**Trigger:** Calling any API endpoint on docs-app

The Next.js app has `basePath: "/docs"` in `next.config.ts`. All routes — including internal API routes — are served under `/docs/api/...`, not `/api/...`.

**Wrong:**
```
POST http://docs-app:3010/api/orgs/{slug}/kbs   → 404 Not Found
```

**Correct:**
```
POST http://docs-app:3010/docs/api/orgs/{slug}/kbs
```

Use `base_url="http://docs-app:3010/docs"` in the httpx client so relative paths resolve correctly.

**Source:** SPEC-KB-003 integration debugging, 2026-03-25

---

## platform-docs-app-visibility-values

**Severity:** HIGH

**Trigger:** Creating a KB via the docs-app API when the portal visibility is `internal`

The docs-app DB has a check constraint that only accepts `public` or `private` as visibility values. The portal uses `internal` as its third visibility option. Passing `internal` causes a 500 from docs-app.

**Wrong:**
```python
json={"visibility": "internal"}  # → 500 Internal Server Error
```

**Correct:**
```python
docs_visibility = "public" if visibility == "public" else "private"
json={"visibility": docs_visibility}
```

Map portal `internal` → docs-app `private` before calling the API.

**Source:** SPEC-KB-003 integration debugging, 2026-03-25

---

## platform-docs-app-error-logging

**Severity:** MEDIUM

**Trigger:** Debugging docs-app integration failures from portal-api logs

Without the response body in the log, all failures look the same (`httpx.HTTPStatusError`). Always log status code + response text.

**Wrong:**
```python
log.exception("Gitea provisioning failed for KB slug=%s", kb_slug)
```

**Correct:**
```python
log.error(
    "Gitea provisioning failed for KB slug=%s: %s %s",
    kb_slug,
    exc.response.status_code,
    exc.response.text[:500],
)
```

Also catch `httpx.ConnectError` separately — a connection refused error has no `.response` attribute and will itself raise an `AttributeError` if you try to access it.

**Source:** SPEC-KB-003 integration debugging, 2026-03-25

---

## platform-falkordb-sspLv1-license

**Severity:** MED

**Trigger:** Evaluating FalkorDB as a graph database for a self-hosted deployment

FalkorDB is licensed under SSPLv1, not Apache 2.0. This surprises people who assume it is permissive open-source like Redis was before its license change.

**What this means in practice:**
- SSPLv1 requires that if you offer FalkorDB *as a service to others* (SaaS), you must open-source your entire service stack.
- For **internal self-hosted use** (running it on your own infrastructure for your own users), SSPLv1 imposes no obligations. This is Klai's use case — fine.
- Decision: FalkorDB is approved for production in the Klai knowledge graph stack (SPEC-KB-011).

**Also watch out for Neo4j Community Edition:**
Neo4j Community Edition uses GPLv3, and Neo4j's own documentation includes "non-production use" language in places. Avoid Neo4j Community for production deployments — GPLv3 plus ambiguous vendor messaging creates legal risk.

**Prevention:**
1. When evaluating a new graph/vector/AI database, check its license before writing any SPEC
2. SSPLv1 = fine for self-hosted internal use, not fine for SaaS offering
3. Default to checking the GitHub repo license file, not marketing copy

**Source:** SPEC-KB-011 research, 2026-03-26

---

## platform-hipporag2-vs-graphiti-different-layers

**Severity:** HIGH

**Trigger:** Evaluating HippoRAG2 vs Graphiti as "competing alternatives" for a knowledge graph layer

HippoRAG2 and Graphiti operate at different layers of the retrieval stack. Treating them as direct alternatives causes incorrect architecture decisions.

**The distinction:**

| | HippoRAG2 | Graphiti |
|---|---|---|
| What it does | Retrieval only (Personalized PageRank traversal over an existing graph) | End-to-end: ingest + entity extraction + bi-temporal graph + retrieval |
| Temporal model | None — static graph snapshot | Full bi-temporal (fact validity + ingestion time) |
| Who builds the graph | You — HippoRAG2 assumes a graph already exists | Graphiti — handles entity extraction and edge creation |
| Integration complexity | Needs a separate graph construction pipeline | Self-contained |

**What went wrong:** The initial architecture docs (klai-knowledge-architecture.md §5.3 and §13.3) listed "HippoRAG2 + SpaCy" as the recommended evaluation path for the graph layer. This was wrong because HippoRAG2 has no temporal model and SpaCy misses contextual entity resolution. The docs were corrected as part of SPEC-KB-011.

**Rule:** Before comparing retrieval frameworks, determine which layer each operates at. A retrieval-only library cannot replace an ingestion + storage + retrieval system.

**Source:** SPEC-KB-011 research, 2026-03-26

---

## platform-tei-embedding-timeout

**Severity:** HIGH

**Trigger:** knowledge-ingest returns 500 errors during large document batches sent to the TEI embedder

Large document batches legitimately take up to 35 seconds on TEI (up to 24s queue time + 11s inference time). With the default `timeout=30.0` in `httpx`, the client times out before TEI finishes. The `httpx.ReadTimeout` propagates unhandled and the connector receives a 500.

**Symptom:**
```
# Connector log:
Failed to process X.md: Server error '500 Internal Server Error'

# knowledge-ingest log:
httpx.ReadTimeout: timed out while reading response from http://tei:8080/embed
```

**Why it happens:**
The httpx client in `knowledge_ingest/embedder.py` had a hardcoded `timeout=30.0`. TEI processes batches sequentially and queues incoming requests; under load, total round-trip time exceeds 30s.

**Prevention:**
1. Set TEI client timeout to at least 120s (configurable via `TEI_TIMEOUT` env var)
2. Add retry with exponential backoff: 3 attempts, 1s/2s/4s waits on timeout and 5xx responses
3. Split large inputs into batches of 32 before sending to reduce TEI queue pressure

```python
# embedder.py — correct pattern
timeout = float(os.getenv("TEI_TIMEOUT", "120"))
client = httpx.AsyncClient(timeout=timeout)

for attempt in range(3):
    try:
        response = await client.post(url, json={"inputs": batch})
        response.raise_for_status()
        break
    except (httpx.ReadTimeout, httpx.HTTPStatusError) as e:
        if attempt == 2:
            raise
        await asyncio.sleep(2 ** attempt)
```

**Source:** commit `2ae26bd` — knowledge-ingest TEI timeout + retry + batching fix

---

## platform-librechat-redis-config-cache

**Severity:** HIGH

**Trigger:** Changing `librechat.yaml` and restarting the LibreChat container, expecting the new config to be active

When `USE_REDIS=true` (Klai's setup), LibreChat caches the parsed `librechat.yaml` in Redis under `CacheKeys.APP_CONFIG` with **no TTL**. A container restart reads from Redis, not from disk — the new YAML is silently ignored.

**Wrong procedure:**
```bash
docker restart librechat-{slug}   # ← old config still served from Redis
```

**Correct procedure:**
```bash
docker exec redis redis-cli FLUSHALL   # flush Redis first
docker restart librechat-{slug}        # now reads fresh from disk
```

**Warning:** `FLUSHALL` clears all Redis data including sessions and other caches. If you want to be surgical, find the specific key prefix (`_BASE_`, `STARTUP_CONFIG`) and delete only those — but FLUSHALL is safe during a maintenance window.

**Source:** LibreChat issue #11175 — confirmed, no fix in upstream as of March 2026.

**See also:** `patterns/platform.md#platform-librechat-config-lifecycle`

---

## platform-librechat-addparams-no-envvars

**Severity:** MEDIUM

**Trigger:** Trying to use `${ENV_VAR}` or `{{USER_VAR}}` inside `addParams` in `librechat.yaml`

`addParams` values are literal — they do not support environment variable substitution or user template variables. Only `apiKey` and `baseURL` fields in a custom endpoint support `${ENV_VAR}` syntax.

**Wrong:**
```yaml
endpoints:
  custom:
    - name: "Klai AI"
      addParams:
        x-tenant-id: "${KLAI_ORG_SLUG}"   # ← does NOT work
        x-user-id: "{{LIBRECHAT_USER_ID}}"  # ← does NOT work
```

**Correct approach:** Inject dynamic per-request context at the LiteLLM layer via a pre-call hook (e.g., `KlaiKnowledgeHook`), or use MCP server headers which do support `{{LIBRECHAT_USER_ID}}` interpolation.

---

## platform-librechat-dual-system-message

**Severity:** MEDIUM

**Trigger:** Using both `modelSpecs[].preset.promptPrefix` in `librechat.yaml` AND injecting a system message via the KlaiKnowledgeHook (or any LiteLLM pre-call hook)

LibreChat applies `promptPrefix` as a system message **before** sending the request to LiteLLM. KlaiKnowledgeHook intercepts **at the LiteLLM layer** — after LibreChat has already built its request. Result: the LLM receives two separate system messages.

**Execution order:**
1. LibreChat builds messages array → inserts `promptPrefix` as `role: system`
2. LibreChat POSTs to LiteLLM
3. KlaiKnowledgeHook injects its own context into `data["messages"]`

Most modern LLMs accept multiple system messages without breaking, but the behavior is model-dependent. To avoid ambiguity:
- If the hook's context is additive (knowledge chunks), append to the existing system message rather than prepend a new one
- Check `data["messages"][0]["role"] == "system"` and extend its content rather than inserting a new message

**Note:** The correct field name is `promptPrefix`, not `systemPrompt`. The librechat.yaml `preset` schema does not have a `systemPrompt` key.

---

## platform-portal-api-deploy-env-preflight

**Severity:** CRIT

**Trigger:** Running `docker compose pull portal-api && docker compose up -d portal-api` after merging security commits or any code change that adds new required fields to `portal/backend/app/core/config.py`

Portal-api validates ALL required env vars at startup and performs a live PAT check against Zitadel before accepting any traffic. If a new required field has no value in `.env`, the container crashes immediately with a `ValueError`. Because **all authentication goes through portal-api** (Login V2 routes Zitadel's own login through the portal), a portal-api crash creates a total auth outage — no one can log in, including to the Zitadel console.

**What happened (2026-03-27):**
1. Security commits added three new required fields to `config.py`: `zitadel_pat`, `portal_secrets_key`, `sso_cookie_key`
2. `docker compose up -d portal-api` was run without pre-flight check
3. Portal-api crashed with `ValueError: AES-256 requires a 32-byte key, got 0 bytes`
4. All auth broke — Login V2 routes Zitadel's login through the portal, so even the Zitadel console was inaccessible
5. Recovery required: database fix for Login V2, manual PAT creation from the user, manual secrets generation

**How to check before deploying a new portal-api image:**

```bash
# 1. Check what changed in config.py (new required fields have no default value)
cd ~/Server/projects/klai
git log --oneline portal/backend/app/core/config.py | head -5
git show HEAD:portal/backend/app/core/config.py | grep -E '^\s+\w+: str\s*$|^\s+\w+: \w+\s*$' | grep -v '='

# 2. Pre-flight: check which env vars the container will receive
ssh core-01 "cd /opt/klai && docker compose config portal-api | grep -A 80 'environment:'"

# 3. Verify every required field (no default in config.py) has a non-empty value in the output
#    Required fields in config.py have NO '= ""' or default value — they look like:
#    zitadel_pat: str
#    portal_secrets_key: str
#    sso_cookie_key: str

# 4. Only then deploy
ssh core-01 "cd /opt/klai && docker compose up -d portal-api"
```

**If portal-api is already down and all auth is broken:**
1. First break the Login V2 deadlock: `platform-zitadel-login-v2-recovery`
2. Get the missing secrets (PAT from Zitadel console, generate keys locally)
3. Add to `/opt/klai/.env` with single quotes: `echo 'PORTAL_API_ZITADEL_PAT=...' >> /opt/klai/.env`
4. Do pre-flight check, then `docker compose up -d portal-api`
5. Re-enable Login V2: use the INSERT from `platform-zitadel-login-v2-recovery`
6. Update `.env.sops` from MacBook `/tmp` (see `platform-zitadel-pat-invalid-after-upgrade` step 5)

**Required secrets and how to generate them:**

| Env var | Config field | How to generate |
|---|---|---|
| `PORTAL_API_ZITADEL_PAT` | `zitadel_pat` | Zitadel console → Users → Service Accounts → Portal API → Personal Access Tokens → + New |
| `PORTAL_API_PORTAL_SECRETS_KEY` | `portal_secrets_key` | `openssl rand -hex 32` (must be 64 hex chars = 32 bytes) |
| `PORTAL_API_SSO_COOKIE_KEY` | `sso_cookie_key` | `python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

**Rule:** Before deploying ANY new portal-api image: `git diff HEAD portal/backend/app/core/config.py` to see if new required fields were added. If yes, generate and add the missing vars to `.env` AND `.env.sops` BEFORE deploying.

**Rule:** The PAT and encryption keys are now in `core-01/.env.sops`. Never regenerate them — only rotate the PAT if it becomes invalid (see `platform-zitadel-pat-invalid-after-upgrade`).

---

## See Also

- [patterns/platform.md](../patterns/platform.md) - Correct platform configuration patterns
- [architecture/platform.md](architecture/platform.md) - Full compatibility review
