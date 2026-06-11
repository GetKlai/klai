from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from fastapi import HTTPException, status

from app.api.mcp_servers import McpServerUpdateRequest, update_mcp_server
from app.services.secrets import encrypt_mcp_secret
from tests.conftest import make_org, make_perms


def _catalog() -> dict:
    return {
        "twenty-crm": {
            "display_name": "Twenty CRM",
            "required_env_vars": ["TWENTY_API_KEY", "TWENTY_BASE_URL"],
            "config_template": {
                "type": "streamable-http",
                "url": "${TWENTY_BASE_URL}/mcp",
                "headers": {"Authorization": "Bearer ${TWENTY_API_KEY}"},
            },
        }
    }


def _db_mock() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_update_preserves_existing_secret_when_edit_omits_it() -> None:
    org = make_org(platform_unlocked_features=["custom_mcps"])
    org.mcp_servers = {
        "twenty-crm": {
            "enabled": True,
            "env": {
                "TWENTY_API_KEY": "encrypted-existing",
                "TWENTY_BASE_URL": "https://old.example.test",
            },
        }
    }

    runtime_apply = MagicMock()
    cache_invalidate = MagicMock()
    recreate = MagicMock()

    with (
        patch("app.api.mcp_servers._load_org_or_500", AsyncMock(return_value=org)),
        patch("app.api.mcp_servers._load_catalog", AsyncMock(return_value=_catalog())),
        patch("sqlalchemy.orm.attributes.flag_modified"),
        patch("app.api.mcp_servers._write_tenant_mcp_runtime_files", runtime_apply),
        patch("app.services.provisioning._invalidate_librechat_config_cache", cache_invalidate),
        patch("app.services.provisioning._start_librechat_container", recreate),
    ):
        response = await update_mcp_server(
            "twenty-crm",
            McpServerUpdateRequest(enabled=True, env={"TWENTY_BASE_URL": "https://new.example.test"}),
            perms=make_perms(platform_unlocked_features=["custom_mcps"]),
            db=_db_mock(),
        )

    assert org.mcp_servers["twenty-crm"]["env"] == {
        "TWENTY_API_KEY": "encrypted-existing",
        "TWENTY_BASE_URL": "https://new.example.test",
    }
    assert response.restart_required is False
    runtime_apply.assert_called_once_with(org.slug, org.mcp_servers, _catalog())
    cache_invalidate.assert_called_once_with(org.slug)
    recreate.assert_called_once_with(org.slug, "/opt/klai/librechat/voys/.env", org.mcp_servers)


@pytest.mark.asyncio
async def test_update_replaces_secret_when_new_secret_is_submitted() -> None:
    org = make_org(platform_unlocked_features=["custom_mcps"])
    org.mcp_servers = {
        "twenty-crm": {
            "enabled": True,
            "env": {
                "TWENTY_API_KEY": "encrypted-existing",
                "TWENTY_BASE_URL": "https://old.example.test",
            },
        }
    }

    with (
        patch("app.api.mcp_servers._load_org_or_500", AsyncMock(return_value=org)),
        patch("app.api.mcp_servers._load_catalog", AsyncMock(return_value=_catalog())),
        patch("app.api.mcp_servers.encrypt_mcp_secret", return_value="encrypted-new"),
        patch("sqlalchemy.orm.attributes.flag_modified"),
        patch("app.api.mcp_servers._write_tenant_mcp_runtime_files"),
        patch("app.services.provisioning._invalidate_librechat_config_cache"),
        patch("app.services.provisioning._start_librechat_container"),
    ):
        await update_mcp_server(
            "twenty-crm",
            McpServerUpdateRequest(
                enabled=True,
                env={
                    "TWENTY_API_KEY": "plain-new",
                    "TWENTY_BASE_URL": "https://new.example.test",
                },
            ),
            perms=make_perms(platform_unlocked_features=["custom_mcps"]),
            db=_db_mock(),
        )

    assert org.mcp_servers["twenty-crm"]["env"] == {
        "TWENTY_API_KEY": "encrypted-new",
        "TWENTY_BASE_URL": "https://new.example.test",
    }


