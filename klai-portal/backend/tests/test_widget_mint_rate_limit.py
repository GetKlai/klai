"""RED: Per-widget mint rate-limit on /partner/v1/widget-config and /public-bot-config.

REQ-7 (Finding B-4, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001):
- widget-config must call check_rate_limit before DB lookup
- public-bot-config must call check_rate_limit before DB lookup
- 429 is returned with Retry-After header when limit exceeded

AC7.1, AC7.2, AC7.3 from acceptance.md.

# @MX:NOTE: [AUTO] Tests patch app.services.partner_rate_limit.check_rate_limit
# because partner.py imports the function directly from that module.
# @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-7
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fakes (mirrors test_widget_platform_unlock.py)
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
            "css_variables": {},
        }
    )
    public_share_enabled: bool = True
    allow_any_origin: bool = True  # allow any origin so origin check doesn't interfere
    rate_limit_rpm: int = 60
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    created_by: str = "test-user"


@dataclass
class FakeOrg:
    id: int = 42
    zitadel_org_id: str = "zitadel-org-123"
    slug: str = "test"
    platform_unlocked_features: list = field(default_factory=lambda: ["widgets"])
    enabled_addons: list = field(default_factory=list)


def _make_db_mock(widget_row=None, org_row=None):
    """Build a mock AsyncSession that returns the given rows in sequence."""
    db = AsyncMock()
    db.add = MagicMock()

    widget_result = MagicMock()
    widget_result.scalar_one_or_none.return_value = widget_row or FakeWidget()

    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = org_row or FakeOrg()

    kb_result = MagicMock()
    kb_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[widget_result, org_result, kb_result])
    return db


def _make_request(origin: str = "https://example.com"):
    req = MagicMock()
    req.headers = {"origin": origin}
    return req


# ---------------------------------------------------------------------------
# AC7.2: 429 returned on widget-config when rate limit exceeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_config_429_after_10_calls_per_minute():
    """widget-config returns 429 with Retry-After when rate limit exceeded (AC7.2)."""
    from app.api.partner import widget_config

    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        # Simulate rate limit exceeded: allowed=False, retry_after=45
        mock_rl.return_value = (False, 45)

        response = await widget_config(id="wgt_abcdef1234567890abcdef1234567890abcdef12", request=request, db=db)

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.headers["Retry-After"] == "45"


@pytest.mark.asyncio
async def test_public_bot_config_429_after_10_calls_per_minute():
    """public-bot-config returns 429 with Retry-After when rate limit exceeded (AC7.2)."""
    from app.api.partner import public_bot_config

    db = _make_db_mock()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        # Simulate rate limit exceeded
        mock_rl.return_value = (False, 30)

        response = await public_bot_config(id="wgt_abcdef1234567890abcdef1234567890abcdef12", db=db)

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.headers["Retry-After"] == "30"


@pytest.mark.asyncio
async def test_429_includes_retry_after_header():
    """429 response always includes Retry-After header with positive seconds (AC7.2)."""
    from app.api.partner import widget_config

    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_rl.return_value = (False, 60)

        response = await widget_config(id="wgt_abcdef1234567890abcdef1234567890abcdef12", request=request, db=db)

    assert response.status_code == 429
    retry_after = int(response.headers["Retry-After"])
    assert retry_after > 0


# ---------------------------------------------------------------------------
# AC7.1: 200 returned when within limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widget_config_200_within_limit():
    """widget-config returns 200 when rate limit is not exceeded (AC7.1)."""
    from app.api.partner import widget_config

    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
        patch("app.api.partner.assert_platform_unlocked"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_rl.return_value = (True, 0)

        response = await widget_config(id="wgt_abcdef1234567890abcdef1234567890abcdef12", request=request, db=db)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# AC7.3: Separate widgets have independent rate limit keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_key_uses_widget_id():
    """check_rate_limit is called with a key scoped to the widget_id (AC7.3).

    The key must be f'widget_mint:{widget_id}' so each widget is isolated.
    """
    from app.api.partner import widget_config

    widget_id = "wgt_abcdef1234567890abcdef1234567890abcdef12"
    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
        patch("app.api.partner.assert_platform_unlocked"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_rl.return_value = (True, 0)

        await widget_config(id=widget_id, request=request, db=db)

    mock_rl.assert_called_once()
    call_args = mock_rl.call_args[0]  # positional args
    key_arg = call_args[1]  # second positional: key_id
    assert key_arg == f"widget_mint:{widget_id}"


@pytest.mark.asyncio
async def test_rate_limit_uses_limit_10_per_minute():
    """check_rate_limit is called with limit_per_minute=10 (REQ-7 spec)."""
    from app.api.partner import widget_config

    widget_id = "wgt_abcdef1234567890abcdef1234567890abcdef12"
    db = _make_db_mock()
    request = _make_request()

    with (
        patch("app.api.partner.settings") as mock_settings,
        patch("app.api.partner.get_redis_pool") as mock_get_redis,
        patch("app.api.partner.check_rate_limit", new_callable=AsyncMock) as mock_rl,
        patch("app.api.partner.set_tenant", new_callable=AsyncMock),
        patch("app.api.partner.generate_session_token", return_value="tok"),
        patch("app.api.partner.assert_platform_unlocked"),
    ):
        mock_settings.widget_jwt_secret = "test-secret"
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_rl.return_value = (True, 0)

        await widget_config(id=widget_id, request=request, db=db)

    mock_rl.assert_called_once()
    call_kwargs = mock_rl.call_args[1]  # keyword args
    assert call_kwargs.get("limit_per_minute") == 10
