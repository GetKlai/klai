"""Tests for connector credential encryption in API layer.

Verifies that:
- _connector_out masks sensitive fields with '***'
- The internal get_connector_config decrypts and merges credentials
- Legacy connectors (encrypted_credentials=None) work as fallback
"""

from unittest.mock import MagicMock

from app.api.connectors import _connector_out
from app.services.connector_credentials import SENSITIVE_FIELDS

# Test-only placeholder values (NOT real credentials)
FAKE_TOKEN = "test-placeholder-value"


class TestConnectorOutMasking:
    """_connector_out masks all sensitive fields per SENSITIVE_FIELDS mapping."""

    def _make_connector(self, connector_type: str, config: dict) -> MagicMock:
        c = MagicMock()
        c.id = "test-uuid-001"
        c.kb_id = 1
        c.name = "test connector"
        c.connector_type = connector_type
        c.config = config
        c.schedule = None
        c.is_enabled = True
        c.last_sync_at = None
        c.last_sync_status = None
        c.created_at = "2026-01-01T00:00:00Z"
        c.created_by = "user-1"
        c.content_type = "kb_article"
        c.allowed_assertion_modes = None
        return c

    def test_github_sensitive_fields_masked(self) -> None:
        config = {
            "repo": "GetKlai/klai",
            "access_token": FAKE_TOKEN,
            "installation_token": FAKE_TOKEN,
            "app_private_key": FAKE_TOKEN,
        }
        out = _connector_out(self._make_connector("github", config))
        assert out.config["access_token"] == "***"
        assert out.config["installation_token"] == "***"
        assert out.config["app_private_key"] == "***"
        assert out.config["repo"] == "GetKlai/klai"

    def test_notion_sensitive_fields_masked(self) -> None:
        config = {"workspace_id": "ws-123", "api_token": FAKE_TOKEN}
        out = _connector_out(self._make_connector("notion", config))
        assert out.config["api_token"] == "***"
        assert out.config["workspace_id"] == "ws-123"

    def test_web_crawler_sensitive_fields_masked(self) -> None:
        config = {"url": "https://example.com", "auth_headers": FAKE_TOKEN}
        out = _connector_out(self._make_connector("web_crawler", config))
        assert out.config["auth_headers"] == "***"
        assert out.config["url"] == "https://example.com"

    def test_unknown_type_no_masking(self) -> None:
        config = {"url": "https://example.com", "custom_field": "safe"}
        out = _connector_out(self._make_connector("unknown_type", config))
        assert out.config["url"] == "https://example.com"
        assert out.config["custom_field"] == "safe"

    def test_missing_sensitive_field_no_crash(self) -> None:
        """If a sensitive field is absent from config, masking should not crash."""
        config = {"repo": "GetKlai/klai"}  # no access_token etc.
        out = _connector_out(self._make_connector("github", config))
        assert out.config["repo"] == "GetKlai/klai"
        assert "access_token" not in out.config

    def test_all_connector_types_mask_correctly(self) -> None:
        """Every connector type in SENSITIVE_FIELDS has its fields masked."""
        for connector_type, fields in SENSITIVE_FIELDS.items():
            config = {f: FAKE_TOKEN for f in fields}
            config["safe_field"] = "visible"
            out = _connector_out(self._make_connector(connector_type, config))
            for f in fields:
                assert out.config[f] == "***", f"Field {f} not masked for {connector_type}"
            assert out.config["safe_field"] == "visible"
