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
async def test_update_mcp_server_applies_yaml_and_env_before_restart(tmp_path, monkeypatch) -> None:
    """Admin MCP changes must update the mounted LibreChat files, not only restart."""

    _write_librechat_files(tmp_path)
    monkeypatch.setattr(mcp_mod.settings, "librechat_container_data_path", str(tmp_path))
    monkeypatch.setattr(mcp_mod.settings, "librechat_host_data_path", str(tmp_path))

    org = MagicMock()
    org.id = 1
    org.slug = "acme"
    org.mcp_servers = None
    org.platform_unlocked_features = ["custom_mcps"]

    db = AsyncMock()
    db.get = AsyncMock(return_value=org)
    db.commit = AsyncMock()

    monkeypatch.setattr(mcp_mod, "encrypt_mcp_secret", lambda value: f"encrypted:{value}")

    import app.services.provisioning as provisioning
    import app.services.provisioning.infrastructure as infra_mod

    monkeypatch.setattr(
        infra_mod,
        "decrypt_mcp_secret",
        lambda value: value.removeprefix("encrypted:"),
        raising=False,
    )
    monkeypatch.setattr(provisioning, "_flush_redis_and_restart_librechat", lambda slug: None)

    created_tasks = []

    def capture_task(coro):
        created_tasks.append(coro)
        return MagicMock()

    monkeypatch.setattr(mcp_mod.asyncio, "create_task", capture_task)

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
    assert response.restart_required is True
    assert created_tasks

    await created_tasks[0]

    parsed_yaml = yaml.safe_load((tmp_path / "acme" / "librechat.yaml").read_text(encoding="utf-8"))
    assert "twenty-crm" in parsed_yaml["mcpServers"]
    assert parsed_yaml["modelSpecs"]["list"][0]["mcpServers"] == ["klai-knowledge", "twenty-crm"]

    env_text = (tmp_path / "acme" / ".env").read_text(encoding="utf-8")
    assert "JWT_SECRET=keep-me" in env_text
    assert "TWENTY_API_KEY=new-key" in env_text
    assert "TWENTY_BASE_URL=https://crm.example" in env_text
    assert "old-key" not in env_text
