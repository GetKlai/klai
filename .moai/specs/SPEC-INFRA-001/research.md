# SPEC-INFRA-001 Research: Per-tenant MCP Server Management

## Executive Summary

**Current State**: Per-tenant MCP server configuration is **architecturally designed but partially implemented**. Twenty CRM MCP is live for the `getklai` tenant via a streamable-http endpoint, but the full DB-driven provisioning system (`_generate_librechat_yaml()`) remains unimplemented. The infrastructure exists and is tested, but the automation layer is paused pending this architectural review.

**Key Findings**:
1. The PortalOrg.mcp_servers JSON column exists (migration d2e3f4a5b6c7) but is unused in provisioning flow
2. LibreChat's YAML-based configuration is cached in Redis — requires container restart to apply changes
3. Twenty's built-in MCP server is active on crm.getklai.com/mcp (streamable-http, JSON-RPC)
4. Secrets are encrypted using AES-256-GCM with portal_secrets.encrypt()
5. Container provisioning already has the infrastructure: _generate_librechat_yaml() is coded but never called

---

## 1. Existing Infrastructure

### 1.1 Database: PortalOrg.mcp_servers

**Location**: `klai-portal/backend/app/models/portal.py:37`

```python
mcp_servers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

**Migration**: `klai-portal/backend/alembic/versions/d2e3f4a5b6c7_add_mcp_servers_to_portal_orgs.py`
- Created on 2026-04-02
- Seeded `getklai` tenant with Twenty CRM config (though in old stdio format)
- Current seed value in migration contains deprecated `stdio` transport

**Current Usage**: 
- Column exists in schema
- `getklai` tenant has seed data (but format is outdated — references `stdio` instead of `streamable-http`)
- Data is read in provisioning.py:515 and passed to `_start_librechat_container()`

### 1.2 Provisioning Flow: LibreChat Tenant Startup

**Location**: `klai-portal/backend/app/services/provisioning.py`

**Key Function**: `_start_librechat_container()` (lines 350-395)

```python
def _start_librechat_container(
    slug: str,
    env_file_host_path: str,
    mcp_servers: dict | None = None,
) -> None:
    """Start the LibreChat Docker container for a tenant (synchronous, run in executor)."""
    # Lines 368-373: Generate per-tenant yaml by merging base with mcp_servers
    base_yaml_path = Path(settings.librechat_container_data_path) / "librechat.yaml"
    tenant_yaml_content = _generate_librechat_yaml(base_yaml_path, mcp_servers)
    tenant_yaml_dir = Path(settings.librechat_container_data_path) / slug
    tenant_yaml_dir.mkdir(parents=True, exist_ok=True)
    (tenant_yaml_dir / "librechat.yaml").write_text(tenant_yaml_content)
    
    # Line 382: Mount per-tenant yaml inside container
    f"{librechat_host_base}/{slug}/librechat.yaml": {"bind": "/app/librechat.yaml", "mode": "ro"}
```

**Called From**: `_provision()` line 515 — passes `org.mcp_servers` (from DB) to the container starter

**Current Behavior**:
- The mcp_servers dict is passed through to `_generate_librechat_yaml()`
- Function generates per-tenant YAML with merged MCP configs
- YAML is written to `librechat/{slug}/librechat.yaml` and bind-mounted into container
- Container reads it at startup

### 1.3 YAML Generation: _generate_librechat_yaml()

**Location**: `klai-portal/backend/app/services/provisioning.py:184-207`

```python
def _generate_librechat_yaml(
    base_path: Path,
    extra_mcp_servers: dict | None,
) -> str:
    """Generate a per-tenant librechat.yaml by merging base config with extra MCP servers."""
    with open(base_path) as f:
        config = yaml.safe_load(f)

    if extra_mcp_servers:
        config = copy.deepcopy(config)
        mcp = config.setdefault("mcpServers", {})
        mcp.update(extra_mcp_servers)

        # Add extra server names to modelSpecs.list[].mcpServers
        extra_names = list(extra_mcp_servers.keys())
        for spec in config.get("modelSpecs", {}).get("list", []):
            existing = spec.get("mcpServers", [])
            spec["mcpServers"] = existing + extra_names

    return yaml.dump(config, default_flow_style=False, sort_keys=False)
