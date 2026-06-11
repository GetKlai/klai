from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from app.services.provisioning import _generate_librechat_env, _generate_librechat_yaml
from app.services.secrets import encrypt_mcp_secret


def _write_base_files(tmp_path: Path) -> Path:
    base_yaml = tmp_path / "librechat.yaml"
    base_yaml.write_text(
        textwrap.dedent(
            """\
            version: 1.3.12
            mcpServers:
              klai-knowledge:
                type: streamable-http
                url: http://klai-knowledge-mcp:8080/mcp
            modelSpecs:
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
    return base_yaml


def _tenant_mcp_config(api_key: str, base_url: str) -> dict:
    return {
        "twenty-crm": {
            "enabled": True,
            "env": {
                "TWENTY_API_KEY": encrypt_mcp_secret(api_key),
                "TWENTY_BASE_URL": base_url,
            },
        }
    }


def test_two_tenant_mcp_generation_is_isolated(tmp_path: Path) -> None:
    base_yaml = _write_base_files(tmp_path)
    tenant_a_mcp = _tenant_mcp_config("sk-tenant-a", "https://tenant-a.example.test")
    tenant_b_mcp = _tenant_mcp_config("sk-tenant-b", "https://tenant-b.example.test")

    yaml_a = yaml.safe_load(_generate_librechat_yaml(base_yaml, tenant_a_mcp))
    yaml_b = yaml.safe_load(_generate_librechat_yaml(base_yaml, tenant_b_mcp))
    env_a = _generate_librechat_env(
        "tenant-a",
        "client-a",
        "secret-a",
        "litellm-a",
        "mongo-a",
        "meili-a",
        "zitadel-a",
        tenant_a_mcp,
    )
    env_b = _generate_librechat_env(
        "tenant-b",
        "client-b",
        "secret-b",
        "litellm-b",
        "mongo-b",
        "meili-b",
        "zitadel-b",
        tenant_b_mcp,
    )

    assert yaml_a["mcpServers"]["twenty-crm"] == yaml_b["mcpServers"]["twenty-crm"]
    assert yaml_a["modelSpecs"]["list"][0]["mcpServers"] == ["klai-knowledge", "twenty-crm"]
    assert yaml_b["modelSpecs"]["list"][0]["mcpServers"] == ["klai-knowledge", "twenty-crm"]

    assert "TWENTY_API_KEY=sk-tenant-a" in env_a
    assert "TWENTY_BASE_URL=https://tenant-a.example.test" in env_a
    assert "sk-tenant-b" not in env_a
    assert "tenant-b.example.test" not in env_a

    assert "TWENTY_API_KEY=sk-tenant-b" in env_b
    assert "TWENTY_BASE_URL=https://tenant-b.example.test" in env_b
    assert "sk-tenant-a" not in env_b
    assert "tenant-a.example.test" not in env_b
