"""Tests for REQ-2 (Finding B-2): create_widget auto-fill behavior.

SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2.

AC-tested:
- When POST /api/admin/widgets omits allowed_origins (empty list) AND allow_any_origin=False,
  the backend auto-fills allowed_origins with the tenant subdomain
  ["https://<slug>.getklai.com"] so the widget is not blocked for all traffic
  on first use (default-deny + auto-fill).
- When POST /api/admin/widgets includes allow_any_origin=True, the backend does NOT
  auto-fill allowed_origins (the open-world flag supersedes the gate entirely).

@MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import make_perms
from helpers import FakeResult, setup_db

# ---------------------------------------------------------------------------
# Shared fake row used across both tests
# ---------------------------------------------------------------------------


@dataclass
class FakeWidgetRowCreate:
    id: str = "widget-uuid-new"
    org_id: int = 1
    name: str = "Test Widget"
    description: str | None = None
    widget_id: str = "wgt_testcreate"
    widget_config: dict = field(default_factory=dict)
    public_share_enabled: bool = False
    allow_any_origin: bool = False
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "user-1"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_widget_without_origins_autofills_tenant_subdomain():
    """POST /api/admin/widgets without allowed_origins AND allow_any_origin=False
    → backend fills allowed_origins=['https://voys.getklai.com'] (tenant subdomain).

    REQ-2: empty allowed_origins must not default to open-world. The auto-fill
    prevents the widget from silently denying all traffic on first use while
    staying locked to the org's own domain.
    """
    from app.api.admin_widgets import CreateWidgetRequest, WidgetConfig, create_widget

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    captured_widget: list = []

    async def fake_refresh(row):
        row.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        # Capture what was added to the DB so we can inspect widget_config.
        captured_widget.append(row)

    db.refresh = AsyncMock(side_effect=fake_refresh)
    # KB lookup for response (no KB IDs → empty result)
    setup_db(db, [FakeResult()])

    body = CreateWidgetRequest(
        name="Test Widget",
        kb_ids=[],
        allow_any_origin=False,
        # No widget_config.allowed_origins — defaults to empty list
        widget_config=WidgetConfig(),
    )

    perms = make_perms(role="admin", user_id="user-1", org_id=1, org_slug="voys")

    with patch("app.api.admin_widgets.emit_event"):
        result = await create_widget(body=body, perms=perms, db=db)

    # The returned widget_config must have the tenant subdomain auto-filled.
    assert result.widget_config.allowed_origins == ["https://voys.getklai.com"], (
        f"Expected ['https://voys.getklai.com'] but got {result.widget_config.allowed_origins}"
    )
    assert result.allow_any_origin is False


@pytest.mark.asyncio
async def test_create_widget_with_allow_any_origin_skips_autofill():
    """POST /api/admin/widgets with allow_any_origin=True → backend does NOT
    auto-fill allowed_origins (open-world flag supersedes the origin gate).

    REQ-2: allow_any_origin=True is an explicit opt-in for public chatbots.
    The allowed_origins list is irrelevant in this mode.
    """
    from app.api.admin_widgets import CreateWidgetRequest, WidgetConfig, create_widget

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    async def fake_refresh(row):
        row.created_at = datetime(2026, 1, 1, tzinfo=UTC)

    db.refresh = AsyncMock(side_effect=fake_refresh)
    setup_db(db, [FakeResult()])

    body = CreateWidgetRequest(
        name="Public Widget",
        kb_ids=[],
        allow_any_origin=True,
        # Explicit empty origins — should stay empty when allow_any_origin=True
        widget_config=WidgetConfig(allowed_origins=[]),
    )

    perms = make_perms(role="admin", user_id="user-1", org_id=1, org_slug="voys")

    with patch("app.api.admin_widgets.emit_event"):
        result = await create_widget(body=body, perms=perms, db=db)

    # allow_any_origin=True → no auto-fill, allowed_origins stays empty.
    assert result.widget_config.allowed_origins == [], (
        f"Expected [] (no auto-fill) but got {result.widget_config.allowed_origins}"
    )
    assert result.allow_any_origin is True