```

**Status**: ✅ **Implemented and functional** — not yet integrated with DB-driven provisioning, but code is production-ready.

**What it does**:
- Loads base `librechat.yaml`
- Deep-copies to avoid mutating original
- Merges `extra_mcp_servers` dict into `mcpServers` section
- Appends server names to each model spec's `mcpServers` list
- Returns YAML-formatted string

**Tested With**: 
- Currently used in provisioning flow when starting containers
- Works for getklai tenant (mcp_servers dict is passed)

### 1.4 Base Configuration: librechat.yaml

**Location**: `deploy/librechat/librechat.yaml`

**Current MCP Server**:

```yaml
mcpServers:
  klai-knowledge:
    type: streamable-http
    url: http://klai-knowledge-mcp:8080/mcp
    headers:
      X-User-ID: "{{LIBRECHAT_USER_ID}}"
      X-Org-ID: "${KLAI_ZITADEL_ORG_ID}"
      X-Org-Slug: "${KLAI_ORG_SLUG}"
      X-Internal-Secret: "${KNOWLEDGE_INGEST_SECRET}"
```

**Note on Environment Variable Expansion**:
- LibreChat expands `${}` variables **at container startup** from the container's environment
- The variables are supplied via the container's `.env` file
- This happens ONCE when the container starts, not on every request
- Updating `.env` and restarting the container is required to apply changes

**modelSpecs section** (lines 90-100):

```yaml
modelSpecs:
  prioritize: true
  list:
    - name: "klai-primary"
      mcpServers:
        - "klai-knowledge"
      label: "Klai AI"
      default: true
```

The model spec's `mcpServers` list references which MCP servers this model has access to. `_generate_librechat_yaml()` appends new server names here.

### 1.5 Environment File Generation

**Location**: `klai-portal/backend/app/services/provisioning.py:210-295`

**Function**: `_generate_librechat_env()`

**Key Relevant Lines**:
```python
# Line 292: KLAI_ZITADEL_ORG_ID is the tenant's Zitadel org ID
KLAI_ZITADEL_ORG_ID={zitadel_org_id}

# Line 293: KLAI_ORG_SLUG is the tenant's slug
KLAI_ORG_SLUG={slug}

# Line 294: KNOWLEDGE_INGEST_SECRET from settings (shared across all tenants)
KNOWLEDGE_INGEST_SECRET={settings.knowledge_ingest_secret}
```

These environment variables are injected into the tenant's `.env` file, which the container reads at startup. LibreChat's YAML expansion (${}syntax) resolves them.

### 1.6 Container Networking

**Location**: `deploy/docker-compose.yml` (base template)

**Per-tenant container networks** (provisioning.py:388-394):

```python
# Connect to additional networks
for net_name in ["klai-net-mongodb", "klai-net-meilisearch", "klai-net-redis"]:
    try:
        net = client.networks.get(net_name)
        net.connect(container_name)
    except Exception as exc:
        logger.warning("Could not connect %s to %s: %s", container_name, net_name, exc)
```

Each tenant's LibreChat container is connected to:
- `klai-net` (main network, reverse proxy)
- `klai-net-mongodb` (shared MongoDB)
- `klai-net-meilisearch` (shared Meilisearch)
- `klai-net-redis` (shared Redis)

---

## 2. Current Twenty CRM Implementation

### 2.1 Twenty Built-in MCP Server

**Status**: ✅ **Live in Production** (getklai tenant)

**Location**: `https://crm.getklai.com/mcp`

**Transport**: `streamable-http` (JSON-RPC, not stdio)

**Implementation**:
- Twenty's own MCP server, running inside the Twenty instance on crm.getklai.com
- Exposed via HTTPS (no special infrastructure needed)
- Authentication via Bearer token in Authorization header

