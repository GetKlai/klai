# Platform Pitfalls

> LiteLLM, LibreChat, vLLM, Zitadel, Caddy, Meilisearch — Klai AI stack.
> Most entries are derived from the compatibility review in `klai-website/docs/platform-beslissingen.md`.

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

**Source:** `platform-beslissingen.md` — Compatibility Review: LiteLLM to vLLM

---

## platform-litellm-drop-params

**Severity:** HIGH

**Trigger:** Setting up LiteLLM configuration for vLLM backends

`drop_params: true` must be set in `litellm_settings`. vLLM does not accept all OpenAI parameters and will error without this setting.

```yaml
litellm_settings:
  drop_params: true
```

**Source:** `platform-beslissingen.md` — Compatibility Review: LiteLLM to vLLM

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

**Source:** `platform-beslissingen.md` — Compatibility Review: vLLM gpu-memory-utilization

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

**Source:** `platform-beslissingen.md` — Compatibility Review: vLLM two instances on one GPU

---

## platform-vllm-mps-enforce-eager

**Severity:** HIGH

**Trigger:** Running vLLM with NVIDIA MPS enabled

vLLM CUDAGraph combined with MPS can cause instability (illegal memory access) on some configurations.

**Prevention:** Add `--enforce-eager` to the smaller (8B) vLLM instance to disable CUDAGraph.

```bash
vllm serve qwen3-8b ... --enforce-eager
```

**Source:** `platform-beslissingen.md` — Compatibility Review: NVIDIA MPS setup

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

**Source:** `platform-beslissingen.md` — LibreChat OIDC known issues (GitHub #9303)

---

## platform-librechat-username-claim

**Severity:** HIGH

**Trigger:** Setting up LibreChat OIDC integration with Zitadel

Without explicit configuration, LibreChat falls back to `given_name` as the username, which causes display and identity issues.

**Required setting in all LibreChat container `.env` files:**
```bash
OPENID_USERNAME_CLAIM=preferred_username
```

**Source:** `platform-beslissingen.md` — LibreChat OIDC known issues (GitHub #8672)

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

**Source:** `platform-beslissingen.md` — Auth: Zitadel + LibreChat + FastAPI + React SPA

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

**Source:** `platform-beslissingen.md` — Monitoring: Grafana datasource

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

**Source:** `platform-beslissingen.md` — Per-Tenant Routing: Caddy + Wildcard DNS

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

**Source:** `platform-beslissingen.md` — Per-Tenant Routing: Tenant Router

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

**Source:** `platform-beslissingen.md` — RAG Stack: LibreChat rag_api + HuggingFace TEI

---

## platform-whisper-cuda-version

**Severity:** HIGH

**Trigger:** Deploying faster-whisper on core-01 or ai-01

CTranslate2 (the Whisper runtime) requires CUDA 12 + cuDNN 9. Version mismatch is the most common deployment failure.

**Prevention:**
1. Verify CUDA version before deploying: `nvidia-smi`
2. Verify cuDNN version: `cat /usr/local/cuda/include/cudnn_version.h | grep CUDNN_MAJOR`
3. Use a base Docker image that already pins `cuda:12.x-cudnn9`

**Source:** `platform-beslissingen.md` — GPU Resource Management: faster-whisper on H100

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

## See Also

- [patterns/platform.md](../patterns/platform.md) - Correct platform configuration patterns
- [platform-beslissingen.md](../../../klai-website/docs/platform-beslissingen.md) - Full compatibility review
