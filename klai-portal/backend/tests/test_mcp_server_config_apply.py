from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from app.api import mcp_servers as mcp_mod
from app.api.mcp_servers import McpServerUpdateRequest
from tests.conftest import make_perms


def _write_librechat_files(tmp_path) -> None:
    (tmp_path / "librechat.yaml").write_text(
        textwrap.dedent(
            """\
            version: 1.3.8
            mcpServers:
              klai-knowledge:
                type: streamable-http
                url: http://klai-knowledge-mcp:8080/mcp
            modelSpecs:
              prioritize: true
              list:
                - name: klai-primary
                  mcpServers:
                    - klai-knowledge
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "mcp_catalog.yaml").write_text(
        textwrap.dedent(
            """\
            servers:
              twenty-crm:
                display_name: Twenty CRM
                required_env_vars:
                  - TWENTY_API_KEY
                  - TWENTY_BASE_URL
                config_template:
                  type: streamable-http
                  url: "${TWENTY_BASE_URL}/mcp"
                  headers:
                    Authorization: "Bearer ${TWENTY_API_KEY}"
            """
        ),
        encoding="utf-8",
    )
    tenant_dir = tmp_path / "acme"
    tenant_dir.mkdir()
    (tenant_dir / ".env").write_text(
        textwrap.dedent(
            """\
            JWT_SECRET=keep-me

            # MCP server: twenty-crm
            TWENTY_API_KEY=old-key
            TWENTY_BASE_URL=https://old.example
            """
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_update_mcp_server_applies_yaml_and_env_before_recreate(tmp_path, monkeypatch) -> None:
    """Admin MCP changes must update mounted files before recreating LibreChat."""

    _write_librechat_files(tmp_path)
    monkeypatch.setattr(mcp_mod.settings, "librechat_container_data_path", str(tmp_path))
    monkeypatch.setattr(mcp_mod.settings, "librechat_host_data_path", str(tmp_path))

    org = MagicMock()
    org.id = 1
    org.slug = "acme"
    org.mcp_servers = None
    org.platform_unlocked_features = ["custom_mcps"]

    import app.services.provisioning as provisioning

    db = AsyncMock()
    db.commit = AsyncMock()
    cache_invalidate = MagicMock()
    recreate = MagicMock()

    monkeypatch.setattr(mcp_mod, "_load_org_or_500", AsyncMock(return_value=org))
    monkeypatch.setattr(
        mcp_mod,
        "_load_catalog",
        AsyncMock(return_value=yaml.safe_load((tmp_path / "mcp_catalog.yaml").read_text())["servers"]),
    )
    monkeypatch.setattr(provisioning, "_invalidate_librechat_config_cache", cache_invalidate)
    monkeypatch.setattr(provisioning, "_start_librechat_container", recreate)

    response = await mcp_mod.update_mcp_server(
        "twenty-crm",
        McpServerUpdateRequest(
            enabled=True,
            env={
                "TWENTY_API_KEY": "new-key",
                "TWENTY_BASE_URL": "https://crm.example",
            },
        ),
        perms=make_perms(org_id=1, org_slug="acme", platform_unlocked_features=["custom_mcps"]),
        db=db,
    )
    assert response.restart_required is False

    parsed_yaml = yaml.safe_load((tmp_path / "acme" / "librechat.yaml").read_text(encoding="utf-8"))
    assert "twenty-crm" in parsed_yaml["mcpServers"]
    assert parsed_yaml["modelSpecs"]["list"][0]["mcpServers"] == ["klai-knowledge", "twenty-crm"]

    env_text = (tmp_path / "acme" / ".env").read_text(encoding="utf-8")
    assert "JWT_SECRET=keep-me" in env_text
    assert "TWENTY_API_KEY=new-key" in env_text
    assert "TWENTY_BASE_URL=https://crm.example" in env_text
    assert "old-key" not in env_text
    cache_invalidate.assert_called_once_with("acme")
    recreate.assert_called_once_with("acme", f"{tmp_path}/acme/.env", org.mcp_servers)