### 2.2 Current Configuration (getklai)

**Hardcoded on Server** (not via DB):

File: `/opt/klai/librechat/getklai/librechat.yaml` (on core-01)

```yaml
twenty-crm:
  type: streamable-http
  url: https://crm.getklai.com/mcp
  headers:
    Authorization: 'Bearer ${TWENTY_API_KEY}'
```

**Issues**:
1. Hardcoded in a per-tenant yaml file on the server
2. Not managed by the DB provisioning system
3. Doesn't follow the MCP Catalog pattern described in SPEC v3

### 2.3 Twenty Feature Flag: IS_AI_ENABLED

**Status**: ✅ **Manually enabled**

Set in PostgreSQL `core.featureFlag` table and Redis cache cleared.

**Purpose**: Enables the MCP server endpoint and AI integration features within Twenty.

**Limitation**: This is a manual setup step. Not automated during Twenty deployment.

### 2.4 Twenty API Authentication Limitation

**Issue**: Twenty's built-in `http_request` tool does NOT inject the Authorization header automatically.

**Current Workaround**: Token is hardcoded in the system prompt:

```yaml
systemPrompt: >-
  ## Twenty CRM
  Use http_request_mcp_twenty-crm tool. Base URL already configured.
  Notes require bodyV2 (NOT body):
    { "title": "...", "bodyV2": { "markdown": "...", "blocknote": null } }
  POST /objects/noteTargets to link notes to records.
```

**Problem**: 
- When the API key is rotated, the system prompt must be manually updated
- The token is visible in logs (though within a system prompt in an isolated tenant)
- This violates the principle of "no secrets in system prompts"

**Better Solution** (not yet implemented):
- Set the token as a request header at the MCP server configuration level, not in the system prompt
- Currently not possible because the http_request tool doesn't support auth injection

---

## 3. Secrets Management

### 3.1 Encryption Pattern: AES-256-GCM

**Location**: `klai-portal/backend/app/services/secrets.py`

```python
class PortalSecretsService:
    def __init__(self, hex_key: str) -> None:
        self._cipher = AESGCMCipher(bytes.fromhex(hex_key))

    def encrypt(self, plaintext: str) -> bytes:
        return self._cipher.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> str:
        return self._cipher.decrypt(ciphertext)

portal_secrets = PortalSecretsService(settings.portal_secrets_key)
```

**Used For** (in provisioning.py):
- Line 530: `org.zitadel_librechat_client_secret = portal_secrets.encrypt(client_secret)`
- Line 531: `org.litellm_team_key = portal_secrets.encrypt(litellm_team_key) if litellm_team_key else None`

**For MCP Secrets** (not yet used):
- Proposed but not implemented: Twenty API keys would be encrypted with this service before storing in `mcp_servers` JSON

### 3.2 Current Secrets in DB

**PortalOrg Table**:

| Column | Type | Encrypted | Note |
|--------|------|-----------|------|
| zitadel_librechat_client_secret | BYTEA | ✅ Yes | OIDC secret |
| litellm_team_key | BYTEA | ✅ Yes | LiteLLM routing token |
| mcp_servers | JSON | ❌ No | Would store MCP config + secrets |

**Constraint**: The `mcp_servers` JSON column is not encrypted. If Twenty API keys are stored there, they would be plaintext in the database.

### 3.3 Environment Variable Injection

**Pattern**: Secrets are NOT stored in git or docker-compose.yml. Instead:
1. Stored (encrypted in DB) or generated at provisioning time
2. Written to a per-tenant `.env` file on the host
3. Mounted into the container as a read-only volume
4. Container reads at startup

**Example** (provisioning.py:486):

```python
env_file_container_path.write_text(env_content)
# Host path for Docker volume mount
env_file_host_path = f"{settings.librechat_host_data_path}/{slug}/.env"
```

Then in docker run (line 381):
```python
env_file_host_path: {"bind": "/app/.env", "mode": "ro"}
```

