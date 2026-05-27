"""Integration tests for admin widgets endpoints — SPEC-WIDGET-002.

Tests the full endpoint flow with mocked auth + DB. Verifies that:
- create generates a wgt_ widget_id (no pk_live_ key)
- list returns all widgets for the org
- update patches widget_config
- delete removes the widget
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import make_perms
from helpers import FakeResult, setup_db


@dataclass
class FakeWidgetRow:
    id: str = "widget-uuid-1"
    org_id: int = 1
    name: str = "Help Bot"
    description: str | None = None
    widget_id: str = "wgt_abc123def456"
    widget_config: dict = field(
        default_factory=lambda: {
            "allowed_origins": ["https://example.com"],
            "title": "Help",
            "welcome_message": "Hi!",
            "css_variables": {},
        }
    )
    public_share_enabled: bool = False
    allow_any_origin: bool = False
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "user-1"
    deleted_at: datetime | None = None  # REQ-16 soft-delete


@pytest.mark.asyncio
async def test_create_widget_returns_wgt_id_no_api_key():
    """POST /api/admin/widgets returns widget_id (wgt_...) and NO api_key field."""
    from app.api.admin_widgets import CreateWidgetRequest, WidgetConfig, create_widget

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    async def fake_refresh(row):
        row.created_at = datetime(2026, 1, 1, tzinfo=UTC)

    db.refresh = AsyncMock(side_effect=fake_refresh)
    # KB lookup for response
    setup_db(db, [FakeResult()])

    body = CreateWidgetRequest(
        name="Help Bot",
        kb_ids=[],
        widget_config=WidgetConfig(
            allowed_origins=["https://example.com"],
            title="Help",
            welcome_message="Hi!",
        ),
    )

    with patch("app.api.admin_widgets.emit_event"):
        result = await create_widget(
            body=body,
            perms=make_perms(role="admin", user_id="user-1", org_id=1),
            db=db,
        )

    assert result.widget_id.startswith("wgt_")
    assert not hasattr(result, "api_key")
    assert result.name == "Help Bot"
    assert result.widget_config.title == "Help"
    assert result.public_share_enabled is False
    db.add.assert_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_widgets_returns_org_widgets():
    """GET /api/admin/widgets returns all widgets for the org."""
    from app.api.admin_widgets import list_widgets

    w1 = FakeWidgetRow(id="w-1", name="Bot A")
    w2 = FakeWidgetRow(id="w-2", name="Bot B")
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([w1, w2]),  # SELECT Widget
            FakeResult(),  # COUNT kb_access
        ],
    )

    result = await list_widgets(
        perms=make_perms(role="admin", user_id="user-1", org_id=1),
        db=db,
    )

    assert len(result) == 2
    assert result[0].name == "Bot A"
    assert result[1].name == "Bot B"


@pytest.mark.asyncio
async def test_update_widget_patches_config():
    """PATCH /api/admin/widgets/{id} updates widget_config."""
    from app.api.admin_widgets import UpdateWidgetRequest, WidgetConfig, update_widget

    widget = FakeWidgetRow()
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([widget]),  # SELECT widget
        ],
    )

    body = UpdateWidgetRequest(
        widget_config=WidgetConfig(
            allowed_origins=["https://new.example.com"],
            title="Updated",
            welcome_message="Hello!",
        ),
        public_share_enabled=True,
    )

    with patch("app.api.admin_widgets.emit_event"):
        result = await update_widget(
            widget_id="widget-uuid-1",
            body=body,
            perms=make_perms(role="admin", user_id="user-1", org_id=1),
            db=db,
        )

    assert result.widget_config.title == "Updated"
    assert result.widget_config.allowed_origins == ["https://new.example.com"]
    assert result.public_share_enabled is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_hubspot_status_reports_not_configured_for_platform_org():
    """GET integration status is available but reports server config state."""
    from app.api.admin_widgets import get_hubspot_integration_status

    widget = FakeWidgetRow()
    db = AsyncMock()
    setup_db(db, [FakeResult([widget])])

    result = await get_hubspot_integration_status(
        widget_id="widget-uuid-1",
        perms=make_perms(
            role="admin",
            user_id="user-1",
            org_id=1,
            org_slug="getklai",
            platform_unlocked_features=["widgets"],
        ),
        db=db,
    )

    assert result.status == "not_configured"
    assert result.configured is False


@pytest.mark.asyncio
async def test_hubspot_connect_persists_channel_account():
    """POST connect creates/reuses a HubSpot channel account and stores IDs."""
    from app.api.admin_widgets import connect_hubspot_integration
    from app.services.hubspot_custom_channel import HubSpotChannelAccount

    widget = FakeWidgetRow()
    db = AsyncMock()
    setup_db(db, [FakeResult([widget])])
    account = HubSpotChannelAccount(
        id="3307400689",
        channel_id="2930388",
        inbox_id="1364799639",
        name="Klai Webchat Support",
        active=True,
        authorized=True,
        archived=False,
    )

    with (
        patch("app.api.admin_widgets.ensure_channel_account", AsyncMock(return_value=account)),
        patch("app.api.admin_widgets.hubspot_webchat_configured", return_value=True),
        patch("app.api.admin_widgets.emit_event"),
    ):
        result = await connect_hubspot_integration(
            widget_id="widget-uuid-1",
            perms=make_perms(
                role="admin",
                user_id="user-1",
                org_id=1,
                org_slug="getklai",
                platform_unlocked_features=["widgets"],
            ),
            db=db,
        )

    assert result.status == "connected"
    assert result.channel_account_id == "3307400689"
    assert widget.widget_config["integrations"]["hubspot"]["status"] == "connected"
    assert widget.widget_config["integrations"]["hubspot"]["channel_account_id"] == "3307400689"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_widget_soft_deletes_widget_keeps_audit_trail():
    """REQ-16 (Finding B-14): DELETE /api/admin/widgets/{id} soft-deletes the
    widget (sets deleted_at) and revokes kb_access, but does NOT physically
    DELETE the widget row so the conversation/messages audit trail survives.
    """
    from app.api.admin_widgets import delete_widget

    widget = FakeWidgetRow()
    assert widget.deleted_at is None  # precondition
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([widget]),  # SELECT widget
            FakeResult(),  # DELETE kb_access
        ],
    )

    with patch("app.api.admin_widgets.emit_event"):
        await delete_widget(
            widget_id="widget-uuid-1",
            perms=make_perms(role="admin", user_id="user-1", org_id=1, platform_unlocked_features=["widgets"]),
            db=db,
        )

    db.commit.assert_awaited_once()
    # Only TWO executes: SELECT widget + DELETE kb_access. The widget row
    # itself is updated via attribute mutation (widget.deleted_at = NOW()),
    # not via a DELETE statement.
    assert db.execute.await_count == 2
    assert widget.deleted_at is not None, "Soft-delete must set deleted_at"
