# Platform Pitfalls

> LiteLLM, LibreChat, vLLM, Zitadel, Caddy, Meilisearch — Klai AI stack.
> Most entries are derived from the compatibility review in `architecture/platform.md`.
> For step-by-step emergency recovery procedures, see `runbooks/platform-recovery.md`.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [platform-litellm-vllm-provider-prefix](#platform-litellm-vllm-provider-prefix) | HIGH | Use `hosted_vllm/` prefix, not `openai/` |
| [platform-litellm-drop-params](#platform-litellm-drop-params) | HIGH | Always set `drop_params: true` in LiteLLM config |
| [platform-vllm-gpu-memory-utilization](#platform-vllm-gpu-memory-utilization) | CRIT | Split GPU memory between two vLLM instances |
| [platform-vllm-sequential-startup](#platform-vllm-sequential-startup) | CRIT | Start vLLM instances sequentially, never in parallel |
| [platform-vllm-mps-enforce-eager](#platform-vllm-mps-enforce-eager) | HIGH | Add `--enforce-eager` when using NVIDIA MPS |
| [platform-librechat-oidc-reuse-tokens](#platform-librechat-oidc-reuse-tokens) | CRIT | Never set `OPENID_REUSE_TOKENS=true` |
| [platform-librechat-username-claim](#platform-librechat-username-claim) | HIGH | Set `OPENID_USERNAME_CLAIM=preferred_username` |
| [platform-grafana-victorialogs-loki-incompatible](#platform-grafana-victorialogs-loki-incompatible) | HIGH | VictoriaLogs uses LogsQL datasource, not Loki |
| [platform-caddy-not-auto-routing](#platform-caddy-not-auto-routing) | HIGH | Caddy never auto-routes to new containers |
| [platform-caddy-admin-off-reload](#platform-caddy-admin-off-reload) | HIGH | Reload Caddy by restarting container, not Admin API |
| [platform-rag-api-non-lite-image](#platform-rag-api-non-lite-image) | HIGH | Use full TEI image for cross-encoder reranker |
| [platform-whisper-cuda-version](#platform-whisper-cuda-version) | HIGH | Match faster-whisper CUDA version to GPU drivers |
| [platform-fastapi-background-tasks-db-session](#platform-fastapi-background-tasks-db-session) | CRIT | Never pass request-scoped DB session to BackgroundTasks |
| [caddy-basicauth-monitoring-conflict](#caddy-basicauth-monitoring-conflict) | HIGH | `basic_auth` blocks Uptime Kuma health checks |
| [caddy-log-not-in-handle](#caddy-log-not-in-handle) | MED | `log` directive must be at server block level |
| [platform-zitadel-project-grant-vs-user-grant](#platform-zitadel-project-grant-vs-user-grant) | HIGH | Use user grants, not project grants for roles |
| [platform-zitadel-resourceowner-claim-unreliable](#platform-zitadel-resourceowner-claim-unreliable) | HIGH | Don't use `resourceowner:id` claim for org lookup |
| [platform-sso-cache-single-instance](#platform-sso-cache-single-instance) | HIGH | SSO in-memory cache is not shared across instances |
| [caddy-permissions-policy-blocks-mediadevices](#caddy-permissions-policy-blocks-mediadevices) | CRIT | Set explicit Permissions-Policy for camera/mic |
| [platform-alembic-shared-postgres-schema-conflict](#platform-alembic-shared-postgres-schema-conflict) | CRIT | Multiple services need separate Alembic version tables |
| [platform-zitadel-login-v2-recovery](#platform-zitadel-login-v2-recovery) | CRIT | Login v2 breaks portal redirect; see runbooks/ for fix |
| [platform-zitadel-pat-invalid-after-upgrade](#platform-zitadel-pat-invalid-after-upgrade) | CRIT | Zitadel upgrade invalidates PATs; rotate immediately |
| [platform-vexa-timeout-looks-like-bug](#platform-vexa-timeout-looks-like-bug) | MED | 60s "Recording" delay is normal bot behavior |
| [platform-vexa-guard-breaks-stop-flow](#platform-vexa-guard-breaks-stop-flow) | HIGH | Status guard in webhook handler breaks stop flow |
| [platform-falkordb-sspLv1-license](#platform-falkordb-sspLv1-license) | MED | FalkorDB is NOT open source (SSLV1 license) |
| [platform-hipporag2-vs-graphiti-different-layers](#platform-hipporag2-vs-graphiti-different-layers) | HIGH | HippoRAG2 and Graphiti are not alternatives |
| [platform-tei-embedding-timeout](#platform-tei-embedding-timeout) | HIGH | TEI times out on large batches; use smaller batches |
| [platform-librechat-redis-config-cache](#platform-librechat-redis-config-cache) | HIGH | librechat.yaml cached in Redis; restart container to apply |
| [platform-librechat-addparams-no-envvars](#platform-librechat-addparams-no-envvars) | MED | `addParams` does not support env var interpolation |
| [platform-librechat-dual-system-message](#platform-librechat-dual-system-message) | MED | `promptPrefix` + LiteLLM hook = duplicate system messages |
| [platform-portal-api-deploy-env-preflight](#platform-portal-api-deploy-env-preflight) | CRIT | New config fields need env vars before deploying |

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

When you add `basic_auth` to a Caddy route, ALL requests to that host get challenged — including health checks. The monitor switches from "up" to "down" immediately after deploy.

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

The `urn:zitadel:iam:user:resourceowner:id` claim contains the Zitadel org ID of the org that owns the user — but this claim is not always present in the userinfo response.

**Wrong:**
```python
info = await zitadel.get_userinfo(token)
zitadel_org_id = info.get("urn:zitadel:iam:user:resourceowner:id")
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

**Why:** `sub` is the OIDC subject claim — always present and stable. `portal_users` is the authoritative mapping of Zitadel user → portal org. This also means user-org membership is controlled by the portal, not implicitly derived from where a user lives in Zitadel.

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

**Current state:** Safe because portal-api runs as a single container on core-01.

**When this becomes a problem:** Adding a second replica, blue/green deploy, or multi-instance setup.

**Resolution when needed:**
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
curl -sI https://getklai.getklai.com/ | grep -i permissions-policy
```

**After fixing the header:** Users who previously visited the page may have a cached "denied" permission. They must manually reset it: Chrome/Brave → lock icon → Site settings → Microphone → Reset to default.

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

**Trigger:** Portal login is broken AND Zitadel console (`auth.getklai.com/ui/console`) redirects to the broken portal login — chicken-and-egg deadlock

When Login V2 is active, ALL Zitadel OIDC flows — including the admin console — redirect to the portal login. If that login is broken, you cannot access Zitadel to fix it.

**Immediate fix:** Delete the Login V2 row from PostgreSQL (takes effect immediately, no restart):

```bash
POSTGRES=$(docker ps --format '{{.Names}}' | grep -i postgres | head -1)
docker exec $POSTGRES psql -U zitadel -d zitadel -c \
  "DELETE FROM projections.instance_features5
   WHERE instance_id = '362757920133218310' AND key = 'login_v2';"
```

`auth.getklai.com/ui/console` now uses Zitadel's built-in login.

**Full recovery procedure** (fix the portal issue, re-enable Login V2, instance constants): `runbooks/platform-recovery.md#zitadel-login-v2-recovery`

---

## platform-zitadel-pat-invalid-after-upgrade

**Severity:** CRIT

**Trigger:** `create_session failed 401: Errors.Token.Invalid (AUTH-7fs1e)` in portal-api logs after a Zitadel upgrade or portal-api restart

Portal-api uses a PAT to call Zitadel's `/v2/sessions` API. This PAT can become invalid after major Zitadel upgrades. The failure is masked while portal-api is running (session cache). After a restart, every login fails immediately.

**Symptoms:** All users see "E-mailadres of wachtwoord is onjuist" on login.

**Fix:** Rotate the PAT — go to Zitadel console → Users → Service Accounts tab → Portal API → Personal Access Tokens → **+ New**.

**Full rotation procedure** (apply to container, verify, update .env.sops): `runbooks/platform-recovery.md#zitadel-pat-rotation`

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
3. After any Vexa deployment, verify these values: `ssh core-01 cat /opt/klai/vexa-patches/process.py`

**See also:** `patterns/platform.md#platform-vexa-bot-lifecycle`

---

## platform-vexa-guard-breaks-stop-flow

**Severity:** HIGH

**Trigger:** Adding a status guard to the webhook handler to "prevent race conditions" while debugging Vexa

The normal Stop flow is: user clicks Stop → `status = processing` → Vexa fires `completed` webhook → `run_transcription`. If you add a guard like "skip if status == processing" to the webhook handler mid-debug, you block step 3. Transcription never runs after clicking Stop.

**What went wrong:**
A guard was added to prevent a perceived race condition between the bot_poller and the webhook. The guard was correct in intent but wrong in placement — it blocked the primary happy path, not just the race.

**Prevention:**
1. Never add status guards inside the webhook handler — the `completed` webhook IS the expected trigger for `run_transcription`
2. When the DB shows `status: done` after a change, check `docker logs` to confirm which path resolved it (webhook vs poller vs manual)
3. Before modifying the webhook handler, draw the full state machine: Idle → Joining → Active → Stopping → Completed

**See also:** `patterns/platform.md#platform-vexa-bot-lifecycle`

---

## platform-falkordb-sspLv1-license

**Severity:** MED

**Trigger:** Evaluating FalkorDB as a graph database for a self-hosted deployment

FalkorDB is licensed under SSPLv1, not Apache 2.0.

**What this means in practice:**
- SSPLv1 requires that if you offer FalkorDB *as a service to others* (SaaS), you must open-source your entire service stack.
- For **internal self-hosted use** (running it on your own infrastructure for your own users), SSPLv1 imposes no obligations. This is Klai's use case — fine.
- Decision: FalkorDB is approved for production in the Klai knowledge graph stack (SPEC-KB-011).

**Also watch out for Neo4j Community Edition:**
Neo4j Community Edition uses GPLv3. Avoid for production — GPLv3 plus ambiguous vendor "non-production use" messaging creates legal risk.

**Prevention:** When evaluating any graph/vector/AI database, check its license before writing any SPEC. SSPLv1 = fine for self-hosted internal use, not fine for SaaS offering.

**Source:** SPEC-KB-011 research, 2026-03-26

---

## platform-hipporag2-vs-graphiti-different-layers

**Severity:** HIGH

**Trigger:** Evaluating HippoRAG2 vs Graphiti as "competing alternatives" for a knowledge graph layer

HippoRAG2 and Graphiti operate at different layers of the retrieval stack. Treating them as direct alternatives causes incorrect architecture decisions.

| | HippoRAG2 | Graphiti |
|---|---|---|
| What it does | Retrieval only (Personalized PageRank over an existing graph) | End-to-end: ingest + entity extraction + bi-temporal graph + retrieval |
| Temporal model | None — static graph snapshot | Full bi-temporal (fact validity + ingestion time) |
| Who builds the graph | You — assumes a graph already exists | Graphiti — handles entity extraction and edge creation |

**Rule:** Before comparing retrieval frameworks, determine which layer each operates at. A retrieval-only library cannot replace an ingestion + storage + retrieval system.

**Source:** SPEC-KB-011 research, 2026-03-26

---

## platform-tei-embedding-timeout

**Severity:** HIGH

**Trigger:** knowledge-ingest returns 500 errors during large document batches sent to the TEI embedder

Large document batches legitimately take up to 35 seconds on TEI (up to 24s queue time + 11s inference time). With the default `timeout=30.0` in `httpx`, the client times out before TEI finishes.

**Symptom:**
```
# Connector log:
Failed to process X.md: Server error '500 Internal Server Error'

# knowledge-ingest log:
httpx.ReadTimeout: timed out while reading response from http://tei:8080/embed
```

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

**Trigger:** `docker compose up -d portal-api` after a code change that adds new required fields to `portal/backend/app/core/config.py`

Portal-api validates ALL required env vars at startup. If a new required field has no value in `.env`, the container crashes immediately. Because **all auth goes through portal-api** (Login V2 routes Zitadel's login through the portal), a portal-api crash creates a total auth outage — no one can log in, including to the Zitadel console.

**Pre-flight check — run before every portal-api deploy:**

```bash
ssh core-01 "cd /opt/klai && docker compose config portal-api | grep -A 80 'environment:'"
# Verify every required field (no default value in config.py) is non-empty before deploying
```

Required fields in `config.py` have no `= ""` or default value:
```python
zitadel_pat: str
portal_secrets_key: str
sso_cookie_key: str
```

**Rule:** When adding a new required field to `config.py`: push the value to `core-01/.env.sops` FIRST (auto-sync writes to server via `sync-env.yml`), then push the `config.py` change. The portal-api CI will block the deploy if the var is still missing.

**If portal-api is already down (all auth broken):** See `runbooks/platform-recovery.md#portal-api-deploy-outage-recovery`

---

## See Also

- [patterns/platform.md](../patterns/platform.md) - Correct platform configuration patterns
- [runbooks/platform-recovery.md](../runbooks/platform-recovery.md) - Emergency recovery procedures
- [pitfalls/docs-app.md](docs-app.md) - klai-docs integration pitfalls
- [architecture/platform.md](../architecture/platform.md) - Full compatibility review