@pytest.mark.asyncio
async def test_update_missing_required_vars_rejects_before_commit() -> None:
    org = make_org(platform_unlocked_features=["custom_mcps"])
    org.mcp_servers = {}
    db = _db_mock()

    with (
        patch("app.api.mcp_servers._load_org_or_500", AsyncMock(return_value=org)),
        patch("app.api.mcp_servers._load_catalog", AsyncMock(return_value=_catalog())),
        pytest.raises(HTTPException) as exc,
    ):
        await update_mcp_server(
            "twenty-crm",
            McpServerUpdateRequest(enabled=True, env={}),
            perms=make_perms(platform_unlocked_features=["custom_mcps"]),
            db=db,
        )

    assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_surfaces_runtime_apply_failure_after_save() -> None:
    org = make_org(platform_unlocked_features=["custom_mcps"])
    org.mcp_servers = {}
    db = _db_mock()

    with (
        patch("app.api.mcp_servers._load_org_or_500", AsyncMock(return_value=org)),
        patch("app.api.mcp_servers._load_catalog", AsyncMock(return_value=_catalog())),
        patch("sqlalchemy.orm.attributes.flag_modified"),
        patch("app.api.mcp_servers._write_tenant_mcp_runtime_files", side_effect=RuntimeError("disk full")),
        patch("app.services.provisioning._invalidate_librechat_config_cache") as cache_invalidate,
        patch("app.services.provisioning._start_librechat_container") as recreate,
        pytest.raises(HTTPException) as exc,
    ):
        await update_mcp_server(
            "twenty-crm",
            McpServerUpdateRequest(
                enabled=True,
                env={
                    "TWENTY_API_KEY": "plain-new",
                    "TWENTY_BASE_URL": "https://new.example.test",
                },
            ),
            perms=make_perms(platform_unlocked_features=["custom_mcps"]),
            db=db,
        )

    assert exc.value.status_code == status.HTTP_502_BAD_GATEWAY
    db.commit.assert_awaited_once()
    cache_invalidate.assert_not_called()
    recreate.assert_not_called()


def test_runtime_file_writer_updates_yaml_and_replaces_only_mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.mcp_servers as mcp_mod

    (tmp_path / "librechat.yaml").write_text(
        textwrap.dedent(
            """\
            version: 1.3.12
            mcpServers:
              klai-knowledge:
                type: streamable-http
            modelSpecs:
              list:
                - name: klai-primary
                  mcpServers:
                    - klai-knowledge
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "mcp_catalog.yaml").write_text(yaml.safe_dump({"servers": _catalog()}), encoding="utf-8")
    tenant_dir = tmp_path / "acme"
    tenant_dir.mkdir()
    (tenant_dir / ".env").write_text(
        "\n".join(
            [
                "JWT_SECRET=keep-me",
                "# MCP server: twenty-crm",
                "TWENTY_API_KEY=old",
                "TWENTY_BASE_URL=https://old.example.test",
                "LITELLM_API_KEY=also-keep-me",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    encrypted_key = encrypt_mcp_secret("sk-acme")
    mcp_servers = {
        "twenty-crm": {
            "enabled": True,
            "env": {
                "TWENTY_API_KEY": encrypted_key,
                "TWENTY_BASE_URL": "https://new.example.test",
            },
        }
    }

    monkeypatch.setattr(mcp_mod.settings, "librechat_container_data_path", str(tmp_path))

    mcp_mod._write_tenant_mcp_runtime_files("acme", mcp_servers, _catalog())

    parsed_yaml = yaml.safe_load((tenant_dir / "librechat.yaml").read_text(encoding="utf-8"))
    assert "twenty-crm" in parsed_yaml["mcpServers"]
    assert parsed_yaml["modelSpecs"]["list"][0]["mcpServers"] == ["klai-knowledge", "twenty-crm"]

    env_text = (tenant_dir / ".env").read_text(encoding="utf-8")
    assert "JWT_SECRET=keep-me" in env_text
    assert "LITELLM_API_KEY=also-keep-me" in env_text
    assert "TWENTY_API_KEY=old" not in env_text
    assert "TWENTY_API_KEY=sk-acme" in env_text
    assert "TWENTY_BASE_URL=https://new.example.test" in env_text
