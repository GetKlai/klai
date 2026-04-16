"""Tests for widget-config endpoint — SPEC-WIDGET-002.

Covers the core flow:
- GET /partner/v1/widget-config with valid wgt_id + allowed origin → 200 + JWT
- Unknown wgt_id → 404
- Disallowed origin → 403
- Empty allowed_origins list → 403 (fail-closed)
- Missing JWT secret in settings → 503
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.partner import widget_config


@dataclass
class FakeWidget:
    """Simulates a Widget row."""

    id: str = "widget-uuid-1"
    org_id: int = 42
    name: str = "Test widget"
    description: str | None = None
    widget_id: str = "wgt_abcdef1234567890abcdef1234567890abcdef12"
    widget_config: dict = field(
        default_factory=lambda: {
            "allowed_origins": ["https://example.com"],
            "title": "Chat",
            "welcome_message": "Hello!",
            "css_variables": {},
        }
    )
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "test-user"


@dataclass
class FakeOrg:
    id: int = 42
    zitadel_org_id: str = "zitadel-org-123"


def _make_request(origin: str | None = "https://example.com") -> MagicMock:
    request = MagicMock()
    request.headers = {"origin": origin} if origin else {}
    return request


def _make_db_chain(widget: FakeWidget | None, org: FakeOrg | None, kb_ids: list[int]) -> AsyncMock:
    """Build an AsyncMock db that returns widget, org, then kb_access rows in sequence."""
    db = AsyncMock()

    widget_result = MagicMock()
    widget_result.scalar_one_or_none = MagicMock(return_value=widget)

    org_result = MagicMock()
    org_result.scalar_one_or_none = MagicMock(return_value=org)

    kb_result = MagicMock()
    kb_scalars = MagicMock()
    kb_rows = [MagicMock(kb_id=kb_id) for kb_id in kb_ids]
    kb_scalars.all = MagicMock(return_value=kb_rows)
    kb_result.scalars = MagicMock(return_value=kb_scalars)

    db.execute = AsyncMock(side_effect=[widget_result, org_result, kb_result])
    return db


@pytest.mark.asyncio
async def test_widget_config_happy_path():
    """Valid wgt_id + allowed origin returns 200 with session token."""
    widget = FakeWidget()
    org = FakeOrg()
    db = _make_db_chain(widget, org, [1, 2])
    request = _make_request("https://example.com")

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 200
    body = response.body.decode()
    assert '"session_token": "fake.jwt.token"' in body
    assert '"title": "Chat"' in body


@pytest.mark.asyncio
async def test_widget_config_missing_jwt_secret():
    """503 when WIDGET_JWT_SECRET is not configured."""
    db = AsyncMock()
    request = _make_request()

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = ""
        response = await widget_config(id="wgt_any", request=request, db=db)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_widget_config_unknown_widget_id():
    """404 when the wgt_id does not exist in widgets table."""
    db = _make_db_chain(None, None, [])
    request = _make_request()

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = "shared-secret"
        with pytest.raises(Exception) as exc_info:
            await widget_config(id="wgt_does_not_exist", request=request, db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_widget_config_disallowed_origin():
    """403 when the Origin header is not in allowed_origins."""
    widget = FakeWidget()
    db = _make_db_chain(widget, None, [])
    request = _make_request("https://evil.example.com")

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = "shared-secret"
        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_widget_config_empty_allowed_origins_fail_closed():
    """403 when allowed_origins is an empty list (fail-closed)."""
    widget = FakeWidget(
        widget_config={
            "allowed_origins": [],
            "title": "",
            "welcome_message": "",
            "css_variables": {},
        },
    )
    db = _make_db_chain(widget, None, [])
    request = _make_request("https://example.com")

    with patch("app.api.partner.settings") as mock_settings:
        mock_settings.widget_jwt_secret = "shared-secret"
        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 403
