"""Smoke tests for admin API keys endpoints — SPEC-WIDGET-002.

Verifies the endpoint module imports, Pydantic schemas validate,
and the helper functions exist. Full integration tests require a
running database and are covered by CI's pytest-integration suite.
"""

from unittest.mock import MagicMock


def test_module_imports():
    """All admin_api_keys symbols import without error."""
    from app.api.admin_api_keys import (
        create_api_key,
        delete_api_key,
        get_api_key_detail,
        list_api_keys,
        router,
        update_api_key,
    )

    assert router.prefix == "/api/admin/api-keys"
    assert callable(create_api_key)
    assert callable(list_api_keys)
    assert callable(get_api_key_detail)
    assert callable(update_api_key)
    assert callable(delete_api_key)


def test_create_request_schema_validates():
    """CreateApiKeyRequest accepts valid input."""
    from app.api.admin_api_keys import CreateApiKeyRequest

    req = CreateApiKeyRequest(
        name="Test Key",
        description="Test",
        permissions={"chat": True, "feedback": False, "knowledge_append": False},
        kb_access=[{"kb_id": 1, "access_level": "read"}],
        rate_limit_rpm=60,
    )
    assert req.name == "Test Key"
    assert req.rate_limit_rpm == 60


def test_create_request_rejects_short_name():
    """CreateApiKeyRequest rejects names shorter than 3 chars."""
    import pytest

    from app.api.admin_api_keys import CreateApiKeyRequest

    with pytest.raises(ValueError):
        CreateApiKeyRequest(
            name="ab",
            permissions={"chat": True},
            kb_access=[],
        )


def test_response_schema():
    """ApiKeyResponse has no widget-specific fields."""
    from app.api.admin_api_keys import ApiKeyResponse

    fields = set(ApiKeyResponse.model_fields.keys())
    assert "widget_id" not in fields
    assert "widget_config" not in fields
    assert "integration_type" not in fields
    assert "active" not in fields
    assert "key_prefix" in fields
    assert "permissions" in fields


def test_key_to_response_helper():
    """_key_to_response produces correct output."""
    from app.api.admin_api_keys import _key_to_response

    key = MagicMock()
    key.id = "uuid-1"
    key.name = "Test"
    key.description = None
    key.key_prefix = "pk_live_1234"
    key.permissions = {"chat": True}
    key.rate_limit_rpm = 60
    key.last_used_at = None
    key.created_at = "2026-01-01"
    key.created_by = "user-1"

    resp = _key_to_response(key, kb_access_count=2)
    assert resp.name == "Test"
    assert resp.kb_access_count == 2
    assert resp.key_prefix == "pk_live_1234"
