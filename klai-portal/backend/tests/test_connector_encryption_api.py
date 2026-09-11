"""Tests for connector credential encryption in API layer.

Verifies that:
- _connector_out rejects plaintext sensitive fields instead of masking them
- The internal get_connector_config decrypts and merges credentials
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.connectors import (
    _connector_out,
    _cookie_identities_from_credentials,
    _drop_stale_credentials_on_origin_change,
    _merge_saved_sensitive_credentials,
)
from app.services.connector_credentials import SENSITIVE_FIELDS

# Test-only placeholder values (NOT real credentials)
FAKE_TOKEN = "test-placeholder-value"


class TestConnectorOutPlaintextSecretRejection:
    """_connector_out must fail closed when storage still contains plaintext secrets."""

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
        c.encrypted_credentials = None
        return c

    @pytest.mark.parametrize("connector_type,fields", sorted(SENSITIVE_FIELDS.items()))
    def test_sensitive_fields_rejected(self, connector_type: str, fields: list[str]) -> None:
        config = {field: FAKE_TOKEN for field in fields}
        config["safe_field"] = "visible"
        with pytest.raises(RuntimeError, match="plaintext sensitive fields"):
            _connector_out(self._make_connector(connector_type, config))

    def test_github_sensitive_fields_rejected(self) -> None:
        config = {
            "repo": "GetKlai/klai",
            "access_token": FAKE_TOKEN,
            "installation_token": FAKE_TOKEN,
            "app_private_key": FAKE_TOKEN,
        }
        with pytest.raises(RuntimeError, match="plaintext sensitive fields"):
            _connector_out(self._make_connector("github", config))

    def test_notion_sensitive_fields_rejected(self) -> None:
        config = {"workspace_id": "ws-123", "access_token": FAKE_TOKEN}
        with pytest.raises(RuntimeError, match="plaintext sensitive fields"):
            _connector_out(self._make_connector("notion", config))

    def test_web_crawler_sensitive_fields_rejected(self) -> None:
        config = {"url": "https://example.com", "auth_headers": FAKE_TOKEN}
        with pytest.raises(RuntimeError, match="plaintext sensitive fields"):
            _connector_out(self._make_connector("web_crawler", config))

    def test_confluence_api_token_rejected(self) -> None:
        config = {
            "base_url": "https://example.atlassian.net/wiki",
            "email": "admin@example.com",
            "api_token": FAKE_TOKEN,
        }
        with pytest.raises(RuntimeError, match="plaintext sensitive fields"):
            _connector_out(self._make_connector("confluence", config))

    def test_unknown_type_no_secret_contract(self) -> None:
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

    def test_encrypted_credentials_reported_without_exposing_secret(self) -> None:
        connector = self._make_connector("web_crawler", {"url": "https://example.com"})
        connector.encrypted_credentials = b"encrypted-placeholder"
        out = _connector_out(connector)
        assert out.has_saved_credentials is True
        assert "cookies" not in out.config

    def test_encrypted_json_feed_url_is_not_returned_by_public_api(self) -> None:
        connector = self._make_connector("json_feed", {})
        connector.encrypted_credentials = b"encrypted-placeholder"

        out = _connector_out(connector)

        assert out.config == {}
        assert out.has_saved_credentials is True


def test_cookie_identities_carry_no_values() -> None:
    identities = _cookie_identities_from_credentials(
        {
            "cookies": [
                {"name": "prod-knowledgebase-session", "value": FAKE_TOKEN},
                {"name": "XSRF-TOKEN", "value": FAKE_TOKEN},
                {"name": "XSRF-TOKEN", "value": "duplicate"},
            ]
        }
    )

    assert [c.name for c in identities] == ["prod-knowledgebase-session", "XSRF-TOKEN"]
    assert FAKE_TOKEN not in str(identities)


def test_two_cookies_sharing_a_name_across_paths_both_survive() -> None:
    """Deduplicating on the name alone made one of them unreachable from the
    form, and a partial replacement then dropped it."""
    identities = _cookie_identities_from_credentials(
        {
            "cookies": [
                {"name": "sess", "domain": "w.example.com", "path": "/", "value": FAKE_TOKEN},
                {"name": "sess", "domain": "w.example.com", "path": "/admin", "value": "other"},
            ]
        }
    )

    assert [(c.name, c.path) for c in identities] == [("sess", "/"), ("sess", "/admin")]


@pytest.mark.asyncio
async def test_merge_saved_sensitive_credentials_fills_omitted_confluence_token() -> None:
    connector = MagicMock()
    connector.connector_type = "confluence"
    connector.encrypted_credentials = b"ENCRYPTED"

    with patch("app.api.connectors.credential_store") as mock_store:
        mock_store.decrypt_credentials = AsyncMock(return_value={"api_token": FAKE_TOKEN})

        merged = await _merge_saved_sensitive_credentials(
            connector=connector,
            config={
                "base_url": "https://example.atlassian.net/wiki",
                "email": "admin@example.com",
                "space_keys": ["ENG"],
            },
            org_id=77,
            db=AsyncMock(),
        )

    assert merged == {
        "base_url": "https://example.atlassian.net/wiki",
        "email": "admin@example.com",
        "space_keys": ["ENG"],
        "api_token": FAKE_TOKEN,
    }


@pytest.mark.asyncio
async def test_merge_saved_sensitive_credentials_preserves_omitted_json_feed_url() -> None:
    connector = MagicMock()
    connector.connector_type = "json_feed"
    connector.encrypted_credentials = b"ENCRYPTED"
    saved_url = "https://data.example.com/feed.json?token=placeholder"

    with patch("app.api.connectors.credential_store") as mock_store:
        mock_store.decrypt_credentials = AsyncMock(return_value={"url": saved_url})

        merged = await _merge_saved_sensitive_credentials(
            connector=connector,
            config={},
            org_id=77,
            db=AsyncMock(),
        )

    assert merged == {"url": saved_url}


@pytest.mark.asyncio
async def test_merge_saved_sensitive_credentials_drops_cookies_on_origin_change() -> None:
    """Regression: PATCHing base_url to a different origin must not carry the
    old cookies forward into this save - they were captured for the old site.
    Without this, a connector-manage user could repoint base_url and have the
    real site's decrypted cookies sent to the new origin on the next probe.
    """
    connector = MagicMock()
    connector.connector_type = "web_crawler"
    connector.encrypted_credentials = b"ENCRYPTED"
    connector.config = {"base_url": "https://wiki.redcactus.cloud/nl/"}

    with patch("app.api.connectors.credential_store") as mock_store:
        mock_store.decrypt_credentials = AsyncMock(return_value={"cookies": [{"name": "session", "value": FAKE_TOKEN}]})

        merged = await _merge_saved_sensitive_credentials(
            connector=connector,
            config={"base_url": "https://attacker.example.com/"},
            org_id=8,
            db=AsyncMock(),
        )

    assert "cookies" not in merged


@pytest.mark.asyncio
async def test_merge_saved_sensitive_credentials_keeps_cookies_on_same_origin_path_change() -> None:
    """A base_url edit that stays on the same origin (e.g. adding a path)
    must still carry the saved cookies forward - only a different origin
    invalidates them."""
    connector = MagicMock()
    connector.connector_type = "web_crawler"
    connector.encrypted_credentials = b"ENCRYPTED"
    connector.config = {"base_url": "https://wiki.redcactus.cloud/nl/"}
    cookies = [{"name": "session", "value": FAKE_TOKEN}]

    with patch("app.api.connectors.credential_store") as mock_store:
        mock_store.decrypt_credentials = AsyncMock(return_value={"cookies": cookies})

        merged = await _merge_saved_sensitive_credentials(
            connector=connector,
            config={"base_url": "https://wiki.redcactus.cloud/nl/login"},
            org_id=8,
            db=AsyncMock(),
        )

    assert merged["cookies"] == cookies


class TestDropStaleCredentialsOnOriginChange:
    """Regression for the same gap, at the point that actually clears storage."""

    def _connector(self, *, base_url: str) -> MagicMock:
        connector = MagicMock()
        connector.connector_type = "web_crawler"
        connector.encrypted_credentials = b"ENCRYPTED"
        connector.config = {"base_url": base_url}
        return connector

    def test_clears_on_origin_change_without_fresh_cookies(self) -> None:
        connector = self._connector(base_url="https://wiki.redcactus.cloud/nl/")
        _drop_stale_credentials_on_origin_change(
            connector=connector,
            new_config={"base_url": "https://attacker.example.com/"},
            clear_credentials=False,
        )
        assert connector.encrypted_credentials is None

    def test_keeps_credentials_when_fresh_cookies_supplied(self) -> None:
        connector = self._connector(base_url="https://wiki.redcactus.cloud/nl/")
        _drop_stale_credentials_on_origin_change(
            connector=connector,
            new_config={
                "base_url": "https://attacker.example.com/",
                "cookies": [{"name": "session", "value": FAKE_TOKEN}],
            },
            clear_credentials=False,
        )
        assert connector.encrypted_credentials == b"ENCRYPTED"

    def test_keeps_credentials_on_same_origin(self) -> None:
        connector = self._connector(base_url="https://wiki.redcactus.cloud/nl/")
        _drop_stale_credentials_on_origin_change(
            connector=connector,
            new_config={"base_url": "https://wiki.redcactus.cloud/nl/login"},
            clear_credentials=False,
        )
        assert connector.encrypted_credentials == b"ENCRYPTED"
