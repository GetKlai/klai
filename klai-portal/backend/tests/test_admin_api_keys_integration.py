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
from helpers import FakeResult, setup_db


@dataclass
class FakeOrg:
    id: int = 1
    zitadel_org_id: str = "zit-org-1"


@dataclass
class FakeUser:
    role: str = "admin"
    zitadel_user_id: str = "user-1"


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


def _mock_auth():
    """Patch _get_caller_org + _require_admin to bypass auth."""
    return (
        patch(
            "app.api.admin_api_keys._get_caller_org",
            new=AsyncMock(return_value=("user-1", FakeOrg(), FakeUser())),
        ),
        patch("app.api.admin_api_keys._require_admin"),
    )


@pytest.mark.asyncio
async def test_create_api_key_returns_plaintext_key():
    """POST /api/api-keys returns api_key (plaintext) in response."""
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

    with _mock_auth()[0], _mock_auth()[1], patch("app.api.admin_api_keys.emit_event"):
        result = await create_api_key(
            body=body,
            credentials=MagicMock(credentials="fake-token"),
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
    """GET /api/api-keys returns all keys for the org."""
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

    with _mock_auth()[0], _mock_auth()[1]:
        result = await list_api_keys(
            credentials=MagicMock(credentials="fake-token"),
            db=db,
        )

    assert len(result) == 2
    assert result[0].name == "First"
    assert result[1].name == "Second"


@pytest.mark.asyncio
async def test_delete_api_key_calls_db_delete():
    """DELETE /api/api-keys/{id} executes DELETE on DB."""
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

    with _mock_auth()[0], _mock_auth()[1], patch("app.api.admin_api_keys.emit_event"):
        await delete_api_key(
            key_id="key-1",
            credentials=MagicMock(credentials="fake-token"),
            db=db,
        )

    db.commit.assert_awaited_once()
    # execute called 3 times: SELECT + 2x DELETE
    assert db.execute.await_count == 3
