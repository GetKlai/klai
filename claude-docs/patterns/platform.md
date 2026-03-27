# Platform Patterns

> LiteLLM, LibreChat, vLLM, Zitadel, Caddy — Klai AI stack configuration.
> Based on the compatibility review in `klai-claude/docs/architecture/platform.md`.

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
