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
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "user-1"


def _mock_auth():
    return (
        patch(
            "app.api.admin_widgets._get_caller_org",
            new=AsyncMock(return_value=("user-1", FakeOrg(), FakeUser())),
        ),
        patch("app.api.admin_widgets._require_admin"),
    )


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

    with _mock_auth()[0], _mock_auth()[1], patch("app.api.admin_widgets.emit_event"):
        result = await create_widget(
            body=body,
            credentials=MagicMock(credentials="fake-token"),
            db=db,
        )

    assert result.widget_id.startswith("wgt_")
    assert not hasattr(result, "api_key")
    assert result.name == "Help Bot"
    assert result.widget_config.title == "Help"
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

    with _mock_auth()[0], _mock_auth()[1]:
        result = await list_widgets(
            credentials=MagicMock(credentials="fake-token"),
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
    )

    with _mock_auth()[0], _mock_auth()[1], patch("app.api.admin_widgets.emit_event"):
        result = await update_widget(
            widget_id="widget-uuid-1",
            body=body,
            credentials=MagicMock(credentials="fake-token"),
            db=db,
        )

    assert result.widget_config.title == "Updated"
    assert result.widget_config.allowed_origins == ["https://new.example.com"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_widget_calls_db_delete():
    """DELETE /api/admin/widgets/{id} executes DELETE on DB."""
    from app.api.admin_widgets import delete_widget

    widget = FakeWidgetRow()
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([widget]),  # SELECT widget
            FakeResult(),  # DELETE kb_access
            FakeResult(),  # DELETE widget
        ],
    )

    with _mock_auth()[0], _mock_auth()[1], patch("app.api.admin_widgets.emit_event"):
        await delete_widget(
            widget_id="widget-uuid-1",
            credentials=MagicMock(credentials="fake-token"),
            db=db,
        )

    db.commit.assert_awaited_once()
    assert db.execute.await_count == 3
