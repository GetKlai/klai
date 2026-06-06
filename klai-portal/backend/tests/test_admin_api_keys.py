"""Smoke tests for admin API keys endpoints — SPEC-WIDGET-002.

Verifies the endpoint module imports, Pydantic schemas validate,
and the helper functions exist. Full integration tests require a
running database and are covered by CI's pytest-integration suite.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from helpers import FakeResult, setup_db


def test_module_imports():
    """All admin_api_keys symbols import without error."""
    from app.api.admin_api_keys import (
        create_api_key,
        delete_api_key,
        get_api_key_detail,
        list_api_keys,
        rotate_api_key,
        router,
        update_api_key,
    )

    assert router.prefix == "/api/admin/api-keys"
    assert callable(create_api_key)
    assert callable(list_api_keys)
    assert callable(get_api_key_detail)
    assert callable(update_api_key)
    assert callable(rotate_api_key)
    assert callable(delete_api_key)


def test_create_request_schema_validates():
    """CreateApiKeyRequest accepts valid input."""
    from app.api.admin_api_keys import CreateApiKeyRequest, KbAccessEntry

    req = CreateApiKeyRequest(
        name="Test Key",
        description="Test",
        permissions={"chat": True, "feedback": False, "knowledge_append": False},
        kb_access=[KbAccessEntry(kb_id=1, access_level="read")],
        rate_limit_rpm=60,
    )
    assert req.name == "Test Key"
    assert req.rate_limit_rpm == 60


def test_create_request_rejects_short_name():
    """CreateApiKeyRequest rejects names shorter than 3 chars."""
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
    assert "rotated_from_key_id" in fields
    assert "rotated_to_key_id" in fields
    assert "rotation_started_at" in fields


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


@pytest.mark.asyncio
async def test_validate_kb_ids_allows_callers_own_personal_kb():
    """API keys may be scoped to the caller's own personal KB."""
    from app.api.admin_api_keys import _validate_kb_ids

    personal_kb = SimpleNamespace(id=7, owner_type="user", owner_user_id="user-1")
    db = AsyncMock()
    setup_db(db, [FakeResult([personal_kb])])

    result = await _validate_kb_ids([7], org_id=1, user_id="user-1", db=db)

    assert result == [personal_kb]


@pytest.mark.asyncio
async def test_validate_kb_ids_rejects_other_users_personal_kb():
    """API keys must never be granted access to another user's personal KB."""
    from app.api.admin_api_keys import _validate_kb_ids

    other_personal_kb = SimpleNamespace(id=8, owner_type="user", owner_user_id="user-2")
    db = AsyncMock()
    setup_db(db, [FakeResult([other_personal_kb])])

    with pytest.raises(HTTPException) as exc:
        await _validate_kb_ids([8], org_id=1, user_id="user-1", db=db)

    assert exc.value.status_code == 400
    assert "not owned by the caller" in exc.value.detail
