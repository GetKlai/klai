"""Regression tests for platform-admin cross-tenant widget conversation reads.

The platform console must let Klai staff read ANY tenant's webchat
conversations via /platform/bots/{widget_id}/conversations*, gated solely on
require_platform_admin() — an ordinary tenant admin gets 403, never another
tenant's transcript.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from tests.conftest import make_perms

WIDGET_UUID = "5112f9ad-3768-4b76-9a13-5b74e9165bc3"


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TextRows:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


def _platform_perms():
    return make_perms(
        role="admin",
        user_id="platform-admin",
        org_id=1,
        org_slug="getklai",
        is_platform_admin=True,
    )


def _tenant_admin_perms():
    """Ordinary tenant admin: ADMIN role, own org 42, NOT a platform admin."""
    return make_perms(
        role="admin",
        user_id="tenant-admin",
        org_id=42,
        org_slug="voys",
        is_platform_admin=False,
    )


def _conv_row():
    now = datetime(2026, 9, 1, 12, 0, 0)
    return SimpleNamespace(
        id=7,
        started_at=now,
        last_message_at=now,
        message_count=2,
        first_user_query="Wat kost Klai?",
        language_detected="nl",
    )


@pytest.mark.asyncio
async def test_platform_admin_reads_other_org_conversation_list_without_org_filter() -> None:
    from app.api.admin.platform import platform_bot_conversations

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            TextRows([SimpleNamespace(id=WIDGET_UUID, org_id=42)]),
            TextRows([_conv_row()]),
        ]
    )

    with (
        patch("app.api.admin.platform.cross_org_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform._audit", new=AsyncMock()) as audit,
    ):
        result = await platform_bot_conversations(widget_id=WIDGET_UUID, cursor=None, limit=20, perms=_platform_perms())

    # Cross-tenant read: org 42's conversation served to a platform-admin in org 1.
    assert [c.id for c in result] == [7]
    assert result[0].first_user_query == "Wat kost Klai?"
    # The platform query must NOT carry an org restriction — the platform-admin
    # gate plus cross_org_session() is the only boundary (mirrors platform_bots).
    for call in db.execute.await_args_list:
        assert "org_id" not in str(call.args[0])
    # Every cross-tenant read writes an audit event, no exceptions.
    audit.assert_awaited_once()
    assert audit.await_args.args[1:] == ("bot-conversations", WIDGET_UUID)


@pytest.mark.asyncio
async def test_platform_admin_reads_conversation_transcript_with_rating() -> None:
    from app.api.admin.platform import platform_bot_conversation

    now = datetime(2026, 9, 1, 12, 0, 0)
    msg_rows = [
        SimpleNamespace(
            id=1, role="user", content="Wat kost Klai?", sources=None, created_at=now, sequence=1, rating=None
        ),
        SimpleNamespace(
            id=2,
            role="assistant",
            content="Dat hangt van het plan af.",
            sources=[{"label": "1", "title": "Prijzen", "url": "https://getklai.com/prijzen"}],
            created_at=now,
            sequence=2,
            rating="thumbsUp",
        ),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            TextRows([SimpleNamespace(id=WIDGET_UUID, org_id=42)]),
            TextRows([_conv_row()]),
            TextRows(msg_rows),
        ]
    )

    with (
        patch("app.api.admin.platform.cross_org_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform._audit", new=AsyncMock()),
    ):
        detail = await platform_bot_conversation(widget_id=WIDGET_UUID, conv_id=7, perms=_platform_perms())

    assert detail.id == 7
    assert [m.id for m in detail.messages] == [1, 2]
    assert detail.messages[1].rating == "thumbsUp"
    assert detail.messages[1].sources[0]["url"] == "https://getklai.com/prijzen"


@pytest.mark.asyncio
async def test_tenant_admin_gets_403_on_platform_conversation_endpoints() -> None:
    from app.api.admin.platform import router
    from app.core.permissions import get_caller

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_caller] = _tenant_admin_perms

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get(f"/platform/bots/{WIDGET_UUID}/conversations")
        detail = await client.get(f"/platform/bots/{WIDGET_UUID}/conversations/7")

    assert listing.status_code == 403
    assert detail.status_code == 403
