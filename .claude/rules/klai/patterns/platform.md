---
paths:
  - "**/litellm*.yml"
  - "**/docker-compose*.yml"
  - "klai-portal/backend/**"
  - "deploy/**"
---
# Platform Patterns

> LiteLLM, LibreChat, vLLM, Zitadel, Caddy — Klai AI stack configuration.
> Based on the compatibility review in `docs/architecture/platform.md`.

## Index
> Keep this index in sync — add a row when adding a pattern below.

| Pattern | When to use | Evidence |
|---|---|---|
| [platform-litellm-tier-model](#platform-litellm-tier-model) | Three-tier alias model: when to use klai-fast / klai-primary / klai-large | `config.py` uses `klai-*` alias, never raw model |
| [platform-litellm-provider-swap](#platform-litellm-provider-swap) | Switching all services between Mistral and Claude | `curl litellm:4000/model/info` shows new provider |
| [platform-litellm-vllm-config](#platform-litellm-vllm-config) | Configuring LiteLLM to route to vLLM instances | `curl localhost:8001/health` returns 200 via LiteLLM |
| [platform-vllm-startup-sequence](#platform-vllm-startup-sequence) | Starting vLLM services on ai-01 | `docker ps` shows all vLLM containers healthy |
| [platform-mongodb-per-tenant](#platform-mongodb-per-tenant) | Provisioning a new customer tenant | `mongosh` shows new DB named after tenant ID |
| [platform-caddy-tenant-routing](#platform-caddy-tenant-routing) | Per-tenant subdomain routing in Caddy | `curl -sI https://<slug>.getklai.com` returns 200 |
| [platform-docker-socket-proxy](#platform-docker-socket-proxy) | Safe Docker socket access from application containers | `docker.from_env().ping()` works via proxy port |
| [platform-zitadel-org-per-tenant](#platform-zitadel-org-per-tenant) | Creating a Zitadel organization for a new tenant | Zitadel API returns new org with matching name |
| [platform-librechat-env-template](#platform-librechat-env-template) | Generating a LibreChat container `.env` file | OIDC login redirects to Zitadel and back |
| [platform-portal-users-mapping-only](#platform-portal-users-mapping-only) | Storing user membership in the portal database | `portal_users` table has no email/name columns |
| [platform-zitadel-user-role-assignment](#platform-zitadel-user-role-assignment) | Assigning a Zitadel project role to a user | Token `roles` claim contains assigned role key |
| [platform-vexa-bot-lifecycle](#platform-vexa-bot-lifecycle) | Debugging a meeting stuck in status | Meeting row transitions to `done` after webhook |
| [platform-hetzner-dns-wildcard-tls](#platform-hetzner-dns-wildcard-tls) | Building Caddy with wildcard TLS for `*.getklai.com` | `curl -sI https://*.getklai.com` shows valid cert |
| [platform-docker-network-testing](#platform-docker-network-testing) | Testing internal services from within the Docker network | `docker exec <ctr> wget -qO- <url>` returns data |
| [platform-db-debug-multi-tenant](#platform-db-debug-multi-tenant) | Debugging multi-tenant auth failures by inspecting org data | SQL query shows matching `zitadel_org_id` values |

---

## platform-litellm-tier-model

**When to use:** Deciding which `klai-*` alias to use in a service, or understanding what each tier maps to

Klai uses three tier aliases in LiteLLM. All service code calls only these — never a raw provider model name.

| Alias | Mistral (default) | Claude (fallback) | Task type |
|---|---|---|---|
| `klai-fast` | `mistral-small-2603` | `claude-haiku-4-5-20251001` | Lightweight, high-volume, latency-sensitive |
| `klai-primary` | `mistral-small-2603` | `claude-sonnet-4-6` | Standard quality, user-facing |
| `klai-large` | `mistral-large-2512` | `claude-sonnet-4-6` | Agentic, tool use, MCP flows |

### Which tier per task

| Task | Tier |
|---|---|
| Coreference / query rewrite | `klai-fast` |
| LLM enrichment (HyPE questions, context prefix) | `klai-fast` |
| Graphiti entity extraction + graph search | `klai-fast` |
| KB chat synthesis (streaming, citations) | `klai-primary` |
| Meeting / transcription summarization | `klai-primary` |
| LibreChat general chat | `klai-primary` (custom_router may upscale to large) |
| MCP tool use / multi-step agentic flows | `klai-large` |

### Why fast and primary map to the same Mistral model

Mistral Small 4 (`mistral-small-2603`) is a 119B MoE with 6.5B active parameters per token — combining low latency with quality that matches older Large-class models. It serves both lightweight tasks (fast tier) and user-facing synthesis (primary tier) at $0.15/$0.60 per M tokens, one-third the cost of Large 3.

The tiers remain separate aliases so each can be independently pointed at a different model — e.g. Haiku vs Sonnet when running Claude, or different vLLM endpoints.

**Mistral Nemo (`open-mistral-nemo`) is retired** — superseded by Small 4 across all fast-tier tasks.

### Hardcoding rule

Never hardcode model names in service files. Always use `settings.X_model` with a default in `config.py`:

```python
# config.py
coreference_model: str = "klai-fast"     # query rewrite
synthesis_model:   str = "klai-primary"  # KB chat answer
graphiti_llm_model: str = "klai-fast"    # entity extraction
enrichment_model:  str = "klai-fast"     # HyPE + context prefix
summarize_model:   str = "klai-primary"  # meeting/transcription

# service file
body = {"model": settings.coreference_model, ...}  # never a literal string
```

**See also:** `model-policy.md` for the full alias table and forbidden model names

---

## platform-litellm-provider-swap

**When to use:** Switching all Klai services from Mistral to Claude (or back), e.g. during quota exhaustion or for demos

Because all services use tier aliases (`klai-fast`, `klai-primary`, `klai-large`), a full provider swap is **3 lines in the LiteLLM config** — no service code changes needed.

### Switch to Claude (Anthropic)

```yaml
# deploy/litellm/config.yaml — model_list section
- model_name: klai-fast
  litellm_params:
    model: anthropic/claude-haiku-4-5-20251001
    api_key: os.environ/ANTHROPIC_API_KEY
  rpm: 50
  tpm: 100000

- model_name: klai-primary
  litellm_params:
    model: anthropic/claude-sonnet-4-6
    api_key: os.environ/ANTHROPIC_API_KEY
  rpm: 50
  tpm: 200000

- model_name: klai-large
  litellm_params:
    model: anthropic/claude-sonnet-4-6
    api_key: os.environ/ANTHROPIC_API_KEY
  rpm: 50
  tpm: 200000
```

Then `docker compose restart litellm` on core-01. All services pick up the new backend automatically.

### Switch back to Mistral

```yaml
- model_name: klai-fast
  litellm_params:
    model: mistral/mistral-small-latest
    api_key: os.environ/MISTRAL_API_KEY
  rpm: 20
  tpm: 45000

- model_name: klai-primary
  litellm_params:
    model: mistral/mistral-small-latest
    api_key: os.environ/MISTRAL_API_KEY
  rpm: 20
  tpm: 45000

- model_name: klai-large
  litellm_params:
    model: mistral/mistral-large-latest
    api_key: os.environ/MISTRAL_API_KEY
  rpm: 20
  tpm: 45000
```

### Rate limit notes

- Mistral org limit: 60 RPM shared across all models (20 RPM per alias)
- Anthropic: 50 RPM per model, much more headroom
- The `enforce_model_rate_limits` callback in `router_settings` must be present for rate limiting to queue rather than fail

**See also:** `platform-litellm-tier-model` for which alias to use per task

---

## platform-litellm-vllm-config

**When to use:** Configuring LiteLLM to route to vLLM instances on core-01 or ai-01

Full working LiteLLM config for the dual-model Qwen3 setup:

```yaml
model_list:
  - model_name: klai-complex
    litellm_params:
      model: hosted_vllm/qwen3-32b     # Must use hosted_vllm/ prefix
      api_base: http://localhost:8001/v1
      api_key: "none"

  - model_name: klai-fast
    litellm_params:
      model: hosted_vllm/qwen3-8b
      api_base: http://localhost:8002/v1
      api_key: "none"

litellm_settings:
  drop_params: true                     # Required: vLLM rejects unknown OpenAI params

router_settings:
  routing_strategy: complexity-based-routing
  model_group_alias:
    default: klai-complex
```

**Key rules:**
- Always `hosted_vllm/` prefix, never `openai/`
- Always `drop_params: true`
- Verify Complexity Router is available in your LiteLLM OSS version (`>= 1.74.9`)

**See also:** `pitfalls/platform.md#platform-litellm-vllm-provider-prefix`

---

## platform-vllm-startup-sequence

**When to use:** Starting vLLM services on ai-01

Sequential startup order with health checks. Never start in parallel.

```yaml
# docker-compose.yml (ai-01)
services:
  vllm-32b:
    image: vllm/vllm-openai:latest
    command: >
      --model Qwen/Qwen3-32B
      --gpu-memory-utilization 0.55
      --port 8001
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 30s
      timeout: 10s
      retries: 10

  vllm-8b:
    image: vllm/vllm-openai:latest
    command: >
      --model Qwen/Qwen3-8B
      --gpu-memory-utilization 0.40
      --port 8002
      --enforce-eager            # Required with MPS
    depends_on:
      vllm-32b:
        condition: service_healthy  # Wait for 32B before starting 8B

  whisper:
    image: faster-whisper:latest
    depends_on:
      vllm-8b:
        condition: service_healthy  # Start last
```

**See also:** `pitfalls/platform.md#platform-vllm-sequential-startup`

---

## platform-mongodb-per-tenant

**When to use:** Provisioning a new customer tenant

One MongoDB database per tenant on a shared MongoDB server. Database-level isolation without a MongoDB container per customer.

```python
# provisioning_service.py

def provision_tenant(tenant_id: str) -> dict:
    """Create a new tenant's resources."""
    # MongoDB: separate database via MONGO_URI
    mongo_uri = f"mongodb://mongo/{tenant_id}"  # e.g. mongodb://mongo/tenant_abc

    # LibreChat .env per tenant
    env = {
        "MONGO_URI": mongo_uri,
        "MEILI_HOST": "http://meilisearch:7700",  # Shared Meilisearch
        "OPENID_ISSUER": f"https://auth.getklai.com/oidc",
        "OPENID_CLIENT_ID": f"librechat-{tenant_id}",
        "OPENID_USERNAME_CLAIM": "preferred_username",  # Required
        "OPENID_REUSE_TOKENS": "false",                 # Never true
    }
    return env
```

**Rule:** MONGO_URI database name = tenant identifier. No shared databases between tenants.

---

## platform-caddy-tenant-routing

**When to use:** Handling per-tenant subdomain routing on core-01

Caddy uses one wildcard block (`*.getklai.com`) for known services plus per-tenant blocks that are appended at provisioning time. The portal-api (not a separate dispatcher) writes new blocks and restarts Caddy.

```
# Caddyfile: one wildcard block handles all known routes
*.{$DOMAIN} {
    tls { dns hetzner {$HETZNER_AUTH_API_TOKEN} propagation_delay 120s }

    # Specific services via named matchers
    @auth host auth.{$DOMAIN}
    handle @auth { reverse_proxy zitadel:8080 }

    @chat host chat.{$DOMAIN}
    handle @chat { reverse_proxy librechat-klai:3080 }

    # Portal API — all subdomains, /api/* prefix
    handle /api/* { reverse_proxy portal-api:8010 }

    # Portal SPA — wildcard fallback
    handle { root * /srv/portal; try_files {path} /index.html; file_server }
}

# Per-tenant blocks appended by portal-api at provisioning:
chat.{slug}.{$DOMAIN} {
    tls { dns hetzner {$HETZNER_AUTH_API_TOKEN} propagation_delay 120s }
    reverse_proxy librechat-{slug}:3080
}
```

**Provisioning flow:**
1. portal-api acquires `_caddy_lock` (asyncio.Lock)
2. Appends new block to `./caddy/Caddyfile` (bind-mounted)
3. Restarts Caddy container via Docker SDK (Admin API is disabled: `admin off`)
4. Releases lock

**See also:** `pitfalls/platform.md#platform-caddy-admin-off-reload`

---

## platform-docker-socket-proxy

**When to use:** A service needs to manage Docker containers (provisioning, monitoring) but should not have full Docker daemon access

Mount a `docker-socket-proxy` (Tecnativa) instead of `/var/run/docker.sock` directly. This limits the service to only the Docker API calls it actually needs.

```yaml
# docker-compose.yml
services:
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy:0.3.0
    restart: unless-stopped
    environment:
      CONTAINERS: 1   # allow GET/POST on containers
      NETWORKS: 1     # allow listing + connecting networks
      POST: 1         # allow POST (start, stop, restart, create)
      DELETE: 1       # allow DELETE (remove containers)
      # Everything else defaults to 0 (denied)
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - socket-proxy  # internal network — only portal-api can reach it

  portal-api:
    environment:
      DOCKER_HOST: tcp://docker-socket-proxy:2375  # no direct socket mount
    networks:
      - klai-net
      - net-postgres
      - socket-proxy
```

**Python SDK usage:**
```python
import docker
# DOCKER_HOST env var is automatically read by docker.from_env()
client = docker.from_env()
container = client.containers.get("my-container")
container.restart(timeout=10)
```

**Rule:** Always use docker-socket-proxy for application containers. Never mount `/var/run/docker.sock` directly — it gives root-equivalent access to the host.

**See also:** `pitfalls/platform.md#platform-caddy-admin-off-reload`

---

## platform-zitadel-org-per-tenant

**When to use:** Provisioning a new customer in Zitadel

One Zitadel Organization per customer is the canonical approach. Use the Zitadel Management API.

```python
# zitadel_provisioning.py
import httpx

async def create_tenant_org(tenant_name: str, domain: str) -> dict:
    """Create Zitadel Organization for a new tenant."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{ZITADEL_BASE}/v2/organizations",
            headers={"Authorization": f"Bearer {SERVICE_ACCOUNT_TOKEN}"},
            json={
                "name": tenant_name,
                "primaryDomain": domain,
            }
        )
        response.raise_for_status()
        return response.json()
```

**Rule:** Org ID is the primary tenant identifier in Zitadel. Store it in PostgreSQL alongside the LibreChat container name and MongoDB database name.

---

## platform-librechat-env-template

**When to use:** Generating a new LibreChat container `.env` file at provisioning

Required OIDC settings that must be in every LibreChat tenant `.env`:

```bash
# Authentication — required settings (never deviate from these)
OPENID_ISSUER=https://auth.getklai.com/oidc
OPENID_CLIENT_ID=librechat-{{ tenant_id }}
OPENID_CLIENT_SECRET={{ generated_secret }}
OPENID_SCOPE=openid profile email
OPENID_CALLBACK_URL=https://{{ tenant_id }}.getklai.com/oauth/openid/callback
OPENID_USERNAME_CLAIM=preferred_username    # Required — default is given_name (wrong)
OPENID_REUSE_TOKENS=false                  # Never true on non-fresh deployment

# Database — tenant-isolated
MONGO_URI=mongodb://mongo/{{ tenant_id }}

# Search — shared instance, safe because userId is globally unique
MEILI_HOST=http://meilisearch:7700
MEILI_MASTER_KEY={{ shared_meili_key }}

# AI routing — via LiteLLM
OPENAI_API_KEY={{ litellm_virtual_key }}
OPENAI_REVERSE_PROXY=http://litellm:4000/v1
```

**See also:** `pitfalls/platform.md#platform-librechat-oidc-reuse-tokens`

---

## platform-portal-users-mapping-only

**When to use:** Storing user membership in the portal database

`portal_users` is a **pure mapping table** — it maps `zitadel_user_id` to `org_id` and records when the user joined. Identity details (email, first name, last name) are always fetched live from Zitadel, never stored in PostgreSQL.

```python
# portal.py — correct model
class PortalUser(Base):
    __tablename__ = "portal_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    zitadel_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("portal_orgs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    org: Mapped["PortalOrg"] = relationship(back_populates="users")
    # No email, first_name, last_name — those live in Zitadel
```

**Listing users with live identity (admin.py pattern):**
```python
# 1. Get portal membership records (gives us org_id + created_at)
portal_users = {u.zitadel_user_id: u for u in db_users}

# 2. Fetch live identity from Zitadel
zitadel_users = await zitadel.list_org_users(org.zitadel_org_id)

# 3. Merge — only return users that are in our portal
for z in zitadel_users:
    uid = z.get("id", "")
    if uid not in portal_users:
        continue  # skip service accounts or users not in our portal
    profile = z.get("human", {}).get("profile", {})
    email_obj = z.get("human", {}).get("email", {})
    users_out.append(UserOut(
        zitadel_user_id=uid,
        email=email_obj.get("email", ""),
        first_name=profile.get("firstName", ""),
        last_name=profile.get("lastName", ""),
        created_at=portal_users[uid].created_at,
    ))
```

**Why mapping-only:**
- No drift: identity data cannot go stale (name/email changes in Zitadel are immediately reflected)
- Single source of truth: Zitadel owns identity, portal owns membership
- Simpler: no sync job, no update-on-login logic needed

**See also:** `pitfalls/platform.md#platform-zitadel-resourceowner-claim-unreliable`

---

## platform-zitadel-user-role-assignment

**When to use:** Assigning a Zitadel project role to a user (e.g. at signup or invite)

After creating a user in Zitadel, you must assign a role via a **user grant** for the role to appear in their token. The role must already exist on the Zitadel project.

```python
# zitadel.py
async def grant_user_role(self, org_id: str, user_id: str, role: str) -> None:
    """Assign a project role to a specific user (user grant)."""
    resp = await self._http.post(
        f"/management/v1/users/{user_id}/grants",
        headers={"x-zitadel-orgid": org_id},
        json={
            "projectId": settings.zitadel_project_id,
            "roleKeys": [role],
        },
    )
    resp.raise_for_status()
```

**Usage in signup flow:**
```python
# After creating user in Zitadel:
await zitadel.grant_user_role(
    org_id=zitadel_org_id,
    user_id=zitadel_user_id,
    role="org:owner",
)
```

**Prerequisites (one-time Zitadel setup):**
1. Role must be defined on the project (e.g. `org:owner`, `org:admin`)
2. Project must have "Return user roles during authentication" enabled
3. Role appears in token as `urn:zitadel:iam:org:project:roles`

**See also:** `pitfalls/platform.md#platform-zitadel-project-grant-vs-user-grant`

---

## platform-vexa-bot-lifecycle

**When to use:** Debugging a meeting stuck in "Recording", "Joining", or "Processing" — or understanding how a meeting transitions to "Done"

The Vexa bot has two resolution paths. Knowing which path applies determines where to look when things go wrong.

**Bot lifecycle (happy path):**

```
Bot joins meeting
  → status: joining
  → status: active (first participant seen)

User leaves / everyone leaves
  → everyoneLeftTimeout (5s) fires
  → bot stops itself
  → Vexa fires `completed` webhook → portal-api run_transcription
  → status: done

OR: user clicks Stop in portal
  → portal-api calls stop_bot API
  → Vexa fires `completed` webhook → portal-api run_transcription
  → status: done

Fallback: bot_poller polls every 10s
  → detects bot is gone from Vexa
  → triggers run_transcription
  → status: done
```

**Key lifecycle config (server-side, not in Git):**

File: `/opt/klai/vexa-patches/process.py` on core-01
Mounted as volume override into `vexa-bot-manager` container.

```python
# Confirmed working values (March 2026):
everyoneLeftTimeout = 5000     # ms — was 60000
noOneJoinedTimeout  = 30000    # ms — was 120000
waitingRoomTimeout  = 60000    # ms — was 300000
```

**Always read this file first when debugging:**
```bash
ssh core-01 cat /opt/klai/vexa-patches/process.py
```

**State machine for portal status field:**

| Status | Meaning |
|--------|---------|
| `pending` | Meeting created, bot not yet dispatched |
| `joining` | Bot dispatched, waiting to enter meeting |
| `active` | Bot in meeting, recording |
| `recording` | everyoneLeft timeout counting down (≤5s) |
| `processing` | Stop called, waiting for Vexa webhook |
| `done` | Transcription complete |
| `failed` | Error in transcription or bot |

**Rule:** The `completed` webhook from Vexa is the primary trigger for `run_transcription`. The bot_poller is a fallback only — do not treat it as equivalent.

**See also:**
- `pitfalls/platform.md#platform-vexa-timeout-looks-like-bug`
- `pitfalls/platform.md#platform-vexa-guard-breaks-stop-flow`
- `pitfalls/platform.md#platform-vexa-debug-wrong-layer`

---

## platform-hetzner-dns-wildcard-tls

**When to use:** Building Caddy for wildcard TLS on `*.getklai.com`

Requires a custom Caddy build with the Hetzner DNS plugin:

```bash
# Build Caddy with Hetzner DNS plugin
xcaddy build --with github.com/caddy-dns/hetzner

# Or use a pre-built Docker image
FROM caddy:builder AS builder
RUN xcaddy build --with github.com/caddy-dns/hetzner

FROM caddy:latest
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
```

**Environment variable required:**
```bash
HETZNER_DNS_TOKEN=your_hetzner_dns_api_token
```

**DNS provider:** Hetzner DNS (free, already a customer, GDPR-compliant, EU).
**Do not use Cloud86** — no Caddy plugin available.

**Source:** `architecture/platform.md` — Per-Tenant Routing: Wildcard TLS

---

## platform-docker-network-testing

**When to use:** Testing internal services that are not exposed externally (e.g. docs-app, knowledge-ingest, internal APIs behind reverse proxy)

Use `docker exec` to test from within the Docker network. Check which HTTP client is available in the container first — Alpine-based images have `wget` but not `curl`.

### Step 1: Find the available tool

```bash
# Check what's available in the container
ssh core-01 "docker exec klai-core-docs-app-1 which curl wget"
```

### Step 2: Test the endpoint

```bash
# Alpine containers (wget) — docs-app, Next.js, Node-based
ssh core-01 "docker exec klai-core-docs-app-1 wget -qO- \
  'http://localhost:3000/docs/api/orgs/myorg/kbs' \
  --header='X-Internal-Secret: <secret>' \
  --header='X-Org-ID: <zitadel-org-id>'"

# Debian/Ubuntu containers (curl) — Python, FastAPI
ssh core-01 "docker exec klai-core-portal-api-1 curl -s \
  'http://localhost:8010/api/health'"
```

### When to use this vs external testing

| Scenario | Method |
|---|---|
| Service has no external port | `docker exec` (only option) |
| Testing internal API with `X-Internal-Secret` | `docker exec` (secret not routed externally) |
| Testing public endpoint | `curl` from anywhere |
| Testing service-to-service communication | `docker exec` from the calling container |

**Rule:** Don't flail with multiple approaches. Check `which curl wget` first, then use the right tool for the container's base image.

---

## platform-db-debug-multi-tenant

**When to use:** Debugging org-level auth failures (403s), data visibility issues, or any multi-tenant problem where the wrong org sees wrong data

When org-level authentication fails, always check the `organizations` table for data mismatches first. The stored org identifier must match what the auth layer sends.

### Quick diagnostic queries

```bash
# klai-docs: check org identifiers
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai_docs -c \
  \"SELECT slug, zitadel_org_id, created_at FROM docs.organizations\""

# portal: check org identifiers
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai -c \
  \"SELECT id, slug, zitadel_org_id FROM portal_orgs\""

# Compare stored ID with what the auth header sends
# The zitadel_org_id in the DB must match the X-Org-ID header value
```

### Common data mismatches

| Symptom | Likely cause | Fix |
|---|---|---|
| 403 after adding org verification | Auto-provisioned org stored slug as `zitadel_org_id` | `UPDATE ... SET zitadel_org_id = '<real-id>'` |
| User sees empty data for their org | Org exists in auth but not in service DB | Check auto-provisioning code path |
| User sees another org's data | Missing org scope in query | Add org_id filter (see `pitfalls/security.md`) |

### Fix mismatched org data

```bash
# Correct a wrong zitadel_org_id (after verifying the real ID)
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai_docs -c \
  \"UPDATE docs.organizations SET zitadel_org_id = '<real-zitadel-org-id>' WHERE slug = '<org-slug>'\""
```

**Rule:** Always inspect the DB before theorizing. A 5-second query saves hours of code debugging.

**See also:** `pitfalls/security.md#security-idor-url-org-slug-trusted`, `pitfalls/docs-app.md#platform-docs-app-auto-provision-org-id`

---
