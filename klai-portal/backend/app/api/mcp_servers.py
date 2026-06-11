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
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _load_org_or_500
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import (
    UserPermissions,
    assert_platform_unlocked,
    get_caller_at_least,
)
from app.core.profiles import ProfileRole
from app.core.provisioning_names import validate_slug_for_provisioning
from app.services.provisioning.generators import _generate_librechat_yaml
from app.services.secrets import decrypt_mcp_secret, encrypt_mcp_secret, is_secret_var
from app.utils.response_sanitizer import sanitize_response_body  # SPEC-SEC-INTERNAL-001 REQ-4

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["mcp-servers"])

_CONFIGURED_PLACEHOLDER = "••••••••"


async def _load_catalog() -> dict[str, Any]:
    """Load and return the MCP catalog. Returns empty dict on missing file."""
    catalog_path = Path(settings.librechat_container_data_path) / "mcp_catalog.yaml"
    try:
        catalog = await asyncio.to_thread(_read_yaml, catalog_path)
        return catalog.get("servers", {})
    except FileNotFoundError:
        logger.warning("mcp_catalog.yaml not found at %s", catalog_path)
        return {}


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _catalog_env_var_names(catalog_servers: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for entry in catalog_servers.values():
        names.update(entry.get("required_env_vars", []))
    return names


def _render_mcp_env_lines(mcp_servers: dict[str, Any], catalog_servers: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for server_id, server_cfg in mcp_servers.items():
        if not server_cfg.get("enabled", False):
            continue
        catalog_entry = catalog_servers.get(server_id)
        if not catalog_entry or catalog_entry.get("managed", False):
            continue

        env_vars = server_cfg.get("env", {})
        rendered_for_server: list[str] = []
        for var_name in catalog_entry.get("required_env_vars", []):
            raw_value = env_vars.get(var_name)
            if not raw_value:
                continue
            if is_secret_var(var_name):
                raw_value = decrypt_mcp_secret(raw_value)
            rendered_for_server.append(f"{var_name}={raw_value}")

        if rendered_for_server:
            lines.append(f"# MCP server: {server_id}")
            lines.extend(rendered_for_server)

    return lines


def _replace_mcp_env_block(env_path: Path, mcp_servers: dict[str, Any], catalog_servers: dict[str, Any]) -> None:
    if not env_path.exists():
        raise RuntimeError(f"LibreChat tenant env file missing: {env_path}")

    mcp_env_names = _catalog_env_var_names(catalog_servers)
    kept_lines: list[str] = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# MCP server:"):
            continue
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in mcp_env_names:
            continue
        kept_lines.append(line)

    while kept_lines and not kept_lines[-1].strip():
        kept_lines.pop()

    mcp_lines = _render_mcp_env_lines(mcp_servers, catalog_servers)
    if mcp_lines:
        kept_lines.extend(["", *mcp_lines])

    env_path.write_text("\n".join(kept_lines).rstrip() + "\n", encoding="utf-8")


def _write_tenant_mcp_runtime_files(
    slug: str,
    mcp_servers: dict[str, Any],
    catalog_servers: dict[str, Any],
) -> None:
    validate_slug_for_provisioning(slug, domain=settings.domain)
    base_dir = Path(settings.librechat_container_data_path)
    base_yaml_path = base_dir / "librechat.yaml"
    tenant_dir = base_dir / slug
    env_path = tenant_dir / ".env"
    if not env_path.exists():
        raise RuntimeError(f"LibreChat tenant env file missing: {env_path}")

    tenant_yaml_content = _generate_librechat_yaml(base_yaml_path, mcp_servers)
    (tenant_dir / "librechat.yaml").write_text(tenant_yaml_content, encoding="utf-8")
    _replace_mcp_env_block(env_path, mcp_servers, catalog_servers)


def _build_stored_env(
    *,
    enabled: bool,
    submitted_env: dict[str, str],
    existing_env: dict[str, str],
) -> dict[str, str]:
    if not enabled:
        return {}

    stored_env = dict(existing_env)
    for var_name, value in submitted_env.items():
        if value in ("", _CONFIGURED_PLACEHOLDER) and var_name in existing_env:
            continue
        if value == _CONFIGURED_PLACEHOLDER:
            continue
        if value and is_secret_var(var_name):
            stored_env[var_name] = encrypt_mcp_secret(value)
        else:
            stored_env[var_name] = value
    return stored_env


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
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> McpServersResponse:
    """List all catalog MCP servers with per-tenant enable/configure state.

    Combines catalog metadata (description, required_env_vars) with tenant data
    (enabled flag, which env vars are already configured). Secret values are
    never returned — only the var names.
    """
    org = await _load_org_or_500(db, perms.org_id)
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
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> McpServerUpdateResponse:
    """Enable/disable a MCP server and store its env var configuration.

    Secret vars (KEY/SECRET/TOKEN in name) are encrypted with AES-256-GCM
    before being stored. Applies tenant runtime files and recreates LibreChat.
    """
    org = await _load_org_or_500(db, perms.org_id)
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

    # Non-managed MCP selection is platform-locked.
    # SPEC-PORTAL-RBAC-REFACTOR-001 Phase 5C: only enabling non-managed catalog entries
    # requires "custom_mcps" to be in platform_unlocked_features. Managed entries (always-on,
    # Klai-curated) are exempt — tenants can never enable them anyway (blocked above).
    if body.enabled:
        assert_platform_unlocked(org, "custom_mcps")

    required_vars = catalog_entry.get("required_env_vars", [])

    # Merge into existing mcp_servers JSON
    current: dict[str, Any] = dict(org.mcp_servers) if org.mcp_servers else {}
    existing_entry = current.get(server_id, {})
    existing_env = dict(existing_entry.get("env", {})) if isinstance(existing_entry, dict) else {}

    try:
        stored_env = _build_stored_env(enabled=body.enabled, submitted_env=body.env, existing_env=existing_env)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid MCP server configuration: {exc}",
        ) from exc

    # Validate all required vars are present when enabling. Existing configured
    # values count: edit forms omit already-stored secrets so they are not leaked.
    if body.enabled:
        missing = [v for v in required_vars if not stored_env.get(v)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Missing required env vars: {missing}",
            )

    current[server_id] = {"enabled": body.enabled, "env": stored_env}

    # SQLAlchemy needs explicit assignment to detect JSONB mutation
    org.mcp_servers = current  # type: ignore[assignment]
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(org, "mcp_servers")
    await db.commit()

    configured_vars = list(stored_env.keys())
    slug = org.slug
    mcp_servers_to_apply = current

    # Apply the DB state to the mounted tenant runtime files, invalidate
    # LibreChat's config cache, then recreate the tenant container. Recreate is
    # intentional: Docker bakes the `.env` content into the container environment
    # at create time, so secret rotation must not rely on restart semantics.
    # A 200 means the runtime picked up the saved config; failed rollout is
    # surfaced as 502, not hidden behind a best-effort background task.
    try:
        from app.services.provisioning import _invalidate_librechat_config_cache, _start_librechat_container

        await asyncio.to_thread(_write_tenant_mcp_runtime_files, slug, mcp_servers_to_apply, catalog_servers)
        await asyncio.to_thread(_invalidate_librechat_config_cache, slug)
        env_file_host_path = f"{settings.librechat_host_data_path}/{slug}/.env"
        await asyncio.to_thread(_start_librechat_container, slug, env_file_host_path, mcp_servers_to_apply)
    except Exception as exc:
        logger.exception("mcp_server_runtime_apply_failed: slug=%s server_id=%s", slug, server_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MCP config was saved, but LibreChat runtime update failed",
        ) from exc

    return McpServerUpdateResponse(
        id=server_id,
        enabled=body.enabled,
        configured_env_vars=configured_vars,
        restart_required=False,
    )


# ---------------------------------------------------------------------------
# T11: POST /api/mcp-servers/{server_id}/test
# ---------------------------------------------------------------------------


@router.post("/mcp-servers/{server_id}/test", response_model=McpTestResponse)
async def test_mcp_server(
    server_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> McpTestResponse:
    """Test connectivity to a configured MCP server.

    Sends a JSON-RPC 'initialize' request to the MCP server's URL with the
    configured Authorization header. Returns available tools on success.
    """
    org = await _load_org_or_500(db, perms.org_id)
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
            "MCP test failed for tenant %s / server %s: %s",
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
