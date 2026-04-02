# Research: Per-tenant MCP Configuration for LibreChat

## Architecture Analysis

### Current LibreChat Configuration Flow

```
deploy/librechat/librechat.yaml  (shared, one file)
         |
         v
   mounted read-only into ALL LibreChat containers
         |
    +-----------+-----------+
    |           |           |
librechat-klai  librechat-getklai  librechat-{dynamic}
(docker-compose (docker-compose     (provisioning.py
 inline env)     env_file)           containers.run())
```

**Key files:**

| File | Purpose | Lines |
|------|---------|-------|
| `deploy/librechat/librechat.yaml` | Shared MCP + model config | 90 lines |
| `deploy/docker-compose.yml` | Pre-provisioned tenant containers | Lines 227-296 |
| `klai-portal/backend/app/services/provisioning.py` | Dynamic tenant creation | 511 lines |
| `deploy/librechat/_template/README.md` | Documents future per-tenant yaml intent | 11 lines |

### MCP Configuration in librechat.yaml (lines 30-46)

```yaml
mcpServers:
  klai-knowledge:
    type: streamable-http
    url: http://klai-knowledge-mcp:8080/mcp
    headers:
      X-User-ID: "{{LIBRECHAT_USER_ID}}"       # runtime template (per-request)
      X-Org-ID: "${KLAI_ORG_ID}"               # env var expansion (per-container)
      X-Org-Slug: "${KLAI_ORG_SLUG}"           # env var expansion (per-container)
      X-Internal-Secret: "${KNOWLEDGE_INGEST_SECRET}"
```

LibreChat supports two variable syntaxes:
- `${VAR}` — expanded from container env at startup
- `{{LIBRECHAT_USER_ID}}` — runtime template injected per-request

Model specs reference MCP servers via array: `modelSpecs.list[].mcpServers: ["klai-knowledge"]`

### Per-Tenant Provisioning (provisioning.py)

Each tenant gets:
1. Per-tenant `.env` at `librechat/{slug}/.env` (lines 182-266)
2. Per-tenant Docker container via `client.containers.run()` (lines 321-345)
3. Per-tenant Caddyfile at `caddy/tenants/{slug}.caddyfile` (lines 269-302)

Volume mounts in provisioning.py (line 339-345):
```python
volumes={
    env_file_host_path: {"bind": "/app/.env", "mode": "ro"},
    f"{librechat_host_base}/librechat.yaml": {"bind": "/app/librechat.yaml", "mode": "ro"},
    f"{librechat_host_base}/{slug}/images": {"bind": "/app/client/public/images", "mode": "rw"},
}
```

Note: `librechat.yaml` path has NO slug — all dynamic tenants share the same file.

### Pre-provisioned Tenants in docker-compose.yml

**librechat-klai** (lines 227-269): Inline `environment:` block, no env_file.
**librechat-getklai** (lines 271-296): Uses `env_file: ./librechat/getklai/.env` + `environment: REDIS_URI`.

Both mount `./librechat/librechat.yaml:/app/librechat.yaml:ro`.

### Template Directory Intent

`deploy/librechat/_template/README.md` states:
> "At runtime, the portal-api service writes per-tenant LibreChat configuration files here (librechat.yaml per tenant)."

This is **future work** — not implemented. Only `.env` files are per-tenant.

---

## Bugs Discovered

### BUG-1: KLAI_ORG_ID mismatch (CRITICAL)

- `librechat.yaml:40` references `${KLAI_ORG_ID}`
- `provisioning.py:264` sets `KLAI_ZITADEL_ORG_ID` (not `KLAI_ORG_ID`)
- **Result:** `X-Org-ID` MCP header is empty for all provisioned tenants
- **Impact:** klai-knowledge MCP server cannot identify the tenant's org

### BUG-2: KNOWLEDGE_INGEST_SECRET missing from LibreChat containers

- `librechat.yaml:42` references `${KNOWLEDGE_INGEST_SECRET}`
- Neither `librechat-klai` (inline env) nor `librechat-getklai` (env_file) has this variable
- `provisioning.py` .env template also omits it
- **Result:** `X-Internal-Secret` MCP header is empty — knowledge MCP auth is broken
- **Impact:** klai-knowledge-mcp rejects requests from LibreChat (or accepts without auth)
- Setting exists in portal backend: `config.py:92` as `knowledge_ingest_secret`

---

## Twenty CRM MCP Server Research

### Package Comparison

| Package | npm | Stars | Transport | Tools |
|---------|-----|-------|-----------|-------|
| `jezweb/twenty-mcp` | `twenty-mcp-server` | 38 | stdio | 29 |
| `mhenry3164/twenty-crm-mcp-server` | not published | 46 | stdio | ~15 |

### Selected: `twenty-mcp-server` (jezweb)

Reasons:
- Published on npm (can use `npx -y twenty-mcp-server start`)
- 29 tools (most comprehensive)
- Full TypeScript, typed tools
- Active maintenance (last update: Mar 2026)

### Required Environment Variables

- `TWENTY_API_KEY` — from Twenty CRM Settings > API & Webhooks
- `TWENTY_BASE_URL` — Twenty CRM instance URL (e.g. `https://crm.getklai.com/api`)

### LibreChat stdio MCP Config Pattern

```yaml
mcpServers:
  twenty-crm:
    type: stdio
    command: npx
    args: ["-y", "twenty-mcp-server", "start"]
    timeout: 60000
    initTimeout: 30000
    env:
      TWENTY_API_KEY: "${TWENTY_API_KEY}"
      TWENTY_BASE_URL: "${TWENTY_BASE_URL}"
```

### Risk: npx availability

LibreChat image `ghcr.io/danny-avila/librechat:latest` is Node.js-based — `npx` should be available. Must verify with `docker exec librechat-getklai which npx`.

### Risk: Cold start

First `npx` invocation downloads the package (~15-30s). Mitigated by `initTimeout: 30000`.

---

## Reference Implementations

### Existing per-tenant file pattern (provisioning.py)

`.env` generation at lines 182-266 is the exact pattern to follow:
1. Generate content from template with tenant-specific values
2. Write to `librechat/{slug}/.env`
3. Mount into container

### Existing MCP integration (klai-knowledge)

`librechat.yaml:34-42` shows the pattern for adding MCP servers:
- Define in `mcpServers:` block
- Reference in `modelSpecs.list[].mcpServers` array
- Use env var expansion for credentials

---

## Recommendations

1. **Per-tenant yaml approach**: Create `deploy/librechat/getklai/librechat.yaml` as a standalone file (not template-generated) for Phase 1. Template generation belongs in Phase 2.
2. **Fix both bugs**: KLAI_ORG_ID mismatch and KNOWLEDGE_INGEST_SECRET should be fixed in the same change set.
3. **stdio transport**: Use `npx -y twenty-mcp-server start` — simplest integration, no extra container needed.
4. **Credentials in .env**: `TWENTY_API_KEY` and `TWENTY_BASE_URL` added to getklai's `.env` on the server (not in git).
