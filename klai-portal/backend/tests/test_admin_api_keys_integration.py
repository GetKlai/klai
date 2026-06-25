"""Integration tests for admin API keys endpoints — SPEC-WIDGET-002.

Tests the full endpoint flow with mocked auth + DB. Verifies that:
- create issues a pk_live_ key and returns it
- list returns all keys for the org
- delete removes the key
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import make_perms
from fastapi import HTTPException
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
        permissions={"chat": False, "feedback": False, "knowledge_append": False, "general_chat": True},
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
async def test_create_api_key_rejects_knowledge_chat_without_kb_access():
    """Knowledge-grounded chat keys must be scoped to at least one KB."""
    from app.api.admin_api_keys import CreateApiKeyRequest, create_api_key

    db = AsyncMock()
    body = CreateApiKeyRequest(
        name="Knowledge Key",
        permissions={"chat": True, "feedback": False, "knowledge_append": False, "general_chat": False},
        kb_access=[],
        rate_limit_rpm=60,
    )

    with pytest.raises(HTTPException) as exc:
        await create_api_key(
            body=body,
            perms=make_perms(role="admin", user_id="user-1", org_id=1),
            db=db,
        )

    assert exc.value.status_code == 400
    assert "requires at least one knowledge base" in exc.value.detail
    db.commit.assert_not_awaited()


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
async def test_get_api_key_detail_redacts_other_users_personal_kb_metadata():
    """Detail may preserve access rows without leaking another user's personal KB name/slug."""
    from app.api.admin_api_keys import get_api_key_detail

    key = FakeKeyRow(id="key-1", created_by="user-2")
    access = FakeKbAccessRow(partner_api_key_id="key-1", kb_id=123, access_level="read")
    other_personal_kb = SimpleNamespace(
        id=123,
        name="Jelle private notes",
        slug="personal-user-2",
        owner_type="user",
        owner_user_id="user-2",
    )
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([key]),  # SELECT key
            FakeResult([(access, other_personal_kb)]),  # SELECT access + KB
        ],
    )

    result = await get_api_key_detail(
        key_id="key-1",
        perms=make_perms(role="admin", user_id="user-1", org_id=1),
        db=db,
    )

    assert result.kb_access == [
        {
            "kb_id": 123,
            "kb_name": "Personal knowledge base",
            "kb_slug": None,
            "access_level": "read",
        }
    ]


@pytest.mark.asyncio
async def test_update_api_key_rejects_personal_kb_on_key_created_by_other_user():
    """A caller's personal KB cannot be attached to another user's API key."""
    from app.api.admin_api_keys import KbAccessEntry, UpdateApiKeyRequest, update_api_key

    key = FakeKeyRow(id="key-1", created_by="user-2")
    own_personal_kb = SimpleNamespace(id=123, owner_type="user", owner_user_id="user-1")
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([key]),  # SELECT key
            FakeResult([own_personal_kb]),  # validate selected KB ownership
            FakeResult(),  # would delete old access if validation failed open
        ],
    )
    body = UpdateApiKeyRequest(kb_access=[KbAccessEntry(kb_id=123, access_level="read")])

    with pytest.raises(HTTPException) as exc:
        await update_api_key(
            key_id="key-1",
            body=body,
            perms=make_perms(role="admin", user_id="user-1", org_id=1),
            db=db,
        )

    assert exc.value.status_code == 400
    assert "created by the caller" in exc.value.detail
    assert db.execute.await_count == 2
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_api_key_allows_adding_general_chat_to_legacy_key_without_kb():
    """Legacy no-KB chat keys can still be edited when chat itself is unchanged."""
    from app.api.admin_api_keys import UpdateApiKeyRequest, update_api_key

    key = FakeKeyRow(
        id="key-1",
        permissions={"chat": True, "feedback": False, "knowledge_append": False, "general_chat": False},
    )
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([key]),  # SELECT key
            FakeResult([]),  # current KB access rows
            FakeResult(scalar_value=0),  # count KB access for response
        ],
    )
    body = UpdateApiKeyRequest(
        permissions={"chat": True, "feedback": False, "knowledge_append": False, "general_chat": True}
    )

    with patch("app.api.admin_api_keys.emit_event"):
        result = await update_api_key(
            key_id="key-1",
            body=body,
            perms=make_perms(role="admin", user_id="user-1", org_id=1),
            db=db,
        )

    assert result.permissions["general_chat"] is True
    db.commit.assert_awaited_once()


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
    org_kb = SimpleNamespace(id=123, owner_type="org", owner_user_id=None)
    db = AsyncMock()
    db.add = MagicMock()
    rotated_to_seen_at_flush: list[str | None] = []

    async def fake_flush():
        rotated_to_seen_at_flush.append(source_key.rotated_to_key_id)

    db.flush = AsyncMock(side_effect=fake_flush)

    async def fake_refresh(row):
        row.created_at = datetime(2026, 1, 1, tzinfo=UTC)

    db.refresh = AsyncMock(side_effect=fake_refresh)
    setup_db(
        db,
        [
            FakeResult([source_key]),  # SELECT source key
            FakeResult([kb_access]),  # SELECT source KB access rows
            FakeResult([org_kb]),  # validate cloned KB access ownership
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
    assert rotated_to_seen_at_flush == [None, result.id]
    db.commit.assert_awaited_once()
    assert db.add.call_count == 2  # new key row + cloned KB access row


@pytest.mark.asyncio
async def test_rotate_api_key_rejects_other_users_personal_kb_access():
    """Rotation must not mint a new plaintext key for another user's personal KB."""
    from app.api.admin_api_keys import rotate_api_key

    source_key = FakeKeyRow(id="key-old", created_by="user-2")
    kb_access = FakeKbAccessRow(partner_api_key_id="key-old", kb_id=123, access_level="read")
    other_personal_kb = SimpleNamespace(id=123, owner_type="user", owner_user_id="user-2")
    db = AsyncMock()
    db.add = MagicMock()
    setup_db(
        db,
        [
            FakeResult([source_key]),  # SELECT source key
            FakeResult([kb_access]),  # SELECT source KB access rows
            FakeResult([other_personal_kb]),  # validate cloned KB access ownership
        ],
    )

    with pytest.raises(HTTPException) as exc:
        await rotate_api_key(
            key_id="key-old",
            perms=make_perms(role="admin", user_id="user-1", org_id=1),
            db=db,
        )

    assert exc.value.status_code == 400
    assert "not owned by the caller" in exc.value.detail
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
