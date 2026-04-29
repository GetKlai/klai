"""
MCP server management endpoints — per-tenant integration configuration.

All endpoints require admin authentication and resolve the caller's org from
their OIDC token. MCP server definitions come from the catalog; secrets are
encrypted with AES-256-GCM before being stored in portal_orgs.mcp_servers.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, _require_admin, bearer
from app.core.config import settings
from app.core.database import get_db
from app.services.secrets import decrypt_mcp_secret, encrypt_mcp_secret, is_secret_var
from app.utils.response_sanitizer import sanitize_response_body  # SPEC-SEC-INTERNAL-001 REQ-4

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["mcp-servers"])


async def _load_catalog() -> dict[str, Any]:
    """Load and return the MCP catalog. Returns empty dict on missing file."""
    catalog_path = Path(settings.librechat_container_data_path) / "mcp_catalog.yaml"
    try:
        catalog = await asyncio.to_thread(_read_yaml, catalog_path)
        return catalog.get("servers", {})
    except FileNotFoundError:
        logger.warning("mcp_catalog.yaml niet gevonden op %s", catalog_path)
        return {}


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Response / request schemas
# ---------------------------------------------------------------------------


class McpServerOut(BaseModel):
    id: str
    display_name: str
    description: str
    enabled: bool
    managed: bool
    required_env_vars: list[str]
    configured_env_vars: list[str]


class McpServersResponse(BaseModel):
    servers: list[McpServerOut]


class McpServerUpdateRequest(BaseModel):
    enabled: bool
    env: dict[str, str]


class McpServerUpdateResponse(BaseModel):
    id: str
    enabled: bool
    configured_env_vars: list[str]
    restart_required: bool


class McpTestResponse(BaseModel):
    status: str
    response_time_ms: int | None = None
    tools_available: list[str] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# T9: GET /api/mcp-servers
# ---------------------------------------------------------------------------


@router.get("/mcp-servers", response_model=McpServersResponse)
async def list_mcp_servers(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> McpServersResponse:
    """List all catalog MCP servers with per-tenant enable/configure state.

    Combines catalog metadata (description, required_env_vars) with tenant data
    (enabled flag, which env vars are already configured). Secret values are
    never returned — only the var names.
    """
    _zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    catalog_servers = await _load_catalog()
    tenant_config: dict[str, Any] = org.mcp_servers or {}

    servers_out: list[McpServerOut] = []
    for server_id, catalog_entry in catalog_servers.items():
        tenant_entry = tenant_config.get(server_id, {})
        configured_vars = list(tenant_entry.get("env", {}).keys()) if tenant_entry else []
        is_managed = bool(catalog_entry.get("managed", False))
        # Managed servers are always enabled — they are wired via librechat.yaml,
        # tenants cannot disable or configure them.
        enabled = is_managed or bool(tenant_entry.get("enabled", False))
        servers_out.append(
            McpServerOut(
                id=server_id,
                display_name=catalog_entry.get("display_name", server_id),
                description=catalog_entry.get("description", ""),
                enabled=enabled,
                managed=is_managed,
                required_env_vars=catalog_entry.get("required_env_vars", []),
                configured_env_vars=configured_vars,
            )
        )

    return McpServersResponse(servers=servers_out)


# ---------------------------------------------------------------------------
# T10: PUT /api/mcp-servers/{server_id}
# ---------------------------------------------------------------------------


@router.put("/mcp-servers/{server_id}", response_model=McpServerUpdateResponse)
async def update_mcp_server(
    server_id: str,
    body: McpServerUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> McpServerUpdateResponse:
    """Enable/disable a MCP server and store its env var configuration.

    Secret vars (KEY/SECRET/TOKEN in name) are encrypted with AES-256-GCM
    before being stored. Triggers an async Redis flush + container restart.
    """
    _zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    catalog_servers = await _load_catalog()
    if server_id not in catalog_servers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_id}' not found in catalog",
        )

    catalog_entry = catalog_servers[server_id]

    # Managed servers (wired via librechat.yaml) cannot be configured by tenants.
    if catalog_entry.get("managed", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"MCP server '{server_id}' is managed and cannot be modified",
        )

    required_vars = catalog_entry.get("required_env_vars", [])

    # Validate all required vars are present when enabling
    if body.enabled:
        missing = [v for v in required_vars if not body.env.get(v)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Missing required env vars: {missing}",
            )

    # Encrypt secret values; store non-secret values as-is
    stored_env: dict[str, str] = {}
    for var_name, value in body.env.items():
        if value and is_secret_var(var_name):
            try:
                stored_env[var_name] = encrypt_mcp_secret(value)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid value for {var_name}: {exc}",
                ) from exc
        else:
            stored_env[var_name] = value

    # Merge into existing mcp_servers JSON
    current: dict[str, Any] = dict(org.mcp_servers) if org.mcp_servers else {}
    current[server_id] = {"enabled": body.enabled, "env": stored_env}

    # SQLAlchemy needs explicit assignment to detect JSONB mutation
    org.mcp_servers = current  # type: ignore[assignment]
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(org, "mcp_servers")
    await db.commit()

    configured_vars = list(stored_env.keys())

    # Trigger async restart (fire-and-forget; R-002: brief container downtime acceptable).
    # The user has already received their 200 by the time this runs, so we log with
    # full traceback at error level — the MCP config change IS committed in portal,
    # but the LibreChat container may still be serving the old config if the restart
    # silently failed. That state needs to be visible in VictoriaLogs for operators.
    async def _restart() -> None:
        loop = asyncio.get_event_loop()
        from app.services.provisioning import _flush_redis_and_restart_librechat

        try:
            await loop.run_in_executor(None, lambda: _flush_redis_and_restart_librechat(org.slug))
        except Exception:
            logger.exception("mcp_server_librechat_restart_failed: slug=%s", org.slug)

    _task = asyncio.create_task(_restart())  # noqa: RUF006 — fire-and-forget, not awaited

    return McpServerUpdateResponse(
        id=server_id,
        enabled=body.enabled,
        configured_env_vars=configured_vars,
        restart_required=True,
    )


# ---------------------------------------------------------------------------
# T11: POST /api/mcp-servers/{server_id}/test
# ---------------------------------------------------------------------------


@router.post("/mcp-servers/{server_id}/test", response_model=McpTestResponse)
async def test_mcp_server(
    server_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> McpTestResponse:
    """Test connectivity to a configured MCP server.

    Sends a JSON-RPC 'initialize' request to the MCP server's URL with the
    configured Authorization header. Returns available tools on success.
    """
    _zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    catalog_servers = await _load_catalog()
    if server_id not in catalog_servers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_id}' not found in catalog",
        )

    # Managed servers are wired via librechat.yaml; testing requires headers
    # that only LibreChat injects (X-Org-Slug, X-Internal-Secret, etc).
    if catalog_servers[server_id].get("managed", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"MCP server '{server_id}' is managed and cannot be tested from the portal",
        )

    tenant_config: dict[str, Any] = org.mcp_servers or {}
    tenant_entry = tenant_config.get(server_id, {})
    if not tenant_entry.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"MCP server '{server_id}' is not enabled",
        )

    # Decrypt env vars to build the request headers
    stored_env = tenant_entry.get("env", {})
    decrypted_env: dict[str, str] = {}
    for var_name, value in stored_env.items():
        if is_secret_var(var_name):
            try:
                decrypted_env[var_name] = decrypt_mcp_secret(value)
            except ValueError:
                return McpTestResponse(
                    status="error",
                    error=f"Could not decrypt secret for {var_name} — reconfigure the integration",
                )
        else:
            decrypted_env[var_name] = value

    # Resolve the MCP URL and Authorization header from catalog config_template
    catalog_entry = catalog_servers[server_id]
    config_template = catalog_entry.get("config_template", {})
    mcp_url = config_template.get("url", "")
    headers_template = config_template.get("headers", {})

    # Expand ${VAR} placeholders in URL and headers
    for var_name, var_value in decrypted_env.items():
        mcp_url = mcp_url.replace(f"${{{var_name}}}", var_value)
        headers_template = {k: v.replace(f"${{{var_name}}}", var_value) for k, v in headers_template.items()}

    if not mcp_url:
        return McpTestResponse(status="error", error="MCP server URL not configured")

    result = await _probe_mcp_server(mcp_url, headers_template)
    if result.status == "error":
        logger.warning(
            "MCP test mislukt voor tenant %s / server %s: %s",
            org.slug,
            server_id,
            result.error,
        )
    return result


async def _probe_mcp_server(url: str, headers: dict[str, str]) -> McpTestResponse:
    """Send a JSON-RPC initialize request and return the test result."""
    jsonrpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "klai-portal-test", "version": "1.0"},
        },
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=jsonrpc_payload, headers=headers)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code >= 400:
            return McpTestResponse(
                status="error",
                response_time_ms=elapsed_ms,
                error=f"HTTP {resp.status_code}: {sanitize_response_body(resp, max_len=200)}",
            )

        data = resp.json()
        tools: list[str] = []
        result_data = data.get("result", {})
        if isinstance(result_data, dict):
            for tool in result_data.get("tools", []):
                if isinstance(tool, dict) and "name" in tool:
                    tools.append(tool["name"])

        return McpTestResponse(
            status="ok",
            response_time_ms=elapsed_ms,
            tools_available=tools or None,
        )

    except httpx.ConnectError as exc:
        return McpTestResponse(status="error", error=f"Connection refused to {url}: {exc}")
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return McpTestResponse(
            status="error",
            response_time_ms=elapsed_ms,
            error=f"Timeout connecting to {url}",
        )
