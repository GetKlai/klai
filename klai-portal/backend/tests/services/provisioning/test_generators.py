"""
Characterization tests for provisioning generators.

Tests _slugify_unique, _generate_librechat_env, and _generate_librechat_yaml
against the CURRENT behavior before the package extraction.
"""

import textwrap
from unittest.mock import patch

import pytest
import yaml


@pytest.fixture(autouse=True)
def _mock_settings():
    """Provide deterministic settings for all tests."""
    import app.services.provisioning.generators  # noqa: F401

    with patch("app.services.provisioning.generators.settings") as mock:
        mock.domain = "getklai.com"
        mock.mongo_root_password = "test-mongo-pw"
        mock.meili_master_key = "test-meili-key"
        mock.redis_password = "test-redis-pw"
        mock.litellm_master_key = "test-litellm-master"
        mock.firecrawl_internal_key = "test-firecrawl-key"
        mock.knowledge_ingest_secret = "test-knowledge-secret"
        yield mock


class TestCharacterizeSlugifyUnique:
    """Characterization tests for _slugify_unique."""

    def test_basic_slugification(self):
        from app.services.provisioning import _slugify_unique

        result = _slugify_unique("Acme Corp", set())
        assert result == "acme-corp"

    def test_special_characters_removed(self):
        from app.services.provisioning import _slugify_unique

        result = _slugify_unique("Hello! @World#", set())
        assert result == "hello-world"

    def test_unicode_normalized(self):
        from app.services.provisioning import _slugify_unique

        result = _slugify_unique("Cafe\u0301", set())
        assert result == "cafe"

    def test_uniqueness_suffix(self):
        from app.services.provisioning import _slugify_unique

        existing = {"acme-corp"}
        result = _slugify_unique("Acme Corp", existing)
        assert result == "acme-corp-2"

    def test_multiple_collisions(self):
        from app.services.provisioning import _slugify_unique

        existing = {"acme-corp", "acme-corp-2", "acme-corp-3"}
        result = _slugify_unique("Acme Corp", existing)
        assert result == "acme-corp-4"

    def test_empty_name_fallback(self):
        from app.services.provisioning import _slugify_unique

        result = _slugify_unique("!!!", set())
        assert result == "org"

    def test_long_name_truncated(self):
        from app.services.provisioning import _slugify_unique

        long_name = "a" * 100
        result = _slugify_unique(long_name, set())
        assert len(result) <= 50

    def test_whitespace_becomes_hyphens(self):
        from app.services.provisioning import _slugify_unique

        result = _slugify_unique("  My   Company  ", set())
        assert result == "my-company"


class TestCharacterizeGenerateLibrechatEnvSnapshot:
    """Snapshot test: full output with deterministic secrets."""

    def test_full_env_snapshot(self):
        from app.services.provisioning import _generate_librechat_env

        with patch("app.services.provisioning.generators.secrets") as mock_secrets:
            mock_secrets.token_hex = lambda n: "ab" * n

            result = _generate_librechat_env(
                slug="snapshot-org",
                client_id="cid-snap",
                client_secret="csec-snap",
                litellm_api_key="key-snap",
                mongo_password="pw-snap",
                zitadel_org_id="org-snap-123",
            )

        # Verify key structural properties
        assert "MONGO_URI=mongodb://librechat-snapshot-org:pw-snap@mongodb:27017" in result
        assert "OPENID_CLIENT_ID=cid-snap" in result
        assert "DOMAIN_CLIENT=https://chat-snapshot-org.getklai.com" in result
        assert "KLAI_ZITADEL_ORG_ID=org-snap-123" in result
        assert "KLAI_ORG_SLUG=snapshot-org" in result
        # Deterministic secrets
        assert f"JWT_SECRET={'ab' * 32}" in result
        assert f"CREDS_IV={'ab' * 8}" in result


class TestCharacterizeGenerateLibrechatYaml:
    """Characterization tests for _generate_librechat_yaml."""

    @pytest.fixture()
    def base_yaml_file(self, tmp_path):
        content = textwrap.dedent("""\
            version: 1.3.5
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
                  label: Klai AI
                  default: true
        """)
        p = tmp_path / "librechat.yaml"
        p.write_text(content)
        return p

    def test_no_mcp_servers_returns_base(self, base_yaml_file):
        from app.services.provisioning import _generate_librechat_yaml

        result = _generate_librechat_yaml(base_yaml_file, None)
        parsed = yaml.safe_load(result)
        assert "klai-knowledge" in parsed["mcpServers"]
        assert len(parsed["mcpServers"]) == 1

    def test_returns_string(self, base_yaml_file):
        from app.services.provisioning import _generate_librechat_yaml

        result = _generate_librechat_yaml(base_yaml_file, None)
        assert isinstance(result, str)

    def test_valid_yaml_output(self, base_yaml_file):
        from app.services.provisioning import _generate_librechat_yaml

        result = _generate_librechat_yaml(base_yaml_file, None)
        parsed = yaml.safe_load(result)
        assert isinstance(parsed, dict)
        assert "version" in parsed
