"""Integration tests for admin API keys endpoints — SPEC-WIDGET-002.

Tests the full endpoint flow with mocked auth + DB. Verifies that:
- create issues a pk_live_ key and returns it
- list returns all keys for the org
- delete removes the key
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import make_perms
from helpers import FakeResult, setup_db


@dataclass
class FakeKeyRow:
    id: str = "key-uuid-1"
    org_id: int = 1
    name: str = "Test Key"
    description: str | None = None
    key_prefix: str = "pk_live_1234"
    key_hash: str = "abc123"
    permissions: dict = field(default_factory=lambda: {"chat": True, "feedback": False, "knowledge_append": False})
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "user-1"
    rotated_from_key_id: str | None = None
    rotated_to_key_id: str | None = None
    rotation_started_at: datetime | None = None


@dataclass
class FakeKbAccessRow:
    partner_api_key_id: str = "key-uuid-1"
    kb_id: int = 10
    access_level: str = "read"


@pytest.mark.asyncio
async def test_create_api_key_returns_plaintext_key():
    """POST /api/admin/api-keys returns api_key (plaintext) in response."""
    from app.api.admin_api_keys import CreateApiKeyRequest, create_api_key

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    # refresh sets server-generated created_at
    async def fake_refresh(row):
        row.created_at = datetime(2026, 1, 1, tzinfo=UTC)

    db.refresh = AsyncMock(side_effect=fake_refresh)

    body = CreateApiKeyRequest(
        name="Test Key",
        permissions={"chat": True, "feedback": False, "knowledge_append": False},
        kb_access=[],
        rate_limit_rpm=60,
    )

    with patch("app.api.admin_api_keys.emit_event"):
        result = await create_api_key(
            body=body,
            perms=make_perms(role="admin", user_id="user-1", org_id=1),
            db=db,
        )

    assert result.api_key.startswith("pk_live_")
    assert len(result.api_key) > 20
    assert result.name == "Test Key"
    assert result.key_prefix == result.api_key[:12]
    db.add.assert_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_api_keys_returns_org_keys():
    """GET /api/admin/api-keys returns all keys for the org."""
    from app.api.admin_api_keys import list_api_keys

    db = AsyncMock()
    key1 = FakeKeyRow(id="key-1", name="First")
    key2 = FakeKeyRow(id="key-2", name="Second")
    setup_db(
        db,
        [
            FakeResult([key1, key2]),  # SELECT PartnerAPIKey
            FakeResult(),  # COUNT kb_access (no rows)
        ],
    )

    result = await list_api_keys(
        perms=make_perms(role="admin", user_id="user-1", org_id=1),
        db=db,
    )

    assert len(result) == 2
    assert result[0].name == "First"
    assert result[1].name == "Second"


@pytest.mark.asyncio
async def test_delete_api_key_calls_db_delete():
    """DELETE /api/admin/api-keys/{id} executes DELETE on DB."""
    from app.api.admin_api_keys import delete_api_key

    key = FakeKeyRow(id="key-1")
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([key]),  # SELECT key
            FakeResult(),  # DELETE kb_access
            FakeResult(),  # DELETE key
        ],
    )

    with patch("app.api.admin_api_keys.emit_event"):
        await delete_api_key(
            key_id="key-1",
            perms=make_perms(role="admin", user_id="user-1", org_id=1),
            db=db,
        )

    db.commit.assert_awaited_once()
    # execute called 3 times: SELECT + 2x DELETE
    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_rotate_api_key_clones_permissions_and_kb_access():
    """POST /api/admin/api-keys/{id}/rotate creates a replacement key."""
    from app.api.admin_api_keys import rotate_api_key

    source_key = FakeKeyRow(
        id="key-old",
        name="Production API",
        description="Current prod key",
        permissions={"chat": True, "feedback": True, "knowledge_append": False},
        rate_limit_rpm=120,
    )
    kb_access = FakeKbAccessRow(partner_api_key_id="key-old", kb_id=123, access_level="read_write")
    db = AsyncMock()
    db.add = MagicMock()

    async def fake_refresh(row):
        row.created_at = datetime(2026, 1, 1, tzinfo=UTC)

    db.refresh = AsyncMock(side_effect=fake_refresh)
    setup_db(
        db,
        [
            FakeResult([source_key]),  # SELECT source key
            FakeResult([kb_access]),  # SELECT source KB access rows
        ],
    )

    with patch("app.api.admin_api_keys.emit_event"):
        result = await rotate_api_key(
            key_id="key-old",
            perms=make_perms(role="admin", user_id="user-2", org_id=1),
            db=db,
        )

    assert result.api_key.startswith("pk_live_")
    assert result.old_key_id == "key-old"
    assert result.rotated_from_key_id == "key-old"
    assert result.permissions == source_key.permissions
    assert result.rate_limit_rpm == 120
    assert result.kb_access_count == 1
    assert source_key.rotated_to_key_id == result.id
    assert source_key.rotation_started_at is not None
    db.commit.assert_awaited_once()
    assert db.add.call_count == 2  # new key row + cloned KB access row
