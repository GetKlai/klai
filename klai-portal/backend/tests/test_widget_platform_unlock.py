"""Tests for platform-unlock gate on public widget endpoints.

REQ-1 (Finding B-1, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001):
- GET /partner/v1/widget-config must 404 when 'widgets' not in enabled_addons
- GET /partner/v1/public-bot-config must 404 when 'widgets' not in enabled_addons
- POST /partner/v1/chat/completions must 403 when 'widgets' not in enabled_addons

AC1.2, AC1.3, AC1.4 from acceptance.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

from app.api.partner import public_bot_config, widget_config
from app.services.widget_auth import session_token_key_id

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeWidget:
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
            "system_prompt": "Use a friendly support tone.",
            "css_variables": {},
        }
    )
    public_share_enabled: bool = True
    allow_any_origin: bool = False
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "test-user"


@dataclass
class FakeOrgUnlocked:
    """Org with 'widgets' in platform_unlocked_features."""

    id: int = 42
    zitadel_org_id: str = "zitadel-org-123"
    slug: str = "test"
    platform_unlocked_features: list = field(default_factory=lambda: ["widgets"])


@dataclass
class FakeOrgLocked:
    """Org WITHOUT 'widgets' in platform_unlocked_features."""

    id: int = 42
    zitadel_org_id: str = "zitadel-org-123"
    slug: str = "test"
    platform_unlocked_features: list = field(default_factory=list)


def _make_request(origin: str = "https://example.com") -> MagicMock:
    request = MagicMock()
    request.headers = {"origin": origin}
    return request


def _make_db(widget: FakeWidget | None, org: object | None, kb_ids: list[int]) -> AsyncMock:
    """Build AsyncMock db: widget → org → kb_access in sequence."""
    db = AsyncMock()
    db.add = MagicMock()

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


# ---------------------------------------------------------------------------
# REQ-1 tests — widget_config endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_config_returns_404_when_widgets_not_unlocked():
    """AC1.2 — widget_config must return 404 when 'widgets' not in org's enabled features.

    Existence-non-disclosure: do not leak that the widget exists for a locked tenant.
    """
    widget = FakeWidget()
    org = FakeOrgLocked()
    db = _make_db(widget, org, [1])
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        with pytest.raises(HTTPException) as exc_info:
            await widget_config(id=widget.widget_id, request=request, db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_widget_config_returns_200_when_widgets_unlocked():
    """AC1.1 — widget_config must still return 200 when 'widgets' IS unlocked."""
    widget = FakeWidget()
    org = FakeOrgUnlocked()
    db = _make_db(widget, org, [1])
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await widget_config(id=widget.widget_id, request=request, db=db)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# REQ-1 tests — public_bot_config endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_bot_config_returns_404_when_widgets_not_unlocked():
    """AC1.3 — public_bot_config must 404 when 'widgets' not in org's enabled features."""
    widget = FakeWidget()
    org = FakeOrgLocked()
    db = _make_db(widget, org, [1])

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        with pytest.raises(HTTPException) as exc_info:
            await public_bot_config(id=widget.widget_id, db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_public_bot_config_returns_200_when_widgets_unlocked():
    """Happy path — public_bot_config 200 when 'widgets' IS unlocked."""
    widget = FakeWidget()
    org = FakeOrgUnlocked()
    db = _make_db(widget, org, [1])

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool"),
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)),
        patch("app.api.partner.set_tenant", new=AsyncMock()),
        patch("app.api.partner.generate_session_token", return_value="fake.jwt.token"),
    ):
        mock_settings.widget_jwt_secret = "shared-secret"

        response = await public_bot_config(id=widget.widget_id, db=db)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# REQ-1 tests — chat-completions path via _auth_via_session_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_completion_returns_403_when_widgets_not_unlocked():
    """AC1.4 — _auth_via_session_token must raise 403 when 'widgets' not unlocked.

    The JWT already identifies the widget so existence-non-disclosure does not apply.
    A 403 tells the user they can't chat, which is the honest response.

    The platform-unlock check runs after org is loaded and after the JWT is
    verified, so forged/invalid tokens remain opaque 401s.
    """
    from app.api.partner_dependencies import _auth_via_session_token

    org = FakeOrgLocked()

    db = AsyncMock()
    db.add = MagicMock()

    # db.execute: first call for org lookup
    org_result = MagicMock()
    org_result.scalar_one_or_none = MagicMock(return_value=org)
    db.execute = AsyncMock(return_value=org_result)

    with (
        patch("app.api.partner_dependencies.settings") as mock_settings,
        patch("app.api.partner_dependencies.set_tenant", new=AsyncMock()),
        patch("app.api.partner_dependencies.decode_session_token") as mock_decode,
    ):
        mock_settings.widget_jwt_secret = "shared-secret"
        # decode_session_token returns a valid payload — platform check should fire after verification.
        mock_decode.return_value = {
            "org_id": 42,
            "wgt_id": "wgt_test",
            "kb_ids": [1],
        }

        # The function should raise 403 due to platform-unlock check
        with pytest.raises(HTTPException) as exc_info:
            token = jwt.encode(
                {"org_id": 42, "wgt_id": "wgt_test", "kb_ids": [1]},
                "test-widget-secret-at-least-32-bytes",
                headers={"kid": session_token_key_id(42), "typ": "JWT"},
            )
            await _auth_via_session_token(token, db)

    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail.get("error_code") == "feature_not_unlocked"
    assert detail.get("feature") == "widgets"