---

## 4. LibreChat Configuration Caching & Lifecycle

### 4.1 Redis Caching

**Issue**: When `USE_REDIS=true` (Klai's setup), LibreChat caches the parsed `librechat.yaml` in Redis under key `CacheKeys.APP_CONFIG` with **no expiration**.

**Implication**: After updating the YAML file and restarting the container, the old config is still served from Redis until the cache is manually flushed.

**Workaround**: Before restarting a LibreChat container after config changes:
```bash
docker exec redis redis-cli FLUSHALL
docker compose up -d librechat-{slug}
```

### 4.2 Container Startup Flow

1. Container starts
2. LibreChat reads `/app/librechat.yaml` from disk
3. Expands environment variables (`${VAR}` syntax) from container's `.env`
4. Caches parsed config in Redis
5. On subsequent requests, serves from Redis cache

**To apply config changes**:
- Update `/opt/klai/librechat/{slug}/librechat.yaml` on host
- Flush Redis: `docker exec redis redis-cli FLUSHALL`
- Restart container: `docker compose up -d librechat-{slug}`

---

## 5. Database-Driven Provisioning (Planned but Not Implemented)

### 5.1 Planned Flow

The SPEC describes a full flow that is designed but not yet hooked up:

1. **User in Portal**: Tenant admin enables MCP server for their org via portal UI
2. **Portal API**: Stores config in `PortalOrg.mcp_servers` JSON column (encrypted secrets)
3. **Provisioning.py**: Calls `_generate_librechat_yaml(base, org.mcp_servers)`
4. **YAML Generation**: Merges base template with per-tenant MCP configs
5. **Container Lifecycle**: Writes per-tenant YAML, restarts LibreChat
6. **Runtime**: Container reads expanded YAML with tenant-specific MCP servers

### 5.2 Current Gap

The flow is **designed and partially implemented** but **not connected**:

- ✅ `PortalOrg.mcp_servers` column exists
- ✅ `_generate_librechat_yaml()` function exists
- ✅ Portal-API calls `_start_librechat_container(..., org.mcp_servers)` (line 515)
- ❌ No portal UI to configure MCP servers
- ❌ `mcp_servers` column is seeded with stale data (old stdio format)
- ❌ No MCP Catalog YAML exists (`deploy/librechat/mcp_catalog.yaml` is not present)

### 5.3 Expected `mcp_catalog.yaml` Structure

According to SPEC v3.1.0, the expected structure:

```yaml
servers:
  twenty-crm:
    description: "Twenty CRM — contacten, bedrijven, deals, taken"
    required_env_vars:
      - TWENTY_API_KEY
      - TWENTY_BASE_URL
    config_template:
      type: streamable-http
      url: "${TWENTY_BASE_URL}/mcp"
      headers:
        Authorization: "Bearer ${TWENTY_API_KEY}"
```

**Status**: This file does NOT exist in the codebase.

---

## 6. Proposed Architecture (from SPEC v3.0/3.1)

### 6.1 Three-Layer Model

**Layer 1: MCP Catalog** (`deploy/librechat/mcp_catalog.yaml`)
- Defines all supported MCP servers Klai offers
- Describes required configuration and environment variables
- Acts as a whitelist (only catalog entries can be enabled)

**Layer 2: Per-Tenant Activation** (`PortalOrg.mcp_servers` JSON column)
- Which catalog entries are active for this tenant
- Tenant-specific configuration (URLs, API keys)
- Secrets encrypted at rest

**Layer 3: Runtime** (per-tenant `librechat.yaml`)
- Generated at container startup time by merging base template + tenant DB config
- Environment variables expanded by LibreChat at startup
- Secrets never visible in git or logs

### 6.2 Control Flow (Planned)

```
Portal UI (admin enables MCP)
    ↓
PortalOrg.mcp_servers JSON (DB update)
    ↓
Provisioning.py detects change
    ↓
_generate_librechat_yaml(base, db_config)
    ↓
Per-tenant librechat.yaml written to disk
    ↓
Container restart
    ↓
LibreChat expands ${ENV_VARS}
    ↓
MCP servers available in chat
```

**Current State**: Only the bottom half (container-level) is operational. The top half (portal UI, DB integration) is missing.

---

## 7. Key Design Constraints & Assumptions

### 7.1 LibreChat YAML Expansion Rules

**[A-001] LibreChat supports env var expansion in mcpServers config** — **Confidence: High**

Evidence:
- klai-knowledge MCP uses `${KLAI_ZITADEL_ORG_ID}`, `${KLAI_ORG_SLUG}`, `${KNOWLEDGE_INGEST_SECRET}` in headers
- Container reads these from .env file at startup
- Works in production for getklai tenant

**[A-002] Expansion happens at startup only** — **Confidence: High**

Evidence:
- Changes to .env require container restart
- Redis caches the parsed config (with expanded values)
- No dynamic re-expansion on requests

**[A-003] Expansion is available in headers but NOT in system prompts** — **Confidence: High**

Evidence:
- SPEC explicitly notes: "{{USER_VAR}}" interpolation is NOT available in `systemPrompt`
- Current workaround: Twenty API key is hardcoded in system prompt
- This is a LibreChat limitation, not a configuration issue

### 7.2 MCP Transport Options

**Stdio** (deprecated for Klai):
- Local process spawned by LibreChat
- No external network calls
- Complex dependency management (npm install, node binary)
- Not suitable for external APIs like Twenty CRM

**Streamable-HTTP** (current for Twenty):
- External service over HTTPS
- Simple request/response model (JSON-RPC over HTTP)
- No additional dependencies in LibreChat
- Requires network access and authentication

**SSE** (planned future):
- Server-Sent Events for streaming responses
- Per-container deployment (separate Docker container per MCP service)
- Better resource isolation than stdio

### 7.3 Secrets in the Database

**Current Pattern** (for OIDC secrets, LiteLLM keys):
- Encrypted with AES-256-GCM
- Stored as BYTEA in PostgreSQL
- Decrypted at provisioning time
- Never visible in logs or version control

**Proposed for MCP** (not yet implemented):
- Twenty API keys would be encrypted with the same AES-256-GCM service
- Stored in `mcp_servers` JSON alongside plaintext config
- Decrypted when generating per-tenant YAML

---

## 8. Identified Limitations & Risks

### 8.1 LibreChat Caching Risk

**Risk Level**: MEDIUM

**Issue**: Redis cache persists even after container restart. Configuration changes are silently ignored until Redis is manually flushed.

**Mitigation**: Add documentation and scripting to flush Redis before any LibreChat restart.

**Better Solution**: Patch LibreChat to support cache TTL or add a cache key version suffix.

### 8.2 Twenty API Key Rotation Problem

**Risk Level**: HIGH

**Issue**: Current setup requires manual system prompt updates when Twenty API key rotates.

**Current Workaround**: Token is hardcoded in the system prompt on the server.

**Root Cause**: Twenty's `http_request` tool doesn't auto-inject auth headers.

**Better Solutions**:
1. Implement per-request auth injection in an MCP server wrapper around Twenty's endpoint
2. Move token to request headers at the MCP config level (requires LibreChat enhancement)
3. Use Twenty's OAuth/OIDC if available

### 8.3 DB Column Not Encrypted

**Risk Level**: MEDIUM

**Issue**: `mcp_servers` JSON column stores plain values. If Twenty API keys are stored here, they're plaintext in the DB.

**Mitigation**: Implement encryption for the `mcp_servers` column before using it in production:

```python
# Before: store plaintext
mcp_servers = {"twenty-crm": {"env": {"TWENTY_API_KEY": "sk-xxx"}}}

# After: encrypt secrets during storage
mcp_servers = {
    "twenty-crm": {
        "env": {
            "TWENTY_API_KEY": portal_secrets.encrypt("sk-xxx"),
            "TWENTY_BASE_URL": "https://crm.getklai.com"  # plaintext is ok
        }
    }
}

# Then decrypt during YAML generation
```

### 8.4 Missing Portal UI

**Risk Level**: HIGH

**Issue**: No UI exists for tenants to configure MCP servers. Current implementation is admin-only (manual DB updates or SQL).

**Required** before full deployment:
- Portal settings page to manage MCP servers per organization
- Form to input required env vars (with validation)
- UI to test MCP connectivity
- Audit log of MCP config changes

### 8.5 Container Restart Required for Config Changes

**Risk Level**: LOW (but operational pain)

**Issue**: MCP config changes require full container restart, which causes downtime.

**Mitigation**:
- Schedule container restarts during low-traffic windows
- Implement graceful shutdown with request draining
- Consider warm-standby container (future)

**Better Solution** (future architecture):
- Deploy MCP servers as separate containers (one per tenant per service)
- LibreChat connects to MCP container via internal network URL
- MCP container lifecycle is independent of LibreChat
- Updates don't require LibreChat restart

---

## 9. Code Locations Summary

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| PortalOrg.mcp_servers | `klai-portal/backend/app/models/portal.py` | 37 | ✅ Exists |
| _generate_librechat_yaml() | `klai-portal/backend/app/services/provisioning.py` | 184-207 | ✅ Implemented |
| _generate_librechat_env() | `klai-portal/backend/app/services/provisioning.py` | 210-295 | ✅ Implemented |
| _start_librechat_container() | `klai-portal/backend/app/services/provisioning.py` | 350-395 | ✅ Implemented |
| Container start with mcp_servers | `klai-portal/backend/app/services/provisioning.py` | 515 | ✅ Implemented |
| Portal secrets encryption | `klai-portal/backend/app/services/secrets.py` | All | ✅ Implemented |
| DB migration (mcp_servers column) | `klai-portal/backend/alembic/versions/d2e3f4a5b6c7_*.py` | All | ✅ Implemented |
| Base librechat.yaml | `deploy/librechat/librechat.yaml` | All | ✅ Exists |
| MCP Catalog YAML | `deploy/librechat/mcp_catalog.yaml` | N/A | ❌ Does not exist |
| Portal API MCP settings endpoint | `klai-portal/backend/app/api/` | N/A | ❌ Does not exist |
| Portal UI for MCP config | `klai-portal/frontend/src/` | N/A | ❌ Does not exist |

---

## 10. Architectural Options for Full Implementation

### Option A: Continue with Streamable-HTTP (Recommended)

**Approach**:
- Twenty CRM stays as `streamable-http` to `crm.getklai.com/mcp`
- Implement DB-driven provisioning (mcp_catalog.yaml, Portal UI, secrets encryption)
- Keep MCP servers external to LibreChat

**Pros**:
- Works with Twenty's built-in MCP server (no forks)
- Simpler deployment (no new containers)
- Faster to implement

**Cons**:
- Requires manual system prompt update for API key rotation (workaround needed)
- All MCP servers must be external/network-accessible
- Scales with number of tenants but not with MCP server types

**Implementation Timeline**:
1. Create `mcp_catalog.yaml` with Twenty CRM and klai-knowledge templates
2. Implement secrets encryption in `mcp_servers` column
3. Create Portal API endpoints for MCP management
4. Build Portal UI settings page
5. Update provisioning to call `_generate_librechat_yaml()` with real data

### Option B: Future - Per-Service MCP Containers

**Approach**:
- Deploy each MCP server as a separate Docker container per tenant
- Use SSE transport (Server-Sent Events)
- LibreChat connects via internal network URL: `http://{slug}-{service}-mcp:{port}/sse`

**Pros**:
- Complete isolation (resource limits, restarts)
- Easier secret management (one container per secret)
- Scales well (independent MCP service lifecycle)

**Cons**:
- Significant infrastructure change
- More containers to manage per tenant
- Requires SSE support in LibreChat

**Implementation Timeline**: Phase 2 (after Option A is complete)

---

## 11. Risk Assessment & Implicit Contracts

### 11.1 What Must Not Break

1. **Existing Provisioning Flow**: Current tenants (non-MCP) must continue working. Any changes must be backward compatible.
2. **LibreChat Startup**: Container must always start successfully, even if MCP config is malformed.
3. **klai-knowledge Integration**: The internal knowledge MCP is tested in production. Don't break it.
4. **Tenant Isolation**: One tenant's MCP config cannot affect another tenant.
5. **Redis Caching**: LibreChat's cache behavior is understood and relied upon. Changes require testing.

### 11.2 Implicit Assumptions

- **Environment Variables Persist at Startup**: Once a container reads its `.env`, those values don't change until container restarts.
- **Network Access to Twenty**: LibreChat containers have outbound internet access to `crm.getklai.com`.
- **YAML Parsing is Permissive**: Invalid MCP server configs in YAML should either fail fast (container won't start) or be silently ignored (based on LibreChat's behavior).
- **Secrets Never in Logs**: Any secret stored in `mcp_servers` must be encrypted before DB insert, never leaked in application logs.

### 11.3 Implicit Contracts with Operations

- **Container Restart Protocol**: Ops understands that config changes require container restart + Redis flush. This must be documented.
- **Secrets Backup**: SOPS must back up `mcp_servers` JSON (or at least a template). Don't lose tenant MCP secrets.
- **Monitoring**: Alert if LibreChat containers fail to start (config error likely).

---

## 12. Next Steps for Implementation

### Phase 1: Foundation (this SPEC)
1. ✅ Understand current architecture (this document)
2. ⬜ Create `mcp_catalog.yaml` with Twenty CRM template
3. ⬜ Add secrets encryption to `mcp_servers` column
4. ⬜ Update DB migration to use streamable-http format
5. ⬜ Test `_generate_librechat_yaml()` with Twenty CRM config

### Phase 2: Portal Integration
1. ⬜ Create Portal API endpoints: POST/PUT/DELETE `/api/orgs/{org_id}/mcp-servers`
2. ⬜ Build Portal UI: Settings page to manage MCP servers
3. ⬜ Implement API key validation (test connection before save)
4. ⬜ Add audit log for MCP config changes
5. ⬜ Document the Twenty API key rotation procedure

### Phase 3: Production Readiness
1. ⬜ End-to-end test: Portal UI → DB → Provisioning → LibreChat
2. ⬜ Test API key rotation scenario
3. ⬜ Document container restart / Redis flush protocol for ops
4. ⬜ Create runbook for MCP server troubleshooting
5. ⬜ Backup/restore testing for `mcp_servers` data

---

## Confidence Levels

| Finding | Confidence | Evidence |
|---------|-----------|----------|
| PortalOrg.mcp_servers exists | 100% | Visible in schema, migration applied |
| _generate_librechat_yaml() works | 95% | Code is implemented, called in provisioning |
| LibreChat YAML expansion works | 95% | Live in production for klai-knowledge |
| Twenty MCP is streamable-http | 100% | Documented in SPEC, configured in YAML |
| Redis caches APP_CONFIG with no TTL | 85% | SPEC mentions it, pitfall in platform.md confirms |
| Secrets encryption pattern works | 90% | Used for OIDC secrets, proven in production |
| Twenty API key rotation is manual workaround | 100% | Hardcoded token in system prompt visible on server |
| mcp_catalog.yaml doesn't exist | 100% | File search found nothing |
| Portal UI for MCP doesn't exist | 100% | No endpoints or components in codebase |

---

## Sources & References

- SPEC-INFRA-001 spec.md (v3.1.0)
- provisioning.py (full implementation review)
- portal.py (schema definition)
- secrets.py (encryption service)
- librechat.yaml (base template)
- Database migration d2e3f4a5b6c7
- Pitfall docs: platform.md (LibreChat caching, Twenty auth)
- Platform patterns: MCP Catalog design (SPEC v3.0)

