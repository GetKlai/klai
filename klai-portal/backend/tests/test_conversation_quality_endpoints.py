"""Regression tests for the per-conversation quality-judgment read endpoints.

SPEC-CHAT-QUALITY-LOOP-001 REQ-3: the nightly judge (REQ-2) writes one row
per conversation into ``conversation_quality_judgments``; the tenant admin
(`/api/admin/widgets/{widget_id}/conversations/{conv_id}/quality`) and the
platform console
(`/api/admin/platform/bots/{widget_id}/conversations/{conv_id}/quality`)
expose it as a read-only sidecar. A missing row is a 404 — "not judged yet"
is a normal state the frontend renders as nothing, never an error dialog.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException

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

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


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


def _judgment_row():
    return SimpleNamespace(
        org_id=42,
        outcome="escalated",
        failure_category="retrieval_miss",
        reasoning="De bot kende het actuele prijsplan niet.",
        confidence="high",
        suggested_action="Prijs-KB opnieuw in de index opnemen.",
        judged_at=datetime(2026, 9, 11, 2, 0, tzinfo=UTC),
    )


def _widget_row():
    return SimpleNamespace(id=WIDGET_UUID, org_id=42)


@pytest.mark.asyncio
async def test_tenant_quality_route_returns_judgment_fields() -> None:
    from app.api.admin_widgets import get_widget_conversation_quality

    row = _judgment_row()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[TextRows([_widget_row()]), TextRows([row])])

    judgment = await get_widget_conversation_quality(
        widget_id=WIDGET_UUID, conv_id=7, perms=_tenant_admin_perms(), db=db
    )

    assert judgment.outcome == "escalated"
    assert judgment.failure_category == "retrieval_miss"
    assert judgment.reasoning == "De bot kende het actuele prijsplan niet."
    assert judgment.confidence == "high"
    assert judgment.suggested_action == "Prijs-KB opnieuw in de index opnemen."
    assert judgment.judged_at == row.judged_at


@pytest.mark.asyncio
async def test_tenant_quality_route_404_when_conversation_not_judged() -> None:
    from app.api.admin_widgets import get_widget_conversation_quality

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[TextRows([_widget_row()]), TextRows([])])

    with pytest.raises(HTTPException) as exc:
        await get_widget_conversation_quality(
            widget_id=WIDGET_UUID, conv_id=7, perms=_tenant_admin_perms(), db=db
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_platform_quality_route_reads_other_org_judgment() -> None:
    from app.api.admin.platform import platform_bot_conversation_quality

    # Judgment belongs to org 42; the platform admin lives in org 1.
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[TextRows([_widget_row()]), TextRows([_judgment_row()])])

    with (
        patch("app.api.admin.platform.cross_org_session", return_value=AsyncContext(db)),
        patch("app.api.admin.platform._audit", new=AsyncMock()) as audit,
    ):
        judgment = await platform_bot_conversation_quality(
            widget_id=WIDGET_UUID, conv_id=7, perms=_platform_perms()
        )

    assert judgment.outcome == "escalated"
    assert judgment.reasoning == "De bot kende het actuele prijsplan niet."
    # Cross-tenant read: the query must NOT carry an org restriction — the
    # platform-admin gate plus cross_org_session() is the only boundary.
    for call in db.execute.await_args_list:
        assert "org_id" not in str(call.args[0])
    audit.assert_awaited_once()
    assert audit.await_args.args[1:] == ("bot-conversations", WIDGET_UUID)


@pytest.mark.asyncio
async def test_tenant_admin_gets_403_on_platform_quality_route() -> None:
    from app.api.admin.platform import router
    from app.core.permissions import get_caller

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_caller] = _tenant_admin_perms

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/platform/bots/{WIDGET_UUID}/conversations/7/quality")

    assert resp.status_code == 403
