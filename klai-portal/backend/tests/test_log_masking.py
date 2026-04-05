"""Tests for SecretStr log masking in structlog processors.

Verifies that the mask_secret_str processor replaces SecretStr values
with '***' in all structlog event dicts, and that non-sensitive kwargs
pass through unchanged.
"""

from pydantic import SecretStr

from app.logging_setup import mask_secret_str


class TestMaskSecretStrProcessor:
    """mask_secret_str structlog processor."""

    def test_secret_str_value_is_masked(self) -> None:
        event_dict = {"event": "test", "api_key": SecretStr("ghp_abc123")}
        result = mask_secret_str(None, None, event_dict)
        assert result["api_key"] == "***"

    def test_non_sensitive_kwargs_unchanged(self) -> None:
        event_dict = {"event": "test", "connector_id": "abc-123", "count": 42}
        result = mask_secret_str(None, None, event_dict)
        assert result["connector_id"] == "abc-123"
        assert result["count"] == 42

    def test_multiple_secret_str_values_masked(self) -> None:
        event_dict = {
            "event": "test",
            "token": SecretStr("secret1"),
            "password": SecretStr("secret2"),
            "name": "safe",
        }
        result = mask_secret_str(None, None, event_dict)
        assert result["token"] == "***"
        assert result["password"] == "***"
        assert result["name"] == "safe"

    def test_event_field_not_masked_if_not_secret_str(self) -> None:
        event_dict = {"event": "user logged in", "user_id": "u123"}
        result = mask_secret_str(None, None, event_dict)
        assert result["event"] == "user logged in"

    def test_secret_str_in_event_field_is_masked(self) -> None:
        """Edge case: even the 'event' key gets masked if it is a SecretStr."""
        event_dict = {"event": SecretStr("should not happen")}
        result = mask_secret_str(None, None, event_dict)
        assert result["event"] == "***"

    def test_nested_dict_secret_str_not_deeply_masked(self) -> None:
        """Processor masks top-level values only (structlog convention)."""
        inner = {"key": SecretStr("nested")}
        event_dict = {"event": "test", "config": inner}
        result = mask_secret_str(None, None, event_dict)
        # Top-level config is not a SecretStr, so it passes through as-is
        assert result["config"] is inner


class TestPydanticModelSecretStrRepr:
    """Pydantic models with SecretStr hide values in repr/str."""

    def test_secret_str_repr_hides_value(self) -> None:
        secret = SecretStr("super-secret")
        assert "super-secret" not in repr(secret)
        assert "super-secret" not in str(secret)
